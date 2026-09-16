"""Regression tests for Veusz-font mtext (requires Veusz's Python and data/).

    python test/test_text_font.py

Unlike comparing screenshots alone, these tests require the Qt text-run paths
and inspect their geometry, so a silent fallback to Veusz's TeX parser fails.
"""
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
os.environ['VEUSZ_MATHJAX_DEFER'] = '1'
if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(PROJECT))
import veusz_mathjax as plugin
import veusz.qtall as qt
from veusz.utils import textrender

SVG_NS = '{http://www.w3.org/2000/svg}'


def _has_no_outlines(family):
    """True when *family* has glyphs but Qt cannot produce their outlines."""
    try:
        font = qt.QFont(family)
        font.setPixelSize(64)
        raw = qt.QRawFont.fromFont(font)
        if not raw.isValid():
            return False
        indexes = raw.glyphIndexesForString('A')
        if not indexes or indexes[0] == 0:
            return False
        return raw.pathForGlyph(indexes[0]).isEmpty()
    except Exception:
        return False


class TextFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt.QApplication.instance() or qt.QApplication([])
        bundle = PROJECT / 'data' / 'mathjax_bundle.js'
        fonts, default = plugin._discover_fonts(PROJECT, bundle)
        cls.hosts = plugin._HostSet(plugin._find_bridge(PROJECT), fonts, default,
                                   core_bundle=bundle)
        cls.fonts = fonts
        cls.default = default
        cls.renderer_class = plugin.build_renderer_class(textrender, qt, cls.hosts)
        if not cls.hosts.host_for(default).text_font_api:
            raise RuntimeError('Rebuild data/mathjax_bundle.js before this test')
        available = qt.QFontDatabase.families()
        candidates = ['Arial', 'Courier New', 'Times New Roman',
                      'DejaVu Sans', 'DejaVu Sans Mono', 'Liberation Serif']
        cls.families = [name for name in candidates if name in available]
        if len(cls.families) < 2:
            raise RuntimeError('Need two distinct installed fonts for this test')

    def draw(self, tex, family=None, math_font=None, bold=False, italic=False,
             dpi=96, display=False, color='black', angle=0, underline=False,
             style_name='', size=20):
        font = qt.QFont(family or self.families[0])
        font.setPointSizeF(size)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        font.setStyleName(style_name)
        image = qt.QImage(1600, 300, qt.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = qt.QPainter(image)
        painter.pixperpt = dpi / 72
        painter.dpi = dpi
        painter.setPen(qt.QColor(color))
        try:
            renderer = self.renderer_class(
                painter, font, 20, 200, tex, angle=angle,
                display=display, mathjax_font=math_font)
            renderer.render()
            self.assertTrue(renderer.renderer.isValid())
            return renderer, ET.fromstring(renderer.svgbytes)
        finally:
            painter.end()

    def runs(self, root):
        return [n for n in root.iter(SVG_NS + 'path')
                if 'data-veusz-text' in n.attrib]

    def width_pt(self, renderer, dpi=96):
        return renderer.w * 72 / dpi

    def test_latin_text_uses_veusz_font_and_cache_isolated(self):
        a, ar = self.draw(r'\text{WWW iii Fit}')
        b, br = self.draw(r'\text{WWW iii Fit}', self.families[1])
        c, cr = self.draw(r'\text{WWW iii Fit}')
        self.assertEqual(len(self.runs(ar)), 1)
        self.assertNotEqual(self.runs(ar)[0].get('d'), self.runs(br)[0].get('d'))
        self.assertEqual(self.runs(ar)[0].get('d'), self.runs(cr)[0].get('d'))
        self.assertAlmostEqual(a.w, c.w)
        self.assertNotAlmostEqual(a.w, b.w, places=1)

    def test_text_width_matches_qt_advance(self):
        text = 'WWW iii Fit'
        font = qt.QFont(self.families[0])
        font.setPixelSize(1000)
        font.setHintingPreference(qt.QFont.HintingPreference.PreferNoHinting)
        expected = qt.QFontMetricsF(font).horizontalAdvance(text) / 1000 * 20
        renderer, _ = self.draw(r'\text{' + text + '}')
        self.assertAlmostEqual(self.width_pt(renderer), expected, delta=0.03)

    def test_math_is_unchanged_by_text_font(self):
        a, ar = self.draw(r'\frac{a}{b}+\mathrm{Fit}')
        b, br = self.draw(r'\frac{a}{b}+\mathrm{Fit}', self.families[1], bold=True)
        self.assertEqual(self.runs(ar), [])
        self.assertEqual(self.runs(br), [])
        self.assertAlmostEqual(a.w, b.w)
        paths = lambda root: [n.get('d') for n in root.iter(SVG_NS + 'path')]
        self.assertEqual(paths(ar), paths(br))

    def test_text_identical_under_every_math_font(self):
        reference = None
        for spec in self.fonts:
            renderer, root = self.draw(r'\text{Fit 珠子}', math_font=spec['id'])
            self.assertNotIn(b'<text', renderer.svgbytes)
            run_paths = [n.get('d') for n in self.runs(root)]
            self.assertTrue(run_paths)
            actual = (self.width_pt(renderer), renderer.h * 72 / 96, run_paths)
            if reference is not None:
                self.assertAlmostEqual(actual[0], reference[0], delta=0.03)
                self.assertAlmostEqual(actual[1], reference[1], delta=0.03)
                self.assertEqual(actual[2], reference[2])
            reference = actual

    def test_bold_italic_and_element_style(self):
        _, plain = self.draw(r'\text{Fit}')
        _, bold = self.draw(r'\textbf{Fit}')
        _, italic = self.draw(r'\textit{Fit}')
        _, element_bold = self.draw(r'\text{Fit}', bold=True)
        _, element_italic = self.draw(r'\text{Fit}', italic=True)
        path = lambda root: self.runs(root)[0].get('d')
        self.assertNotEqual(path(plain), path(bold))
        self.assertNotEqual(path(plain), path(italic))
        self.assertEqual(path(bold), path(element_bold))
        self.assertEqual(path(italic), path(element_italic))

    def test_following_math_uses_measured_width(self):
        for family in self.families[:2]:
            text, _ = self.draw(r'\text{WWW iii}', family)
            mixed, root = self.draw(r'\text{WWW iii}x', family)
            math, _ = self.draw('x', family)
            self.assertAlmostEqual(mixed.w, text.w + math.w, delta=0.1)
            mi = next(n for n in root.iter() if n.get('data-mml-node') == 'mi')
            offset = float(re.search(r'translate\(([-\d.]+)', mi.get('transform')).group(1))
            self.assertAlmostEqual(offset / 1000 * 20,
                                   self.width_pt(text), delta=0.03)

    def test_spaces_are_not_discarded(self):
        a, _ = self.draw(r'\text{Fit}')
        b, _ = self.draw(r'\text{ Fit }')
        self.assertGreater(b.w, a.w)
        spaces, root = self.draw(r'\text{   }x')
        math, _ = self.draw('x')
        self.assertGreater(spaces.w, math.w)
        self.assertTrue(self.runs(root))

    def test_script_scaling_applied_once(self):
        normal, nr = self.draw(r'\text{Fit}')
        script, sr = self.draw(r'\scriptstyle\text{Fit}')
        tiny, tr = self.draw(r'\scriptscriptstyle\text{Fit}')
        self.assertAlmostEqual(script.w / normal.w, 0.707, delta=0.005)
        self.assertAlmostEqual(tiny.w / normal.w, 0.5, delta=0.005)
        self.assertEqual(self.runs(nr)[0].get('d'), self.runs(sr)[0].get('d'))
        self.assertEqual(self.runs(nr)[0].get('d'), self.runs(tr)[0].get('d'))

    def test_nested_text_and_math(self):
        _, root = self.draw(r'\text{Fit \textbf{bold} $x^2$ end}')
        text = ''.join(n.get('data-veusz-text') for n in self.runs(root))
        self.assertIn('Fit', text)
        self.assertIn('bold', text)
        self.assertIn('end', text)
        self.assertNotIn('x', text)
        self.assertTrue(any(n.get('data-mml-node') == 'msup' for n in root.iter()))

    def test_tex_macro_expands_to_text(self):
        _, root = self.draw(r'\newcommand{\veuszfonttest}{\text{Fit}}\veuszfonttest')
        self.assertEqual([n.get('data-veusz-text') for n in self.runs(root)], ['Fit'])

    def test_unicode_escaping_fallback_and_dpi(self):
        tex = r'\text{Fit 珠子 café a\&b \{x\} "Q"}'
        a, ar = self.draw(tex)
        b, br = self.draw(tex, dpi=300, color='blue', angle=35)
        self.assertNotIn(b'<text', a.svgbytes)
        self.assertNotIn(b'<text', b.svgbytes)
        self.assertEqual([n.get('d') for n in self.runs(ar)],
                         [n.get('d') for n in self.runs(br)])
        self.assertAlmostEqual(self.width_pt(a), self.width_pt(b, 300), places=4)
        self.assertIn(b'#0000ff', b.svgbytes)

    def test_css_emphasis_is_measured_before_outlining(self):
        _, bold = self.draw(r'\textbf{Fit}')
        _, italic = self.draw(r'\textit{Fit}')
        _, css_bold = self.draw(r'\style{font-weight:bold}{\text{Fit}}')
        _, css_italic = self.draw(r'\style{font-style:italic}{\text{Fit}}')
        path = lambda root: self.runs(root)[0].get('d')
        self.assertEqual(path(bold), path(css_bold))
        self.assertEqual(path(italic), path(css_italic))

    def test_emphasis_overrides_named_regular_face(self):
        _, plain = self.draw(r'\text{Fit}', style_name='Regular')
        _, bold = self.draw(r'\textbf{Fit}', style_name='Regular')
        self.assertNotEqual(self.runs(plain)[0].get('d'),
                            self.runs(bold)[0].get('d'))

    def test_underline_and_size_cache(self):
        a, plain = self.draw(r'\text{Fit}')
        b, underlined = self.draw(r'\text{Fit}', underline=True)
        c, large = self.draw(r'\text{Fit}', size=40)
        self.assertAlmostEqual(a.w, b.w)
        self.assertNotEqual(self.runs(plain)[0].get('d'),
                            self.runs(underlined)[0].get('d'))
        self.assertAlmostEqual(c.w, a.w * 2, delta=0.03)
        self.assertEqual(self.runs(plain)[0].get('d'), self.runs(large)[0].get('d'))

    def test_italic_advance_does_not_shift_origin(self):
        text = 'fff'
        font = qt.QFont(self.families[0])
        font.setItalic(True)
        font.setPixelSize(1000)
        font.setHintingPreference(qt.QFont.HintingPreference.PreferNoHinting)
        expected = qt.QFontMetricsF(font).horizontalAdvance(text) / 1000 * 20
        renderer, _ = self.draw(r'\text{' + text + '}', italic=True)
        self.assertAlmostEqual(self.width_pt(renderer), expected, delta=0.03)

    def test_math_matches_legacy_render_api(self):
        tex = r'\frac{a}{b}+x^2+\mathrm{Fit}'
        renderer, root = self.draw(tex)
        svg, w, h, baseline = self.hosts.host_for(self.default).render(tex, 20, '#000000')
        legacy = ET.fromstring(svg)
        self.assertAlmostEqual(self.width_pt(renderer), w, places=4)
        self.assertAlmostEqual(renderer.h * 72 / 96, h, places=4)
        paths = lambda doc: [n.get('d') for n in doc.iter(SVG_NS + 'path')]
        self.assertEqual(paths(root), paths(legacy))

    def test_raster_font_falls_back_instead_of_blank(self):
        """A Font with glyphs but no outlines must not erase the text."""
        raster = [f for f in ('MS Sans Serif', 'System', 'Fixedsys', 'Small Fonts')
                  if f in qt.QFontDatabase.families() and _has_no_outlines(f)]
        if not raster:
            self.skipTest('no raster-only font installed')
        renderer, root = self.draw(r'\text{Fit}', raster[0])
        paths = [n.get('d') for n in root.iter(SVG_NS + 'path') if n.get('d')]
        self.assertTrue(paths, 'the formula came out blank for %r' % raster[0])
        self.assertGreater(self.width_pt(renderer), 10.0)
        # and the ordinary Font still uses the Qt run
        normal, _ = self.draw(r'\text{Fit}')
        self.assertGreater(self.width_pt(normal), 10.0)

    def test_fraction_and_subscript_text(self):
        renderer, root = self.draw(r'\frac{\text{total count}}{\text{time}}+x_{\text{fit}}',
                                   display=True)
        self.assertEqual(len(self.runs(root)), 3)
        self.assertGreater(renderer.h, 20)
        self.assertNotIn(b'<text', renderer.svgbytes)


if __name__ == '__main__':
    unittest.main(verbosity=2)
