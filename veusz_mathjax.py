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
import os
import sys
import threading
from pathlib import Path

# --------------------------------------------------------------------------
# data discovery
# --------------------------------------------------------------------------

def _plugin_dir():
    """Directory this plugin file lives in.

    veusz loads plugin files with exec(f.read(), {}), so there is no __file__;
    the caller's frame (Document.loadPlugins) holds the path in ``plugin``.
    """
    try:
        frame = sys._getframe(2)
        while frame is not None:
            candidate = frame.f_locals.get('plugin')
            if isinstance(candidate, str) and candidate.endswith('.py'):
                return Path(candidate).resolve().parent
            frame = frame.f_back
    except Exception:
        pass
    return None


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
    cands = [os.environ.get('VEUSZ_JSENGINES_BUNDLE')]
    if here:
        cands += [here / 'data' / 'mathjax_bundle.js',
                  here / 'mathjax_bundle.js',
                  here.parent / 'data' / 'mathjax_bundle.js']
    return _first_existing(cands)


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


# --------------------------------------------------------------------------
# JS host (ctypes; the C ABI of veusz/src/mathjaxbridge)
# --------------------------------------------------------------------------

class JsHost:
    def __init__(self, bridge, bundle):
        self.bridge = Path(bridge)
        self.bundle = Path(bundle)
        self.svg_cache = {}
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
        lib.mathjax_initialize.argtypes = [ctypes.c_char_p]
        lib.mathjax_initialize.restype = ctypes.c_int
        lib.mathjax_render_svg.argtypes = [
            ctypes.c_char_p, ctypes.c_float, ctypes.c_int, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_void_p)]
        lib.mathjax_render_svg.restype = ctypes.c_int
        lib.mathjax_free.argtypes = [ctypes.c_void_p]
        lib.mathjax_free.restype = None
        rc = lib.mathjax_initialize(str(self.bundle).encode('utf-8'))
        if rc != 0:
            raise RuntimeError('mathjax_initialize failed (rc=%d)' % rc)
        self.lib = lib

    def render(self, tex, text_size, color=None, display=False):
        """Return (svg_bytes, width_pt, height_pt, baseline_pt).

        ``display`` picks MathJax's display style -- larger fractions, limits
        above and below the operator -- instead of the inline style, which is
        what text in a paragraph looks like.
        """
        key = (tex, float(text_size), color or '', bool(display))
        hit = self.svg_cache.get(key)
        if hit is not None:
            return hit
        out_svg = ctypes.c_void_p()
        out_len = ctypes.c_size_t()
        w, h, b = (ctypes.c_float(), ctypes.c_float(), ctypes.c_float())
        err = ctypes.c_void_p()
        rc = self.lib.mathjax_render_svg(
            tex.encode('utf-8'), float(text_size), 1 if display else 0,
            color.encode('utf-8') if color else None,
            ctypes.byref(out_svg), ctypes.byref(out_len), ctypes.byref(w),
            ctypes.byref(h), ctypes.byref(b), ctypes.byref(err))
        if rc != 0:
            msg = 'mathjaxbridge failed (rc=%d)' % rc
            if err.value:
                msg = ctypes.cast(err, ctypes.c_char_p).value.decode(
                    'utf-8', 'replace')
                self.lib.mathjax_free(err)
            raise RuntimeError(msg)
        try:
            svg = ctypes.string_at(out_svg.value, out_len.value)
        finally:
            if out_svg.value:
                self.lib.mathjax_free(out_svg)
        result = (svg, w.value, h.value, b.value)
        if len(self.svg_cache) > 256:
            self.svg_cache.clear()
        self.svg_cache[key] = result
        return result


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

def build_renderer_class(textrender, qt, host):
    """Create the MathJax renderer class (needs the veusz modules to subclass)."""

    class _MathJaxRenderer(textrender._Renderer):
        """Draws MathJax output by painting the SVG, baseline taken from it."""

        def __init__(self, *args, display=False, **kwargs):
            # set before super(): its __init__ calls _initText(), which measures
            self.display = bool(display)
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

            svg, w_pt, h_pt, base_pt = host.render(self.text, size, color,
                                                   self.display)
            if not svg:
                raise RuntimeError('empty SVG')
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

    host = JsHost(bridge, bundle)

    import veusz.qtall as qt
    renderer_class = build_renderer_class(textrender, qt, host)

    # ---- 1. the MathJax checkboxes on every text-bearing widget -----------
    _orig_text_init = collections.Text.__init__

    def _text_init(self, name, **args):
        _orig_text_init(self, name, **args)
        if 'mathjax' not in self:
            self.add(setting.Bool(
                'mathjax', False,
                descr='Render this text with MathJax',
                usertext='MathJax'))
        if 'mathjaxDisplay' not in self:
            self.add(setting.Bool(
                'mathjaxDisplay', False,
                descr='Typeset as a displayed equation: larger fractions, '
                      'limits above and below the operator.  Off typesets it '
                      'inline, the way text in a paragraph looks.',
                usertext='Display style'))
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
                    display=_display_style(settings))
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

    if verbose:
        print('veusz-mathjax: TeX rendering enabled (MathJax bundle)')
        print('  bridge: %s' % bridge)
        print('  bundle: %s' % bundle)
        print('  widgets wired: %d' % len(wrapped))
    return {'bridge': str(bridge), 'bundle': str(bundle),
            'widgets': wrapped, 'host': host}


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
