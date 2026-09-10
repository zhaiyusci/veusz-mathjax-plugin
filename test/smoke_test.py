"""Smoke test: the plugin renders TeX in *your* veusz.

Run this with the veusz you actually use (its Python), from anywhere:

    python test/smoke_test.py            # uses this project's plugin + data

It loads the plugin the way veusz does, builds a document with a TeX label and
a TeX axis label, exports a PNG and reports the ink it found.  Non-zero exit
code means the plugin did not install or nothing was rendered.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT / 'veusz_mathjax.py'

try:
    import veusz
except ImportError:
    sys.exit('veusz is not importable with this Python (%s).  Run this script '
             'with the interpreter of your veusz installation, or with '
             'PYTHONPATH pointing at a veusz checkout.' % sys.executable)

print('veusz       :', Path(veusz.__file__).resolve().parents[1])
print('plugin      :', PLUGIN)
if not (PROJECT / 'data' / 'mathjax_bundle.js').exists():
    sys.exit('build the plugin first: data/mathjax_bundle.js is missing '
             '(python tools/build_all.py)')
if not (PROJECT / 'data' / 'mathjaxbridge.dll').exists() \
        and not list((PROJECT / 'data').glob('libmathjaxbridge.*')):
    print('warning: no bridge library in data/ -- rendering will fall back to '
          'plain text')
if not (PROJECT / 'data' / 'qjs.dll').exists() \
        and not list((PROJECT / 'data').glob('libqjs.*')):
    print('warning: no QuickJS engine in data/ -- the bridge imports it and '
          'will not load without it (src/build-quickjs-windows.cmd)')

import veusz.qtall as qt                                        # noqa: E402

app = qt.QApplication.instance() or qt.QApplication([])
import veusz.document                                           # noqa: E402
import veusz.windows.mainwindow                                 # noqa: E402,F401

before = 'useTeX' in veusz.setting.collections.Text('probe').__dict__['setdict']
veusz.document.Document.loadPlugins(pluginlist=[str(PLUGIN)])
after = 'useTeX' in veusz.setting.collections.Text('probe').__dict__['setdict']
print('Text.useTeX:', before, '->', after)
if not after:
    sys.exit('FAIL: the plugin did not install (see veusz_mathjax.log)')

tmp = Path(tempfile.mkdtemp(prefix='veusz-mathjax-smoke-'))
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
    ifc.Set('lbl/label', r'x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}')
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


tex_ink = render('tex-label', label_doc)
axis_ink = render('tex-axis', axis_doc)
plain_ink = render('plain', plain_doc)

if tex_ink <= 0:
    ok = False
    print('FAIL: the TeX label drew nothing')
if plain_ink <= 0:
    ok = False
    print('FAIL: a plain label drew nothing (the plugin broke normal text!)')
print()
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
