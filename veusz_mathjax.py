"""veusz plugin: MathJax TeX rendering for *any* veusz, including stock releases.

Part of veusz-mathjax-plugin.  Copyright (C) 2026 Yu Zhai.
Licensed under the Apache License, Version 2.0; see LICENSE.

It carries the whole feature -- settings, renderer and widget wiring -- so it
works on an unmodified veusz (no "Use TeX" checkbox to begin with, no engine
dropdown, no TeX-enabled build required).

How it works, and why each piece is needed:

  1. ``collections.Text.__init__`` is wrapped to add a ``useTeX`` boolean to the
     text settings of every text-bearing widget (the properties panel is
     generated from the settings tree, so the checkbox appears by itself).
  2. ``Widget.draw`` is wrapped for every registered widget class to publish
     "the text settings of the widget currently being painted" in a context
     variable -- that is how the factory below learns whether *this* label
     asked for TeX.  No widget internals are touched.
  3. ``veusz.utils.Renderer`` (the single choke point every widget paints text
     through) is wrapped: with ``useTeX`` set it returns a renderer that draws
     the MathJax SVG, otherwise the original renderer is used.
  4. The MathJax SVG is painted with Qt's own SVG renderer, and the baseline
     (MathJax's ``vertical-align``) is honoured so TeX labels line up with
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

    def render(self, tex, text_size, color=None):
        """Return (svg_bytes, width_pt, height_pt, baseline_pt)."""
        key = (tex, float(text_size), color or '')
        hit = self.svg_cache.get(key)
        if hit is not None:
            return hit
        out_svg = ctypes.c_void_p()
        out_len = ctypes.c_size_t()
        w, h, b = (ctypes.c_float(), ctypes.c_float(), ctypes.c_float())
        err = ctypes.c_void_p()
        rc = self.lib.mathjax_render_svg(
            tex.encode('utf-8'), float(text_size), 1,
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
    """Create the TeX renderer class (needs the veusz modules to subclass)."""

    class _MathJaxRenderer(textrender._Renderer):
        """Draws TeX by painting the MathJax SVG, baseline taken from it."""

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

            svg, w_pt, h_pt, base_pt = host.render(self.text, size, color)
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


def _current_use_tex():
    settings = getattr(_current_text_settings, 'value', None)
    return bool(settings is not None and getattr(settings, 'useTeX', False))


def _is_settings(obj):
    return hasattr(obj, '__dict__') and 'setdict' in obj.__dict__


def _find_tex_settings(settings, depth=0):
    """Settings object of this widget that asked for TeX, or None.

    A widget's text settings are not always at ``settings.Text``: axes keep
    theirs under ``settings.ticklabels`` / ``settings.label``, keys under
    ``settings.Text``, and so on.  The renderer factory cannot know which of
    them belongs to the text being painted (that information only exists at the
    widget's own call site), so the plugin looks for any nested Text group with
    useTeX set and uses that for the widget's text.
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
        if 'useTeX' in value and getattr(value, 'useTeX', False):
            return value
        deeper = _find_tex_settings(value, depth + 1)
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

    # ---- 1. the Use TeX checkbox on every text-bearing widget -------------
    _orig_text_init = collections.Text.__init__

    def _text_init(self, name, **args):
        _orig_text_init(self, name, **args)
        if 'useTeX' not in self:
            self.add(setting.Bool(
                'useTeX', False,
                descr='Render this text as TeX (MathJax plugin)',
                usertext='Use TeX'))

    collections.Text.__init__ = _text_init

    # ---- 2. remember which widget is being painted ------------------------
    wrapped = []

    def _wrap_draw(cls):
        if getattr(cls.draw, '_mathjax_plugin', False):
            return
        orig = cls.draw

        def draw(self, *args, **kwargs):
            settings = getattr(self, 'settings', None)
            tex_settings = None
            if settings is not None:
                try:
                    own = settings.Text
                except AttributeError:
                    own = None
                if own is not None and getattr(own, 'useTeX', False):
                    tex_settings = own
                else:
                    tex_settings = _find_tex_settings(settings)
            previous = getattr(_current_text_settings, 'value', None)
            _current_text_settings.value = tex_settings
            try:
                return orig(self, *args, **kwargs)
            finally:
                _current_text_settings.value = previous

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
        if _current_use_tex() and text and not text.lstrip().startswith('<'):
            try:
                return renderer_class(
                    painter, font, x, y, text,
                    alignhorz=alignhorz, alignvert=alignvert, angle=angle,
                    usefullheight=usefullheight, doc=doc)
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
