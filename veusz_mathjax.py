"""veusz plugin: MathJax TeX rendering for *any* veusz, including stock releases.

Part of veusz-mathjax-plugin.  Copyright (C) 2026 Yu Zhai.
Licensed under the Apache License, Version 2.0; see LICENSE.

It carries the whole feature -- settings, renderer and widget wiring -- so it
works on an unmodified veusz (no TeX option to begin with, no engine dropdown,
no TeX-enabled build required).

How it works, and why each piece is needed:

  1. ``collections.Text.__init__`` is wrapped to add two booleans to the text
     settings of every text-bearing widget -- ``mathjax`` ("MathJax") and
     ``mathjaxDisplay`` ("Display style") -- plus a hidden ``useTeX`` that
     forwards to ``mathjax``, so documents and scripts written with the older
     name keep working.  The properties panel is generated from the settings
     tree, so the checkboxes appear by themselves.
  2. ``Widget.draw`` is wrapped for every registered widget class to publish
     "the text settings of the widget currently being painted" in a context
     variable.  No widget internals are touched.
  3. ``makeQFont`` of the settings class is wrapped, because every widget builds
     the font of a text element from that element's own settings group just
     before drawing it (``s.get('TickLabels')`` for tick numbers,
     ``s.get('Label')`` for an axis label, ``s.get('Text')`` for a label or a
     key).  Which group made the font is what decides *per text element*
     whether MathJax is used: an axis label's setting must not drag its tick
     numbers along.
  4. ``veusz.utils.Renderer`` (the single choke point every widget paints text
     through) is wrapped: for text whose settings group asked for MathJax it
     returns a renderer that draws the MathJax SVG, otherwise the original
     renderer is used.
  5. The MathJax SVG is painted with Qt's own SVG renderer, and the baseline
     (MathJax's ``vertical-align``) is honoured so MathJax labels line up with
     plain text.  Anything missing (no DLL, no bundle, a MathJax error) falls
     back to the original text renderer instead of breaking the plot.

Data files, all in a ``data/`` directory next to this file:

  ``qjs.dll`` (or ``libqjs.so`` / ``libqjs.dylib``)
      the JavaScript engine, built unmodified from quickjs-ng (MIT)
  ``mathjaxbridge.dll`` (or ``.so`` / ``.dylib``)
      the JS host; it imports the engine above, so the two are shipped as
      separate binaries
  ``mathjax_bundle.js``
      MathJax 4 plus its fonts (Apache-2.0)

``VEUSZ_JSENGINES_BRIDGE`` / ``VEUSZ_JSENGINES_BUNDLE`` /
``VEUSZ_JSENGINES_QUICKJS`` override the paths.
"""

import ctypes
import html
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# --------------------------------------------------------------------------
# data discovery
# --------------------------------------------------------------------------

def _plugin_path():
    """Path of the plugin file that is running.

    veusz loads plugin files with exec(f.read(), {}), so there is no __file__;
    the caller's frame (Document.loadPlugins) holds the path in ``plugin``.
    """
    try:
        frame = sys._getframe(1)
        while frame is not None:
            candidate = frame.f_locals.get('plugin')
            if isinstance(candidate, str) and candidate.endswith('.py'):
                return Path(candidate).resolve()
            frame = frame.f_back
    except Exception:
        pass
    try:
        return Path(__file__).resolve()
    except Exception:
        return None


def _plugin_dir():
    """Directory this plugin file lives in."""
    path = _plugin_path()
    return path.parent if path is not None else None


def _first_existing(candidates):
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def _find_bridge(here):
    names = ('mathjaxbridge.dll', 'libmathjaxbridge.dll',
             'libmathjaxbridge.so', 'libmathjaxbridge.dylib')
    cands = [os.environ.get('VEUSZ_JSENGINES_BRIDGE')]
    if here:
        cands += [here / 'data' / n for n in names]
        cands += [here / n for n in names]
        cands += [here.parent / 'data' / n for n in names]      # shared data/
    return _first_existing(cands)


def _find_bundle(here):
    """The bundle to load first, and the one whose fonts are the default.

    The usual name wins if it is there.  Otherwise the first *.js in the data
    directory that says what it carries -- so a data/ holding nothing but a font
    bundle someone dropped in works as it is, with nothing renamed and no list
    to keep in step.  (see _discover_fonts for the rest of the directory).
    """
    env = os.environ.get('VEUSZ_JSENGINES_BUNDLE')
    if env and Path(env).exists():
        return Path(env)
    if not here:
        return None
    found = _first_existing([here / 'data' / 'mathjax_bundle.js',
                             here / 'mathjax_bundle.js',
                             here.parent / 'data' / 'mathjax_bundle.js'])
    if found is not None:
        return found
    for directory in (here / 'data', here, here.parent / 'data'):
        if not directory.is_dir():
            continue
        declared = sorted(p for p in directory.glob('*.js')
                          if _declared_fonts(p, sidecar=False) is not None)
        if declared:
            return declared[0]
    return None


_QUICKJS_NAMES = ('qjs.dll', 'libqjs.so', 'libqjs.dylib',
                  'libqjs.so.0', 'quickjs.dll', 'libquickjs.so')


def _find_quickjs(bridge):
    """Locate the engine the bridge imports.

    The bridge is linked against QuickJS, not against a copy of it, so the
    engine is its own file.  Windows resolves that import from the directory
    of the bridge itself (measured: neither the current directory nor PATH is
    consulted), so putting the two files side by side -- which is what the
    release zip does -- is all that is needed.  This lookup exists for the
    case where the bridge was pointed at from elsewhere with
    VEUSZ_JSENGINES_BRIDGE, and to produce a readable error when the engine is
    simply missing.
    """
    env = os.environ.get('VEUSZ_JSENGINES_QUICKJS')
    cands = [env] if env else []
    if bridge:
        cands += [bridge.parent / n for n in _QUICKJS_NAMES]
    return _first_existing(cands)


def _load_quickjs(bridge):
    """Load the engine before the bridge, by absolute path.  Returns the
    library or None (the bridge then fails with its own message)."""
    path = _find_quickjs(bridge)
    if path is None:
        return None
    try:
        return ctypes.CDLL(str(path))
    except OSError:
        return None


def _declared_fonts(bundle, sidecar=True):
    """What a single file says it carries: (fonts, default, kind) or None.

    Every file the build writes describes itself in a one-line
    ``// MATHJAX-FONT {...}`` header, which this reads without running it -- so a
    file dropped into data/ can be offered in the chooser before anything is
    loaded.  ``kind`` says what it is: ``bundle`` holds MathJax and its fonts,
    ``data`` holds one font's data and nothing else, to be registered into the
    bundle the plugin already runs (that is what keeps one core for every font).
    The older form, a fonts.json written beside the bundle, is still read for
    bundles built before this header existed; it means a bundle.
    """
    try:
        with open(bundle, 'r', encoding='utf-8', errors='replace') as handle:
            head = handle.read(16384)
    except Exception:
        head = ''
    m = re.search(r'^// MATHJAX-FONT (\{.*\})\s*$', head, re.M)
    if m:
        try:
            data = json.loads(m.group(1))
        except Exception:
            data = None
        if data:
            fonts = [f for f in data.get('fonts', []) if f.get('id')]
            if fonts:
                return fonts, data.get('default'), data.get('kind') or 'bundle'
    if sidecar:
        path = Path(bundle).parent / 'fonts.json'
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                return None
            fonts = [f for f in data.get('fonts', []) if f.get('id')]
            if fonts:
                return fonts, data.get('default'), 'bundle'
    return None


def _load_fonts(bundle):
    """Which fonts this file carries: (list of dicts, default id)."""
    env = os.environ.get('VEUSZ_JSENGINES_FONTS')
    found = _declared_fonts(bundle, sidecar=True) if not env else None
    if env and Path(env).exists():
        try:
            data = json.loads(Path(env).read_text(encoding='utf-8'))
            fonts = [f for f in data.get('fonts', []) if f.get('id')]
            found = ((fonts, data.get('default'), 'bundle') if fonts else None)
        except Exception:
            found = None
    if found:
        fonts, default = found[0], found[1]
        if default not in [f['id'] for f in fonts]:
            default = fonts[0]['id']
        return fonts, default
    return [{'id': '', 'title': 'default', 'x_height': None}], ''


def _discover_fonts(here, bundle):
    """Every font the plugin's data directory offers, and the default id.

    The bundle the plugin was pointed at is always there.  Beyond it, any other
    ``*.js`` beside it that declares itself is taken as well, each carrying the
    file it came from -- so a font bundle dropped into ``data/`` shows up in the
    chooser after a restart, with nothing to keep in step by hand.  A bundle
    that declares nothing is only ever loaded as the configured default.
    """
    fonts = []
    default_id = ''
    seen = set()
    candidates = [Path(bundle)]
    if here:
        data_dir = Path(bundle).parent
        try:
            candidates += sorted(p for p in data_dir.glob('*.js')
                                 if p != Path(bundle))
        except Exception:
            pass
    for index, path in enumerate(candidates):
        try:
            is_default = Path(path) == Path(bundle)
        except Exception:
            is_default = False
        declared = _declared_fonts(path, sidecar=is_default)
        if declared is None:
            if not is_default:
                continue
            declared = ([{'id': '', 'title': 'default', 'x_height': None}],
                        '', 'bundle')
        bundle_fonts, bundle_default, bundle_kind = declared
        for font in bundle_fonts:
            font_id = font.get('id') or ''
            if font_id in seen:
                continue
            seen.add(font_id)
            entry = dict(font)
            entry['bundle'] = str(path)
            entry['kind'] = bundle_kind
            fonts.append(entry)
            if is_default and font_id == (bundle_default or bundle_fonts[0]['id']):
                default_id = font_id
    if not fonts:
        return [{'id': '', 'title': 'default', 'x_height': None,
                 'bundle': str(bundle), 'kind': 'bundle'}], ''
    if default_id not in [f['id'] for f in fonts]:
        default_id = fonts[0]['id']
    return fonts, default_id


class _HostSet(object):
    """The JS hosts, one per bundle, created when a font is first used.

    A dropped-in bundle is a whole MathJax (the core plus its font), so a font
    nobody selects costs nothing: no host, no memory, no parse.  A bundle that
    carries several fonts -- the released packages do -- is switched with the
    font id, which is what JsHost.render() already takes.
    """

    def __init__(self, bridge, fonts, default_font, core_bundle=None):
        self.bridge = bridge
        self.fonts = fonts
        self.default_font = default_font
        self.core_bundle = core_bundle
        self._by_id = dict((f['id'], f) for f in fonts)
        self._hosts = {}
        self._registered = set()

    def spec(self, font_id):
        return self._by_id.get(font_id) or self._by_id.get(self.default_font)

    def core_host(self):
        """The host holding MathJax itself, which font data files register into."""
        if self.core_bundle is None:
            return None
        host = self._hosts.get(self.core_bundle)
        if host is None:
            host = JsHost(self.bridge, self.core_bundle)
            self._hosts[self.core_bundle] = host
        return host

    def host_for(self, font_id):
        """The host that can draw this font id (falls back to the default)."""
        spec = self.spec(font_id)
        if spec is None:
            return None
        path = spec.get('bundle')
        if (spec.get('kind') or 'bundle') == 'data':
            # a font data file: no MathJax in it, it goes into the core host
            # once, and every font that arrives this way shares that host
            host = self.core_host()
            if host is None:
                return None
            if path not in self._registered:
                host.load_font(path)
                self._registered.add(path)
            host.remember_font(spec.get('id'), spec.get('x_height'))
            return host
        host = self._hosts.get(path)
        if host is None:
            host = JsHost(self.bridge, path)
            self._hosts[path] = host
        host.remember_font(spec.get('id'), spec.get('x_height'))
        return host


# --------------------------------------------------------------------------
# JS host (ctypes; the C ABI of veusz/src/mathjaxbridge)
# --------------------------------------------------------------------------

class JsHost:
    """The bridge, plus which font the bundle is currently rendering with.

    Uses the multi-bundle API (js_host_*) rather than the legacy mathjax_* one,
    because it can call any function the bundle defines -- that is how the font
    is switched (setFont).
    """

    def __init__(self, bridge, bundle):
        self.bridge = Path(bridge)
        self.bundle = Path(bundle)
        self.svg_cache = {}
        self.fonts, self.default_font = _load_fonts(bundle)
        self._x_height = {f['id']: f.get('x_height') for f in self.fonts}
        self.font = None
        self.quickjs = _load_quickjs(self.bridge)
        try:
            lib = ctypes.CDLL(str(self.bridge))
        except OSError as exc:
            raise RuntimeError(
                'cannot load %s: %s%s'
                % (self.bridge, exc,
                   '' if self.quickjs is not None else
                   ' -- and no QuickJS engine (%s) was found next to it'
                   % ' / '.join(_QUICKJS_NAMES[:2])))
        lib.js_host_init.argtypes = [ctypes.c_char_p]
        lib.js_host_init.restype = ctypes.c_int
        lib.js_host_shutdown.argtypes = [ctypes.c_int]
        lib.js_host_shutdown.restype = None
        lib.js_host_render.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_float,
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_void_p)]
        lib.js_host_render.restype = ctypes.c_int
        # js_host_eval runs one more script in this runtime, which is how a font
        # data file adds its font to the MathJax already loaded here.  A bridge
        # older than that has no such symbol; load_font() says so if it is asked.
        if hasattr(lib, 'js_host_eval'):
            lib.js_host_eval.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                         ctypes.POINTER(ctypes.c_void_p)]
            lib.js_host_eval.restype = ctypes.c_int
        lib.mathjax_set_ex_height.argtypes = [ctypes.c_float]
        lib.mathjax_set_ex_height.restype = None
        lib.mathjax_free.argtypes = [ctypes.c_void_p]
        lib.mathjax_free.restype = None
        self.handle = lib.js_host_init(str(self.bundle).encode('utf-8'))
        if self.handle <= 0:
            raise RuntimeError('cannot load the JavaScript bundle %s'
                               % self.bundle)
        self.lib = lib
        self.set_font(self.default_font)

    def load_font(self, path):
        """Register a font data file's font into this host.

        The file carries no MathJax: it reads the base classes this bundle
        publishes (globalThis.__veuszMathjax) and calls registerFont(), so one
        host -- one MathJax -- ends up holding every font added this way.
        """
        if not hasattr(self.lib, 'js_host_eval'):
            raise RuntimeError(
                'this build of mathjaxbridge cannot add fonts at run time; '
                'replace it with one that has js_host_eval')
        err = ctypes.c_void_p()
        rc = self.lib.js_host_eval(self.handle, str(path).encode('utf-8'),
                                   ctypes.byref(err))
        if rc != 0:
            message = ''
            if err.value:
                message = ctypes.cast(err, ctypes.c_char_p).value.decode(
                    'utf-8', 'replace')
                self.lib.mathjax_free(err)
            raise RuntimeError('cannot load the font %s: %s'
                               % (Path(path).name, message or 'rc=%d' % rc))
        return True

    def _call(self, fn_name, arg, size=0.0, display=0, color=None):
        """Call a function the bundle defines; returns (text, (w, h, baseline))."""
        out = ctypes.c_void_p()
        out_len = ctypes.c_size_t()
        w, h, b = (ctypes.c_float(), ctypes.c_float(), ctypes.c_float())
        err = ctypes.c_void_p()
        # postprocess=1: the bridge rewrites the SVG's ex units to points and
        # applies the colour, which is what the renderer expects
        rc = self.lib.js_host_render(
            self.handle, fn_name.encode('utf-8'), str(arg).encode('utf-8'),
            float(size), int(display),
            color.encode('utf-8') if color else None, 1,
            ctypes.byref(out), ctypes.byref(out_len), ctypes.byref(w),
            ctypes.byref(h), ctypes.byref(b), ctypes.byref(err))
        if rc != 0:
            msg = 'mathjaxbridge failed (rc=%d)' % rc
            if err.value:
                msg = ctypes.cast(err, ctypes.c_char_p).value.decode(
                    'utf-8', 'replace')
                self.lib.mathjax_free(err)
            raise RuntimeError(msg)
        try:
            data = ctypes.string_at(out.value, out_len.value) if out.value \
                else b''
        finally:
            if out.value:
                self.lib.mathjax_free(out)
        return data, (w.value, h.value, b.value)

    def remember_font(self, font_id, x_height=None):
        """Let this host render a font it did not declare itself.

        A font data file registers its font into the core at run time, so the
        core bundle's own list of fonts does not mention it.  Without this the
        plugin took the id for unknown and rendered with the default font
        instead -- a registered font drew the core's glyphs, silently.
        """
        if not font_id:
            return
        self._x_height[font_id] = x_height

    def set_font(self, font):
        """Switch the bundle to another font, and tell the bridge its x-height.

        1ex = size * x_height decides how MathJax's geometry becomes points, so
        leaving the previous font's value in place would render the new one up
        to 19% off (measured: tex 0.442, dejavu 0.519, fira 0.527).
        """
        font = font if font in self._x_height else self.default_font
        if font == self.font:
            return
        if self.font is None or font:
            self._call('setFont', font)
        xh = self._x_height.get(font)
        if xh:
            self.lib.mathjax_set_ex_height(float(xh))
        self.font = font

    def render(self, tex, text_size, color=None, display=False, font=None):
        """Return (svg_bytes, width_pt, height_pt, baseline_pt).

        ``display`` picks MathJax's display style -- larger fractions, limits
        above and below the operator -- instead of the inline style, which is
        what text in a paragraph looks like.  ``font`` is the id of the font in
        fonts.json to render with; empty or unknown means the bundle's default.
        """
        # Resolve to a concrete font first: an empty setting means "the
        # default", and it must switch *back* to the default rather than keep
        # whatever the last text used (that made a freshly ticked label show one
        # font while the chooser showed another).
        target = font if font in self._x_height else self.default_font
        if target != self.font:
            self.set_font(target)
        key = (tex, float(text_size), color or '', bool(display), self.font)
        hit = self.svg_cache.get(key)
        if hit is not None:
            return hit
        svg, (w, h, b) = self._call(
            'render' if display else 'renderInline', tex,
            size=text_size, display=1 if display else 0, color=color)
        if not svg:
            raise RuntimeError('empty SVG')
        result = (svg, w, h, b)
        if len(self.svg_cache) > 256:
            self.svg_cache.clear()
        self.svg_cache[key] = result
        return result


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

def build_renderer_class(textrender, qt, hosts):
    """Create the MathJax renderer class (needs the veusz modules to subclass)."""

    # -- SVG <text> --------------------------------------------------------
    #
    # MathJax draws every character its bundled math font has as a <path>, and
    # emits a <text> element for the ones it does not have (CJK, a rare symbol,
    # an emoji, whenever the chooser's font lacks them).  Text is the one thing
    # in the SVG that is not a path, and it does not survive Veusz's painting:
    # every widget is recorded onto a device and replayed, and Qt sizes SVG text
    # against the paint device's resolution rather than in the SVG's own units
    # (so it comes out dpi/72 times too large: measured x1.33 on a 96dpi screen,
    # x4.17 in a 300dpi export, against x1.00 for the paths), and it is replayed
    # as a hairline outline rather than the filled glyph that was drawn.
    #
    # So convert it here, once, into the <path> outlines Qt would have used.
    # Measured through Veusz's own recording device, inside the installed Veusz:
    #
    #     <text> straight          216 x  68 px FILLED
    #     <text> via recording     478 x 243 px FILLED  (2.2x too big)
    #     <path> straight          216 x  68 px FILLED
    #     <path> via recording     216 x  68 px FILLED  <- what we want
    #
    # and the conversion itself is faithful: the same glyphs as <text> and as
    # <path> differ by 1 pixel in 5393 (IoU 1.000).
    #
    # The <text> element's own transform has to be kept, or the glyphs come out
    # flipped: MathJax wraps the formula in scale(1,-1) and each text element in
    # scale(1,-1) again, so text-local coordinates (y down, baseline at the
    # origin, which is also what QPainterPath.addText produces) land upright
    # only through the second flip.
    _TEXT_ELEM_RE = re.compile(rb'<text\b([^>]*)>(.*?)</text>', re.S)
    _ATTR_RE = re.compile(rb'([A-Za-z-]+)\s*=\s*"([^"]*)"')
    _TEXT_SIZE_RE = re.compile(rb'font-size="([0-9.]+)px"')
    # families that name a *kind* of font rather than one: MathJax writes these
    # for characters its math font lacks, and we substitute the text element's
    # own font for them
    _GENERIC_FAMILIES = frozenset((
        '', 'serif', 'sans-serif', 'monospace', 'cursive', 'fantasy',
        'system-ui', 'ui-serif', 'ui-sans-serif', 'ui-monospace',
        'ui-rounded', 'math', 'emoji', 'fangsong'))
    # glyphs are built at this pixel size and scaled down, so the outlines do
    # not depend on the font size asked for
    _OUTLINE_PX = 1000.0
    _converted = {}

    def _svg_path_data(path):
        """A QPainterPath as SVG path data (glyph contours are closed)."""
        parts = []
        move = qt.QPainterPath.ElementType.MoveToElement
        line = qt.QPainterPath.ElementType.LineToElement
        curve = qt.QPainterPath.ElementType.CurveToElement
        count = path.elementCount()
        i = 0
        while i < count:
            element = path.elementAt(i)
            if element.type == move:
                if parts:
                    parts.append('Z')
                parts.append('M%.2f %.2f' % (element.x, element.y))
            elif element.type == line:
                parts.append('L%.2f %.2f' % (element.x, element.y))
            elif element.type == curve:
                one = path.elementAt(i + 1)
                two = path.elementAt(i + 2)
                parts.append('C%.2f %.2f %.2f %.2f %.2f %.2f'
                             % (element.x, element.y, one.x, one.y,
                                two.x, two.y))
                i += 2
            i += 1
        parts.append('Z')
        return ' '.join(parts)

    def _text_to_path_data(text, attrs, label_family, em_units=None):
        """Outline for one <text> element, in the element's own coordinates.

        ``em_units`` is what one em is worth in this SVG's user units.  MathJax
        sizes the text of a character it has no glyph for as ``2 * ex`` of the
        chosen math font, so the same CJK comes out 17% larger under Fira
        (ex/em 0.527) than under Termes (0.441) -- and 12% smaller than a plain
        label of the same size.  Drawing it at one em instead makes those
        characters match the text around them, in every math font.
        """
        family = (attrs.get('font-family') or '').split(',')[0]
        family = family.strip().strip('\'"')
        # MathJax writes a *generic* family (its default is "serif") for the
        # characters its own math font does not have -- it has no idea what the
        # figure is set in.  Draw those in the font this text element is set in
        # instead, so a formula's CJK matches the text around it (and so the
        # Font setting in the formatting panel means something for it).  An
        # explicit family from the engine still wins.
        if family.lower() in _GENERIC_FAMILIES and label_family:
            family = label_family
        size = attrs.get('font-size') or ''
        size = size.strip().rstrip('px')
        try:
            size = float(size)
        except ValueError:
            return None
        # fall back to the size the engine asked for if we cannot work out what
        # an em is here
        if em_units is None or em_units <= 0:
            em_units = size
        if size <= 0 or em_units <= 0 or not text:
            return None

        font = qt.QFont(family) if family else qt.QFont()
        font.setPixelSize(int(_OUTLINE_PX))
        weight = (attrs.get('font-weight') or '').strip().lower()
        if weight in ('bold', 'bolder') or weight.isdigit() and int(weight) >= 600:
            font.setBold(True)
        if (attrs.get('font-style') or '').strip().lower() in ('italic', 'oblique'):
            font.setItalic(True)
        # outlines, not hinted bitmaps: this is geometry, and it has to match
        # what Qt's SVG renderer would have drawn
        strategy = qt.QFont.StyleStrategy
        font.setStyleStrategy(strategy.PreferOutline | strategy.ForceOutline)
        if hasattr(font, 'setHintingPreference'):
            font.setHintingPreference(
                qt.QFont.HintingPreference.PreferNoHinting)

        # QPainterPath.addText does not fall back to another font for a
        # character this one lacks -- it would draw a box.  Qt's own SVG text
        # route would find a system font, so leave those to it (the size
        # correction below still applies to them).
        try:
            metrics = qt.QFontMetrics(font)
            if any(not metrics.inFontUcs4(ord(char)) for char in text):
                return None
        except Exception:
            pass

        path = qt.QPainterPath()
        path.addText(qt.QPointF(0.0, 0.0), font, text)
        scale = em_units / _OUTLINE_PX
        path = qt.QTransform.fromScale(scale, scale).map(path)
        try:
            x = float(attrs.get('x', '0') or 0)
            y = float(attrs.get('y', '0') or 0)
        except ValueError:
            x = y = 0.0
        if x or y:
            path = qt.QTransform.fromTranslate(x, y).map(path)
        if path.isEmpty():
            return None
        return _svg_path_data(path).encode('utf-8')

    def svg_text_as_paths(svg, label_family='', em_units=None):
        """Replace every <text> in the SVG with equivalent <path> outlines.

        ``label_family`` is the font this text element is set in, used for the
        characters the math font lacks; ``em_units`` is what one em is worth in
        the SVG's user units, so those characters can be drawn at one em.  An
        element that cannot be converted is left as text (and then the font-size
        correction below still keeps its size right).
        """
        key = (svg, label_family, em_units)
        cached = _converted.get(key)
        if cached is not None:
            return cached
        leftover = False

        def one_element(match):
            nonlocal leftover
            attrs = dict((k.decode('ascii', 'replace'), v.decode('utf-8'))
                         for k, v in _ATTR_RE.findall(match.group(1)))
            text = html.unescape(match.group(2).decode('utf-8'))
            try:
                data = _text_to_path_data(text, attrs, label_family, em_units)
            except Exception:
                data = None
            if data is None:
                leftover = True
                return match.group(0)
            transform = attrs.get('transform')
            if transform:
                return (b'<path transform="'
                        + transform.encode('utf-8') + b'" d="' + data + b'"/>')
            return b'<path d="' + data + b'"/>'

        if b'<text' not in svg:
            _converted[key] = (svg, False)
            return svg, False
        converted = _TEXT_ELEM_RE.sub(one_element, svg)
        if len(_converted) > 128:
            _converted.clear()
        _converted[svg] = (converted, leftover)
        return converted, leftover

    def em_in_user_units(svg, w_pt, size_pt):
        """How many SVG user units one em is worth in this formula.

        MathJax lays its maths out with 1 em = the size that was asked for (a
        20pt formula's em is 20pt of paper: the box it gives two unknown
        characters is 40pt), and the plugin paints the SVG into a rect of the
        reported box, so the box and the viewBox give the scale.  Returns None
        if the SVG does not say, and then the engine's own font size is used.
        """
        match = re.search(rb'viewBox="([^"]*)"', svg)
        if match is None or w_pt <= 0 or size_pt <= 0:
            return None
        try:
            viewbox_width = float(match.group(1).split()[2])
        except (IndexError, ValueError):
            return None
        if viewbox_width <= 0:
            return None
        return size_pt * (viewbox_width / w_pt)

    def qt_text_factor(painter):
        """What Qt multiplies SVG <text> font sizes by, or 1.0 if it will not.

        Only Veusz's recording device reports the page dpi in that metric. A
        QImage reports 72 (so there is nothing to cancel), and the QPicture
        fallback used when Veusz's native recording device is missing is not
        scaled this way at all, so both are left alone.
        """
        try:
            device = painter.device()
            if type(device).__name__ != 'RecordPaintDevice':
                return 1.0
            dpi = float(device.metric(
                qt.QPaintDevice.PaintDeviceMetric.PdmDpiY))
        except Exception:
            return 1.0
        return dpi / 72.0 if dpi > 0 else 1.0

    def cancel_qt_text_factor(svg, painter):
        """Undo Qt's device-dpi scaling of any <text> left in the SVG.

        Only needed for characters whose outlines could not be built above.
        """
        factor = qt_text_factor(painter)
        if abs(factor - 1.0) < 1e-6 or b'<text' not in svg:
            return svg
        inverse = 1.0 / factor

        def one_tag(match):
            def one_size(size):
                try:
                    value = float(size.group(1))
                except ValueError:
                    return size.group(0)
                return b'font-size="%gpx"' % (value * inverse)
            return _TEXT_SIZE_RE.sub(one_size, match.group(0))

        return re.compile(rb'<text\b[^>]*>').sub(one_tag, svg)

    class _MathJaxRenderer(textrender._Renderer):
        """Draws MathJax output by painting the SVG, baseline taken from it."""

        def __init__(self, *args, display=False, mathjax_font=None, **kwargs):
            # set before super(): its __init__ calls _initText(), which measures
            #
            # NOTE the name: _Renderer.__init__'s second positional parameter is
            # already called ``font`` (it is the QFont), so a keyword of that
            # name collides with it and every call raises.  That failure was
            # invisible -- _renderer catches exceptions and falls back to plain
            # text -- so the font silently did nothing.
            self.display = bool(display)
            self.font_id = mathjax_font
            super().__init__(*args, **kwargs)

        def _initText(self, text):
            self.error = ''
            self.svgbytes = None
            self.renderer = None
            self.color = None
            self.w = self.h = self.ascent = 1.0
            self.text = text
            self.painter_size = 1.0
            self.measure()

        # -- geometry ------------------------------------------------------
        def _pixperpt(self):
            p = self.painter
            val = getattr(p, 'pixperpt', None)
            if val:
                return float(val)
            dpi = getattr(p, 'dpi', None)
            if dpi:
                return float(dpi) / 72.0
            try:
                return p.device().logicalDpiY() / 72.0
            except Exception:
                return 96.0 / 72.0

        def _text_size_pt(self):
            size = self.font.pointSizeF()
            if size is None or size <= 0:
                size = self.font.pixelSize() / self._pixperpt()
            return max(float(size), 1.0)

        def measure(self):
            """Render (or re-render) the SVG for the current size and colour."""
            size = self._text_size_pt()
            color = None
            try:
                pen = self.painter.pen()
                if pen.style() != qt.Qt.PenStyle.NoPen:
                    color = pen.color().name()
            except Exception:
                color = None

            host = hosts.host_for(self.font_id)
            svg, w_pt, h_pt, base_pt = host.render(self.text, size, color,
                                                   self.display, self.font_id)
            if not svg:
                raise RuntimeError('empty SVG')
            svg, left_as_text = svg_text_as_paths(
                svg, self.font.family(), em_in_user_units(svg, w_pt, size))
            if left_as_text:
                svg = cancel_qt_text_factor(svg, self.painter)
            scale = self._pixperpt()
            self.svgbytes = svg
            self.color = color
            self.painter_size = size
            self.w = max(w_pt * scale, 1.0)
            self.h = max(h_pt * scale, 1.0)
            descent = max(-base_pt * scale, 0.0)
            self.ascent = max(self.h - descent, 1.0)
            from PyQt6.QtSvg import QSvgRenderer
            self.renderer = QSvgRenderer(qt.QByteArray(svg))

        # -- _Renderer interface -------------------------------------------
        def _getWidthHeight(self):
            # ascent as the total height: then the framework's (xi, yi) is the
            # baseline, which is what MathJax reports through vertical-align
            return self.w, self.ascent, 0.0

        def render(self):
            if self.calcbounds is None:
                self.getBounds()
            p = self.painter
            p.save()
            if self.renderer is None or not self.renderer.isValid():
                p.setFont(qt.QFont())
                p.setPen(qt.QPen(qt.QColor('red')))
                p.drawText(
                    qt.QRectF(self.xi, self.yi, 300, 200),
                    qt.Qt.AlignmentFlag.AlignLeft
                    | qt.Qt.AlignmentFlag.AlignTop,
                    'TeX error: %s' % (self.error or 'cannot render'))
                p.restore()
                return self.calcbounds
            p.translate(self.xi, self.yi)
            p.rotate(self.angle)
            p.translate(0.0, -self.ascent)
            self.renderer.render(p, qt.QRectF(0.0, 0.0, self.w, self.h))
            p.restore()
            return self.calcbounds

    return _MathJaxRenderer


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

_current_text_settings = threading.local()
_last_font_owner = threading.local()


def _font_owner(font):
    """The settings group this font was made from, if it was made here.

    Every veusz widget builds the font of a text element from that element's
    own settings group, immediately before drawing it -- ``s.get('TickLabels')
    .makeQFont(painter)`` for the tick numbers, ``s.get('Label').makeQFont(
    painter)`` for the axis label, ``s.get('Text').makeQFont(painter)`` for a
    label or a key.  A veusz that has TeX built in passes that same group's
    flag to the renderer at the call site; stock veusz passes nothing, so the
    group is recorded when the font is made and matched against the font the
    renderer receives.  That is what keeps an axis label's MathJax switch out
    of its tick numbers.
    """
    record = getattr(_last_font_owner, 'value', None)
    if record is None:
        return None
    owner, owner_font = record
    try:
        if owner_font != font:
            return None
    except Exception:
        return None
    return owner


def _wants_mathjax(settings):
    """Whether this settings group asked for MathJax.

    ``useTeX`` is the name this plugin used before 0.3.0; it is still read (the
    setting itself forwards to ``mathjax``, see install()) so that documents
    written with the old name keep rendering, and it is also what a veusz with
    TeX built in calls it.
    """
    if settings is None:
        return False
    for name in ('mathjax', 'useTeX'):
        try:
            if getattr(settings, name, False):
                return True
        except Exception:
            continue
    return False


def _display_style(settings):
    """Display style for this text.  Inline is the default."""
    try:
        return bool(getattr(settings, 'mathjaxDisplay', False))
    except Exception:
        return False


def _font_for_text(settings):
    """Font id this text asked for, or None to use the bundle's default."""
    try:
        return getattr(settings, 'mathjaxFont', '') or None
    except Exception:
        return None


def _settings_for_text(font=None):
    """The settings group the text about to be painted belongs to, or None.

    The group whose makeQFont produced this font wins, because that is the one
    the text belongs to.  The widget-level record is only a fallback, for text
    painted without a preceding makeQFont.
    """
    if font is not None:
        owner = _font_owner(font)
        if owner is not None:
            return owner
    return getattr(_current_text_settings, 'value', None)


def _is_settings(obj):
    return hasattr(obj, '__dict__') and 'setdict' in obj.__dict__


def _find_mathjax_settings(settings, depth=0):
    """Settings object of this widget that asked for MathJax, or None.

    Fallback only.  The precise answer comes from the font the renderer is
    given (see ``_font_owner``); this is for text painted without a preceding
    ``makeQFont``, where all that is known is the widget.  It walks the nested
    settings groups because a widget's text settings are not always at
    ``settings.Text``: axes keep theirs under ``settings.ticklabels`` /
    ``settings.label``, keys under ``settings.Text``, and so on.
    """
    if depth > 4 or not _is_settings(settings):
        return None
    try:
        names = list(settings.__dict__['setdict'])
    except Exception:
        return None
    for name in names:
        try:
            value = getattr(settings, name)
        except Exception:
            continue
        if not _is_settings(value):
            continue
        if _wants_mathjax(value):
            return value
        deeper = _find_mathjax_settings(value, depth + 1)
        if deeper is not None:
            return deeper
    return None


def install(verbose=True):
    here = _plugin_dir()
    from veusz import setting
    from veusz.document import thefactory
    from veusz.setting import collections
    from veusz.utils import textrender

    bridge = _find_bridge(here)
    bundle = _find_bundle(here)
    if bridge is None or bundle is None:
        raise RuntimeError(
            'missing %s. Put mathjaxbridge.dll/.so and mathjax_bundle.js in a '
            'data/ directory next to the plugin file, or set '
            'VEUSZ_JSENGINES_BRIDGE / VEUSZ_JSENGINES_BUNDLE.'
            % ('bridge library' if bridge is None else 'mathjax_bundle.js'))

    fonts, default_font = _discover_fonts(here, bundle)
    hosts = _HostSet(bridge, fonts, default_font, core_bundle=bundle)

    import veusz.qtall as qt
    from veusz.setting import controls
    renderer_class = build_renderer_class(textrender, qt, hosts)

    # ---- 1. the MathJax row on every text-bearing widget -------------------
    # One visible setting carries the row; the other two are hidden so the
    # panel shows a single line:  [x] MathJax   [font v]   [ ] Display style
    _orig_text_init = collections.Text.__init__

    class _MathJaxRow(qt.QWidget):
        """The MathJax switch, the font chooser and the style box in one row.

        It drives three settings; veusz applies whichever one the signal names,
        so each change goes through the normal command/undo path.
        """

        sigSettingChanged = qt.pyqtSignal(qt.QObject, object, object)

        def __init__(self, setting, parent):
            qt.QWidget.__init__(self, parent)
            self.setting = setting
            text = setting.parent                 # the Text settings group
            self.display_setting = text.get('mathjaxDisplay')
            self.font_setting = text.get('mathjaxFont')
            self.ignore = False

            layout = qt.QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            self.setLayout(layout)

            self.check = qt.QCheckBox()
            self.check.setToolTip('Render this text with MathJax')
            self.check.setChecked(bool(setting.val))
            self.check.toggled.connect(self._on_check)
            layout.addWidget(self.check)

            self.combo = qt.QComboBox()
            for f in fonts:
                self.combo.addItem(f.get('title') or f['id'], f['id'])
            idx = self.combo.findData(self.font_setting.val or default_font)
            self.combo.setCurrentIndex(max(idx, 0))
            if len(fonts) < 2:
                self.combo.setEnabled(False)
                self.combo.setToolTip('this package ships one font')
            self.combo.currentIndexChanged.connect(self._on_font)
            layout.addWidget(self.combo, 1)

            self.style = qt.QCheckBox('Display style')
            self.style.setToolTip(
                'Typeset as a displayed equation: larger fractions, limits '
                'above and below.  Off typesets it inline.')
            self.style.setChecked(bool(self.display_setting.val))
            self.style.toggled.connect(self._on_style)
            layout.addWidget(self.style)

            for setn in (setting, self.display_setting, self.font_setting):
                setn.setOnModified(self._sync)

        def _emit(self, setn, value):
            if not self.ignore:
                self.sigSettingChanged.emit(self, setn, value)

        def _on_check(self, state):
            self._emit(self.setting, bool(state))

        def _on_style(self, state):
            self._emit(self.display_setting, bool(state))

        def _on_font(self, _index):
            self._emit(self.font_setting, self.combo.currentData())

        @qt.pyqtSlot()
        def _sync(self):
            """The settings changed elsewhere: follow them."""
            self.ignore = True
            self.check.setChecked(bool(self.setting.val))
            self.style.setChecked(bool(self.display_setting.val))
            idx = self.combo.findData(self.font_setting.val or default_font)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
            self.ignore = False

    class _MathJaxSetting(setting.Bool):
        """The switch, whose row also shows the font and the style."""

        def makeControl(self, *args):
            try:
                return _MathJaxRow(self, *args)
            except Exception:
                return controls.Bool(self, *args)     # never lose the checkbox

    def _text_init(self, name, **args):
        _orig_text_init(self, name, **args)
        if 'mathjax' not in self:
            self.add(_MathJaxSetting(
                'mathjax', False,
                descr='Render this text with MathJax (the font and the style '
                      'are next to this box)',
                usertext='MathJax'))
        if 'mathjaxDisplay' not in self:
            self.add(setting.Bool(
                'mathjaxDisplay', False, hidden=True,
                descr='Typeset as a displayed equation: larger fractions, '
                      'limits above and below the operator.  Off typesets it '
                      'inline, the way text in a paragraph looks.',
                usertext='Display style'))
        if 'mathjaxFont' not in self:
            self.add(setting.Str(
                'mathjaxFont', '', hidden=True,
                descr='Which MathJax font to use for this text (see the font '
                      'list in the MathJax row)',
                usertext='Font'))
        # Documents written before 0.3.0 store the switch as "useTeX"; this
        # hidden setting forwards the old name to the new one, on load and on
        # ifc.Set() alike.
        if 'useTeX' not in self:
            self.add(setting.SettingBackwardCompat('useTeX', 'mathjax', False))

    collections.Text.__init__ = _text_init

    # ---- 1b. remember which settings group a font was made from -----------
    # This is the per-text half of the answer: a widget may hold several text
    # elements (an axis has tick numbers and a label) and each has its own
    # MathJax switch, so "is this text MathJax?" has to be decided per element,
    # from the group that made its font, not once per widget.
    _orig_makeQFont = collections.Text.makeQFont

    def _makeQFont(self, painthelper):
        font = _orig_makeQFont(self, painthelper)
        _last_font_owner.value = (self, font)
        return font

    collections.Text.makeQFont = _makeQFont

    # ---- 2. remember which widget is being painted ------------------------
    wrapped = []

    def _wrap_draw(cls):
        if getattr(cls.draw, '_mathjax_plugin', False):
            return
        orig = cls.draw

        def draw(self, *args, **kwargs):
            settings = getattr(self, 'settings', None)
            mathjax_settings = None
            if settings is not None:
                try:
                    own = settings.Text
                except AttributeError:
                    own = None
                if _wants_mathjax(own):
                    mathjax_settings = own
                else:
                    mathjax_settings = _find_mathjax_settings(settings)
            previous = getattr(_current_text_settings, 'value', None)
            previous_owner = getattr(_last_font_owner, 'value', None)
            _current_text_settings.value = mathjax_settings
            _last_font_owner.value = None      # fonts seen are this draw's
            try:
                return orig(self, *args, **kwargs)
            finally:
                _current_text_settings.value = previous
                _last_font_owner.value = previous_owner

        draw._mathjax_plugin = True
        cls.draw = draw
        wrapped.append(cls.__name__)

    for cls in thefactory.listWidgetClasses():
        if hasattr(cls, 'draw'):
            _wrap_draw(cls)

    # ---- 3. the renderer factory -----------------------------------------
    _orig_renderer = textrender.Renderer

    def _renderer(painter, font, x, y, text,
                  alignhorz=-1, alignvert=-1, angle=0, usefullheight=False,
                  doc=None, **kwargs):
        settings = _settings_for_text(font)
        if _wants_mathjax(settings) and text \
                and not text.lstrip().startswith('<'):
            try:
                return renderer_class(
                    painter, font, x, y, text,
                    alignhorz=alignhorz, alignvert=alignvert, angle=angle,
                    usefullheight=usefullheight, doc=doc,
                    display=_display_style(settings),
                    mathjax_font=_font_for_text(settings))
            except Exception as e:
                pass        # fall through to normal text rendering
        return _orig_renderer(
            painter, font, x, y, text,
            alignhorz=alignhorz, alignvert=alignvert, angle=angle,
            usefullheight=usefullheight, doc=doc, **kwargs)

    textrender.Renderer = _renderer

    # widgets call utils.Renderer, so patch the name they resolve
    import veusz.utils as utils
    utils.Renderer = _renderer

    # Which copy of this file is actually running is worth recording: Veusz
    # loads plugins once, at startup, so editing the file does nothing to a
    # running Veusz, and that is easy to mistake for a fix that did not work.
    try:
        source = _plugin_path()
        stamp = source.stat().st_mtime
        loaded = '%s (%d bytes, modified %s)' % (
            source, source.stat().st_size,
            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stamp)))
    except Exception:
        loaded = '<unknown>'

    if verbose:
        print('veusz-mathjax: TeX rendering enabled (MathJax bundle)')
        print('  plugin: %s' % loaded)
        print('  bridge: %s' % bridge)
        print('  bundle: %s' % bundle)
        print('  widgets wired: %d' % len(wrapped))
    try:
        base = _plugin_dir()
        if base is not None:
            (base / 'veusz_mathjax.log').write_text(
                'veusz-mathjax: installed at %s\nplugin: %s\nbridge: %s\n'
                'bundle: %s\nwidgets wired: %d\n'
                % (time.strftime('%Y-%m-%d %H:%M:%S'), loaded, bridge, bundle,
                   len(wrapped)), encoding='utf-8')
    except Exception:
        pass
    return {'bridge': str(bridge), 'bundle': str(bundle),
            'widgets': wrapped, 'hosts': hosts, 'fonts': fonts}


def _auto_install():
    try:
        install()
    except Exception as e:
        msg = 'veusz-mathjax: not installed: %s' % e
        sys.stderr.write(msg + '\n')
        try:
            import traceback
            base = _plugin_dir() or Path(os.environ.get('TEMP', '.'))
            (base / 'veusz_mathjax.log').write_text(
                msg + '\n' + traceback.format_exc(), encoding='utf-8')
        except Exception:
            pass


if os.environ.get('VEUSZ_MATHJAX_DEFER') != '1':
    _auto_install()
