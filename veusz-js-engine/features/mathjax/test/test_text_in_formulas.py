"""The regression assertions the original project had, ported to the feature.

    PYTHONPATH=<a Veusz> python veusz-js-engine/features/mathjax/test/test_two_layer.py
    PYTHONPATH=<a Veusz> python veusz-js-engine/features/mathjax/test/test_text_in_formulas.py

``test_two_layer.py`` checks the split; this file checks the *drawing*, the way
the original's ``test/test_text_font.py`` did -- every assertion here is one of
its assertions (95-266), re-aimed at the two-layer path.  That file measured a
renderer object the old Python plugin owned; there is no such object now, so
each case drives the same protocol the platform's draw seam drives -- ask,
shape in the element's font, ask again -- and reads the runs out of the SVG.

What it pins down, in the original's words:

* ``\\text{...}`` is measured with Qt and drawn as outlines, in the *element's*
  font, at the width Qt says it is;
* ``\\textbf`` / ``\\textit`` and CSS emphasis reach those outlines, and an
  element-wide bold/italic reaches them too;
* spaces are kept, scripts are scaled once (0.707 / 0.5), nested text and maths
  come out as separate runs, and a ``\\newcommand`` expands into text -- which
  only works because the item is compiled once and both passes see the same
  macros;
* underline is drawn, and the size lives in the transform rather than in the
  outlines, so the same glyphs serve every size.
"""
import json
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

# this file is inside the feature it tests: features/mathjax/test/
TEST_DIR = Path(__file__).resolve().parent
FEATURE_DIR = TEST_DIR.parent
PLATFORM_DIR = FEATURE_DIR.parent.parent

os.environ['VEUSZ_JS_ENGINE_DEFER'] = '1'
if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(PLATFORM_DIR))

import veusz_js_engine as platform_module                      # noqa: E402
import veusz.qtall as qt                                       # noqa: E402
import veusz.document                                          # noqa: E402
import veusz.setting.collections                               # noqa: E402
import veusz.utils                                             # noqa: E402
import veusz.windows.mainwindow                                # noqa: E402,F401

SVG_NS = '{http://www.w3.org/2000/svg}'
FAMILY = 'DejaVu Sans'                # the family every case is set in

app = qt.QApplication.instance() or qt.QApplication([])        # noqa: E402


def load(plugin=PLATFORM_DIR / 'veusz_js_engine.py'):
    """Load the platform the way Veusz does: exec the file, no deferral."""
    os.environ.pop('VEUSZ_JS_ENGINE_DEFER', None)
    veusz.document.Document.loadPlugins(pluginlist=[str(plugin)])


def feature():
    platform = getattr(veusz.utils, 'js_engine')
    found = [f for f in platform.feature_objects() if f.name == 'mathjax']
    assert found, 'the shipped MathJax feature did not load'
    return platform, found[0]


def label_font(bold=False, italic=False, underline=False, family=FAMILY):
    font = qt.QFont(family, 20)
    font.setBold(bold)
    font.setItalic(italic)
    font.setUnderline(underline)
    return font


def draw(text, font='tex', display=False, size=20.0, label=None, passes=3):
    """One render, with the text handshake completed the way the seam does it.

    This is ``install_js_feature``'s draw callback, without Veusz: ask, shape
    what it asked for in the element's font, ask again.  A test that called
    ``veuszRender`` once would be testing half a protocol.
    """
    platform, js_feature = feature()
    label = label or label_font()
    request = {'text': text, 'size': size, 'color': None,
               'props': {'on': True, 'font': font, 'display': display},
               'face': platform_module.text_font_key(qt, label)}
    reply = json.loads(js_feature.runtime.call('veuszRender',
                                               json.dumps(request)))
    asked = []
    for _ in range(passes):
        if 'measure' not in reply:
            break
        asked.append(reply['measure'])
        request['measured'] = platform_module.measure_text_runs(
            qt, reply['measure'], label)
        reply = json.loads(js_feature.runtime.call('veuszRender',
                                                   json.dumps(request)))
    else:
        raise AssertionError('the feature kept asking for measurements')
    reply['asked'] = asked
    return reply


def root_of(reply):
    assert 'error' not in reply, reply.get('error')
    return ET.fromstring(reply['svg'])


def runs(root):
    """The outlined words: paths the feature marked as shaped text."""
    return [node for node in root.iter(SVG_NS + 'path')
            if 'data-veusz-text' in node.attrib]


def outline(reply):
    """The words' outline data, as one string -- identity for a comparison."""
    return [node.get('d') for node in runs(root_of(reply))]


def asked_for(reply):
    """Every run this render asked the platform to shape, in order."""
    return [run for batch in reply['asked'] for run in batch]


def widths(reply):
    """All the ``d`` data in the drawing -- maths included, not just words."""
    return [node.get('d') for node in root_of(reply).iter(SVG_NS + 'path')]


def advance(text, italic=False, bold=False, size=20.0, family=FAMILY):
    """What Qt says that run advances, in points, at the measured size.

    The platform outlines text with ``QTextLayout`` at 1000 px and no hinting,
    so this is the same measurement it makes -- independently arrived at.
    """
    font = qt.QFont(family)
    font.setPixelSize(1000)
    font.setBold(bold)
    font.setItalic(italic)
    font.setHintingPreference(qt.QFont.HintingPreference.PreferNoHinting)
    return qt.QFontMetricsF(font).horizontalAdvance(text) / 1000 * size


class TextInFormulasTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        load()
        cls.platform, cls.feature = feature()

    # -- measured by Qt, drawn as outlines --------------------------------

    def test_the_words_are_drawn_as_outlines_in_the_elements_font(self):
        """The original's run identity: one run, and it is the words.

        ``\\text{...}`` has to leave the math font entirely: the words are the
        *figure's* words, so they are shaped by Qt and drawn as paths -- no
        ``<text>`` element survives, because Veusz cannot draw one.
        """
        reply = draw(r'\text{Fit}')
        self.assertNotIn('<text', reply['svg'])
        runs_ = runs(root_of(reply))
        self.assertEqual(len(runs_), 1)
        self.assertEqual(runs_[0].get('data-veusz-text'), 'Fit')
        self.assertTrue(runs_[0].get('d'))
        self.assertEqual([run['text'] for run in asked_for(reply)], ['Fit'])

        # the maths is not a run: it is the bundle's own outlines
        maths = draw(r'\frac{a}{b}+\mathrm{Fit}')
        self.assertEqual(runs(root_of(maths)), [])
        self.assertTrue(widths(maths))

    def test_the_run_is_as_wide_as_qt_says_it_is(self):
        text = 'WWW iii Fit'
        reply = draw(r'\text{' + text + '}')
        self.assertAlmostEqual(reply['width'], advance(text), delta=0.03)

    def test_italic_advance_does_not_shift_the_origin(self):
        """An italic advance is not its ink: the box must not lose the slant."""
        text = 'fff'
        reply = draw(r'\textit{' + text + '}', label=label_font(italic=True))
        self.assertAlmostEqual(reply['width'],
                               advance(text, italic=True), delta=0.05)

    # -- emphasis, from TeX and from the element ---------------------------

    def test_bold_italic_and_the_elements_own_style(self):
        plain = outline(draw(r'\text{Fit}'))
        bold = outline(draw(r'\textbf{Fit}'))
        italic = outline(draw(r'\textit{Fit}'))
        self.assertNotEqual(plain, bold)
        self.assertNotEqual(plain, italic)
        # the emphasis is asked for, not guessed at from the glyphs
        self.assertTrue(asked_for(draw(r'\textbf{Fit}'))[0]['bold'])
        self.assertTrue(asked_for(draw(r'\textit{Fit}'))[0]['italic'])
        # an element-wide bold/italic reaches the words the same way
        self.assertEqual(bold, outline(draw(r'\text{Fit}',
                                            label=label_font(bold=True))))
        self.assertEqual(italic, outline(draw(r'\text{Fit}',
                                              label=label_font(italic=True))))

    def test_css_emphasis_is_measured_before_outlining(self):
        """``\\style{font-weight:bold}`` must be resolved *before* Qt measures.

        Otherwise the run is shaped in the plain face and then bolded, and the
        words are wider than the space the formula left for them.
        """
        self.assertEqual(outline(draw(r'\textbf{Fit}')),
                         outline(draw(r'\style{font-weight:bold}{\text{Fit}}')))
        self.assertEqual(outline(draw(r'\textit{Fit}')),
                         outline(draw(r'\style{font-style:italic}{\text{Fit}}')))

    # -- spacing and scripts ----------------------------------------------

    def test_spaces_are_not_discarded(self):
        bare = draw(r'\text{Fit}')
        padded = draw(r'\text{ Fit }')
        self.assertGreater(padded['width'], bare['width'])
        spaced = draw(r'\text{   }x')
        alone = draw('x')
        self.assertGreater(spaced['width'], alone['width'])
        self.assertTrue(runs(root_of(spaced)), 'the spaces left no run at all')

    def test_script_scaling_is_applied_once(self):
        """0.707 and 0.5, and the *same glyphs*: the scale is a transform.

        Asking the bundle to re-typeset at a smaller size would scale the
        outlines and the advance both, which is how a script once came out
        0.707 *squared* too small.
        """
        normal = draw(r'\text{Fit}')
        script = draw(r'\scriptstyle\text{Fit}')
        tiny = draw(r'\scriptscriptstyle\text{Fit}')
        self.assertAlmostEqual(script['width'] / normal['width'], 0.707,
                               delta=0.005)
        self.assertAlmostEqual(tiny['width'] / normal['width'], 0.5,
                               delta=0.005)
        self.assertEqual(outline(normal), outline(script))
        self.assertEqual(outline(normal), outline(tiny))

    def test_nested_text_and_math(self):
        reply = draw(r'\text{Fit \textbf{bold} $x^2$ end}')
        words = [node.get('data-veusz-text') for node in runs(root_of(reply))]
        joined = ''.join(words)
        self.assertIn('Fit', joined)
        self.assertIn('bold', joined)
        self.assertIn('end', joined)
        self.assertNotIn('x', joined)
        self.assertTrue(any(node.get('data-mml-node') == 'msup'
                            for node in root_of(reply).iter()),
                        'the maths inside the text was not typeset as maths')

    def test_a_macro_expands_into_text(self):
        """A ``\\newcommand`` that ends in ``\\text`` -- on both passes.

        The item is compiled once and the two passes share it, so the macro is
        still defined when the shape of the drawing is decided the second time.
        """
        reply = draw(r'\newcommand{\veuszfonttest}{\text{Fit}}'
                     r'\veuszfonttest')
        self.assertEqual([node.get('data-veusz-text')
                          for node in runs(root_of(reply))], ['Fit'])

    def test_a_fraction_and_a_subscript_keep_their_words(self):
        reply = draw(r'\frac{\text{total count}}{\text{time}}+x_{\text{fit}}',
                     display=True)
        self.assertEqual(len(runs(root_of(reply))), 3)
        self.assertGreater(reply['height'], 20.0)

    def test_maths_after_a_word_run_starts_where_the_words_end(self):
        """The measured width has to go back *into* MathJax's own layout.

        Shaping the words in Qt is only half the protocol: the formula has to
        be typeset again with the width Qt measured, or the maths after a
        ``\\text{...}`` overlaps it.  MathJax lays the following node out in
        its own units, 1000 to the em, so the offset in the drawing is that
        width -- which makes this the assertion that the answer was used.
        """
        words = draw(r'\text{WWW iii}')
        alone = draw('x')
        mixed = draw(r'\text{WWW iii}x')

        self.assertAlmostEqual(mixed['width'],
                               words['width'] + alone['width'], delta=0.1)
        node = next(n for n in root_of(mixed).iter()
                    if n.get('data-mml-node') == 'mi')
        offset = float(re.search(r'translate\(([-\d.]+)',
                                 node.get('transform')).group(1))
        self.assertAlmostEqual(offset / 1000.0 * 20.0, words['width'],
                               delta=0.03,
                               msg='the maths started where the bundle '
                                   'thought the words ended, not where Qt '
                                   'measured them')

    # -- decorations, and what is in the cache key -------------------------

    def test_underline_is_drawn_and_the_size_is_a_transform(self):
        """Underline is a rule Qt draws, not a glyph, and size is not data.

        The words have to be the *same outlines* at 20 pt and at 40 pt -- the
        size lives in the SVG transform -- which is what makes a size change
        cost nothing but a different wrapper.
        """
        plain = draw(r'\text{Fit}')
        marked = draw(r'\text{Fit}', label=label_font(underline=True))
        large = draw(r'\text{Fit}', size=40.0)

        self.assertAlmostEqual(plain['width'], marked['width'], places=4)
        self.assertNotEqual(outline(plain), outline(marked),
                            'the underline was not drawn')
        self.assertAlmostEqual(large['width'], plain['width'] * 2, delta=0.03)
        self.assertEqual(outline(plain), outline(large))


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(
        TextInFormulasTests))
    sys.exit(0 if result.wasSuccessful() else 1)
