"""Smoke test: the plugin really renders TeX in *your* veusz.

Run this with the veusz you actually use (its Python), from anywhere:

    python test/smoke_test.py            # uses this project's plugin + data

Three things are checked, in this order, because each one fails for a different
reason and the messages should say which:

1. **The engine works.**  The bridge is loaded with ctypes and a formula is
   rendered through it directly, with no veusz involved.  This is what fails
   when data/ is incomplete or a binary cannot be loaded, and it fails with the
   real reason instead of a symptom.

2. **The plugin installs.**  It is loaded the way veusz loads plugins, and
   `veusz.utils.Renderer` must have been replaced -- by identity, not by type:
   veusz's own `Renderer` is a function already, so an isinstance() test would
   pass even when nothing was installed (measured).  A plugin whose install()
   throws leaves the `useTeX` setting behind but renders nothing through
   MathJax; that state is caught here.

3. **Drawing works.**  A TeX label, a TeX axis label and an ordinary label are
   exported and must all contain ink, so a working plugin cannot break normal
   text.

4. **The size is right on the paper.**  A 20pt formula has to be 20pt of paper,
   whatever dpi is used: the ink height of \\mathrm{H} is measured in points
   (ink pixels * 72 / dpi) and compared with what the font's own metrics say
   (New Computer Modern's cap height, 0.683 em = 13.7 pt).  This is the check
   that catches a formula rendered at the wrong size -- a 33% error shipped
   once because nothing measured it.

Note for anyone tempted to simplify this: comparing the TeX label against the
same label with `useTeX` off does **not** work as a check.  Measured on a broken
install -- bridge present, engine unloadable -- those two images come out with
4932 vs 1094 ink.  They differ even though no TeX was rendered, because veusz's
own renderer typesets a LaTeX-like source itself.  Ink, and ink differences, are
both useless as evidence here; only the three checks above are.

Non-zero exit code means one of them failed.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT / 'veusz_mathjax.py'
DATA = PROJECT / 'data'

BRIDGE_NAMES = ('mathjaxbridge.dll', 'libmathjaxbridge.so',
                'libmathjaxbridge.dylib')
ENGINE_NAMES = ('qjs.dll', 'libqjs.so', 'libqjs.dylib')
BUNDLE = DATA / 'mathjax_bundle.js'


def first_existing(names):
    for n in names:
        if (DATA / n).exists():
            return DATA / n
    return None


def die(msg):
    sys.exit('FAIL: ' + msg)


print('project     :', PROJECT)
print('plugin      :', PLUGIN)

# ---------------------------------------------------------------- data files
if not BUNDLE.exists():
    die('data/mathjax_bundle.js is missing -- run tools/build_all.py, or unpack '
        'the release zip whole')
BRIDGE = first_existing(BRIDGE_NAMES)
ENGINE = first_existing(ENGINE_NAMES)
if BRIDGE is None:
    die('no bridge library in data/ -- run tools/build_all.py, or unpack the '
        'release zip whole')
if ENGINE is None:
    die('no QuickJS engine in data/ (data/qjs.dll) -- the bridge imports it and '
        'cannot load without it; run src/build-quickjs-windows.cmd, or unpack '
        'the release zip whole')

# ------------------------------------------------------- 1. the engine works
# The plugin module imports nothing beyond the standard library when
# VEUSZ_MATHJAX_DEFER is set, so this needs no veusz and no Qt.
os.environ['VEUSZ_MATHJAX_DEFER'] = '1'
sys.path.insert(0, str(PROJECT))
try:
    import veusz_mathjax as plugin_module
except ImportError as exc:
    die('cannot import the plugin file itself: %s' % exc)

print('bridge      :', BRIDGE.name)
print('engine      :', ENGINE.name)
try:
    host = plugin_module.JsHost(BRIDGE, BUNDLE)
    svg, w, h, baseline = host.render(r'\frac{a}{b}', 12.0)
except Exception as exc:                                   # noqa: BLE001
    die('the engine did not load or render: %s\n'
        '      (the plugin also writes this to veusz_mathjax.log next to the '
        'plugin file)' % exc)
if not svg or b'<svg' not in svg[:600]:
    die('the engine returned something that is not SVG: %r' % svg[:120])
print('engine check: rendered %d bytes of SVG (%.2f x %.2f pt, baseline %.2f)'
      % (len(svg), w, h, baseline))

# ------------------------------------------------------ 2. the plugin installs
try:
    import veusz
except ImportError:
    die('veusz is not importable with this Python (%s).  Run this script with '
        'the interpreter of your veusz installation, or with PYTHONPATH '
        'pointing at a veusz checkout.' % sys.executable)

print('veusz       :', Path(veusz.__file__).resolve().parents[1])

import veusz.qtall as qt                                        # noqa: E402

app = qt.QApplication.instance() or qt.QApplication([])
import veusz.document                                           # noqa: E402
import veusz.utils                                              # noqa: E402
import veusz.windows.mainwindow                                 # noqa: E402,F401

original_renderer = veusz.utils.Renderer
before = 'useTeX' in veusz.setting.collections.Text('probe').__dict__['setdict']

del os.environ['VEUSZ_MATHJAX_DEFER']       # let the copy veusz loads install
veusz.document.Document.loadPlugins(pluginlist=[str(PLUGIN)])

after = 'useTeX' in veusz.setting.collections.Text('probe').__dict__['setdict']
print('Text.useTeX:', before, '->', after)
if not after:
    die('the plugin added no useTeX setting, so it did not install -- see '
        'veusz_mathjax.log next to the plugin file')
if veusz.utils.Renderer is original_renderer:
    die('the plugin added the useTeX setting but never replaced '
        'veusz.utils.Renderer, so nothing would be rendered through MathJax.\n'
        '      install() failed -- see veusz_mathjax.log next to the plugin '
        'file')
print('Renderer    : replaced by the plugin')

# --------------------------------------------------------- 3. drawing works
tmp = Path(tempfile.mkdtemp(prefix='veusz-mathjax-smoke-'))
LABEL_TEX = r'x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}'
ok = True


def ink(path):
    img = qt.QImage(str(path)).convertToFormat(qt.QImage.Format.Format_ARGB32)
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if (img.pixel(x, y) >> 24) & 0xFF:
                n += 1
    return n


def render(name, build):
    doc = veusz.document.Document()
    ifc = veusz.document.CommandInterface(doc)
    build(ifc)
    path = tmp / ('%s.png' % name)
    ifc.Export(str(path), dpi=150)
    n = ink(path)
    print('%-14s ink=%-7d %s' % (name, n, path))
    return n


def label_doc(ifc):
    page = ifc.Add('page')
    ifc.To(page)
    ifc.Add('label', name='lbl')
    ifc.Set('lbl/label', LABEL_TEX)
    ifc.Set('lbl/Text/useTeX', True)
    ifc.Set('lbl/Text/size', '20pt')


def axis_doc(ifc):
    ifc.SetData('x', [0.0, 1.0, 2.0, 3.0])
    ifc.SetData('y', [0.0, 1.0, 4.0, 9.0])
    page = ifc.Add('page')
    ifc.To(page)
    ifc.Add('graph', name='g')
    ifc.To('g')
    ifc.Add('xy', xData='x', yData='y')
    ifc.Set('x/label', r'\alpha (rad)')
    ifc.Set('x/Label/useTeX', True)


def plain_doc(ifc):
    page = ifc.Add('page')
    ifc.To(page)
    ifc.Add('label', name='lbl')
    ifc.Set('lbl/label', 'plain text label')


def glyph_doc(text, size_pt, usetex):
    """One glyph on a page, so its ink bounding box is the glyph itself."""
    def build(ifc):
        page = ifc.Add('page')
        ifc.To(page)
        ifc.Add('label', name='lbl')
        ifc.Set('lbl/label', text)
        ifc.Set('lbl/Text/size', '%gpt' % size_pt)
        ifc.Set('lbl/Text/useTeX', usetex)

    return build


def glyph_height_pt(text, size_pt, usetex, dpi):
    """Ink height of a single glyph, in points of paper (so: dpi-independent)."""
    doc = veusz.document.Document()
    ifc = veusz.document.CommandInterface(doc)
    glyph_doc(text, size_pt, usetex)(ifc)
    path = tmp / ('glyph-%s-%d.png' % ('tex' if usetex else 'plain', dpi))
    ifc.Export(str(path), dpi=dpi)
    img = qt.QImage(str(path)).convertToFormat(qt.QImage.Format.Format_ARGB32)
    ys = [y for y in range(img.height())
          for x in range(img.width()) if (img.pixel(x, y) >> 24) & 0xFF]
    if not ys:
        return 0.0
    return (max(ys) - min(ys) + 1) * 72.0 / dpi


if render('tex-label', label_doc) <= 0:
    ok = False
    print('FAIL: the TeX label drew nothing')
if render('tex-axis', axis_doc) <= 0:
    ok = False
    print('FAIL: the TeX axis label drew nothing')
if render('plain', plain_doc) <= 0:
    ok = False
    print('FAIL: an ordinary label drew nothing (the plugin broke normal text)')

# ------------------------------------------- 4. is the size right on the paper?
# Both paths are measured in points of paper, which is what has to match: the
# ink height of a 20pt glyph, in points, does not depend on the dpi used.
GLYPH_PT = 20.0
CAP_HEIGHT_EM = 0.683            # New Computer Modern, the bundled math font
EXPECTED_PT = CAP_HEIGHT_EM * GLYPH_PT          # 13.66 pt
TOLERANCE = 0.06                 # ink bounding-box rounding at this dpi
SIZE_DPI = 150

tex_pt = glyph_height_pt(r'\mathrm{H}', GLYPH_PT, True, SIZE_DPI)
plain_pt = glyph_height_pt('H', GLYPH_PT, False, SIZE_DPI)
print()
print('size: at %gpt, \\mathrm{H} is %.2f pt of paper (%.3f em of the requested '
      'size); a plain H is %.2f pt of paper'
      % (GLYPH_PT, tex_pt, tex_pt / GLYPH_PT, plain_pt))
if tex_pt <= 0:
    ok = False
    print('FAIL: could not measure the TeX glyph')
elif abs(tex_pt - EXPECTED_PT) > TOLERANCE * EXPECTED_PT:
    ok = False
    print('FAIL: expected about %.2f pt for a %gpt formula, measured %.2f pt '
          '(%.1f%% off).\n'
          '      The formula is not being drawn at the size that was asked for.'
          % (EXPECTED_PT, GLYPH_PT, tex_pt,
             100.0 * (tex_pt - EXPECTED_PT) / EXPECTED_PT))
if plain_pt > 0 and tex_pt > 0 and not (0.75 < tex_pt / plain_pt < 1.25):
    ok = False
    print('FAIL: TeX text is %.2fx the height of ordinary text at the same '
          'point size' % (tex_pt / plain_pt))

print()
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
