"""The KaTeX feature: it parses, Veusz draws.

    PYTHONPATH=<a Veusz> python veusz-js-engine/features/katex/test/test_katex_feature.py

There is almost nothing of ours to test in the drawing, because we do not draw:
the feature answers a render with "Veusz, draw this MathML", and Veusz's own
MathML widget does the rest.  So what is checked here is

  1. the feature loaded, and its two settings are one row of the panel;
  2. off is off: the hook declines and nothing about the label changes;
  3. on, the answer is a delegation carrying a `<math>` document -- not an SVG
     the platform would paint;
  4. that document is what Veusz really typesets: an export goes through the
     native MathML renderer **without it reporting an error**, which is the
     difference between a formula and Veusz's red explanation of why not;
  5. the space fix, which is the one thing KaTeX's MathML needs: `a\\,b` comes
     out as `<mspace>` and typesets, where a whitespace-only `<mtext>` is
     refused by Qt's widget;
  6. display style is KaTeX's `display="block"`, and changes the picture;
  7. a formula KaTeX cannot parse is a message, not a broken label.
"""
import json
import os
import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
FEATURE_DIR = TEST_DIR.parent
PLATFORM_DIR = FEATURE_DIR.parent.parent

os.environ['VEUSZ_JS_ENGINE_DEFER'] = '1'
if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(PLATFORM_DIR))

import veusz.qtall as qt                                       # noqa: E402
import veusz.document                                          # noqa: E402
import veusz.setting.collections                               # noqa: E402
import veusz.utils                                             # noqa: E402
import veusz.windows.mainwindow                                # noqa: E402,F401

app = qt.QApplication.instance() or qt.QApplication([])        # noqa: E402


def load(plugin=PLATFORM_DIR / 'veusz_js_engine.py'):
    os.environ.pop('VEUSZ_JS_ENGINE_DEFER', None)
    veusz.document.Document.loadPlugins(pluginlist=[str(plugin)])


def feature():
    platform = getattr(veusz.utils, 'js_engine')
    found = [f for f in platform.feature_objects() if f.name == 'katex']
    assert found, 'the KaTeX feature did not load'
    return platform, found[0]


def ask(text, display=False, size=20.0, on=True):
    """One render request, answered by the feature alone.

    A feature that declines answers with the empty string, which is not JSON:
    that is the reply, so it comes back as it is.
    """
    platform, js_feature = feature()
    request = {'text': text, 'size': size, 'color': None,
               'props': {'on': on, 'display': display},
               'face': ''}
    reply = js_feature.runtime.call('veuszRender', json.dumps(request))
    return json.loads(reply) if reply.strip() else ''


class NativeMathMLMixin(object):
    """Watch what Veusz's own MathML renderer says while a page is drawn.

    ``_MmlRenderer._initText`` is where Qt's MML widget is handed the markup,
    and ``self.error`` is its verdict: empty means the formula was typeset,
    anything else means Veusz drew its own red error text instead.  Both put
    ink on the page, so ink alone cannot tell them apart.
    """

    def watch(self):
        textrender = veusz.utils.textrender
        original = textrender._MmlRenderer._initText
        seen = []

        def spy(self, text):
            original(self, text)
            seen.append((text, getattr(self, 'error', '') or ''))

        textrender._MmlRenderer._initText = spy
        self.addCleanup(setattr, textrender._MmlRenderer, '_initText', original)
        return seen

    def draw(self, label, settings=None, name='katex'):
        """Draw one label: its ink, the ink box, and what MML thought."""
        seen = self.watch()
        doc = veusz.document.Document()
        ifc = veusz.document.CommandInterface(doc)
        page = ifc.Add('page')
        ifc.To(page)
        ifc.Add('label', name='lbl')
        ifc.Set('lbl/label', label)
        ifc.Set('lbl/Text/size', '20pt')
        for key, value in (settings or {}).items():
            ifc.Set('lbl/Text/%s' % key, value)
        out = PLATFORM_DIR / 'build-test-katex'
        out.mkdir(exist_ok=True)
        path = out / ('%s.png' % name)
        ifc.Export(str(path), dpi=150)
        image = qt.QImage(str(path))
        assert not image.isNull(), 'the export produced no image'
        image = image.convertToFormat(qt.QImage.Format.Format_ARGB32)
        count = 0
        left, top, right, bottom = 10 ** 6, 10 ** 6, -1, -1
        for y in range(image.height()):
            for x in range(image.width()):
                if (image.pixel(x, y) >> 24) & 0xFF:
                    count += 1
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)
        box = None if count == 0 else (right - left + 1, bottom - top + 1)
        return count, box, seen


class KatexFeatureTests(unittest.TestCase, NativeMathMLMixin):

    @classmethod
    def setUpClass(cls):
        load()
        cls.platform, cls.feature = feature()

    # -- 1. what it adds ---------------------------------------------------

    def test_00_it_loaded_and_asked_for_one_row(self):
        self.assertIn('katex', self.platform.feature_names())
        probe = veusz.setting.collections.Text('probe')
        switch = probe.get('katex')
        display = probe.get('katexDisplay')
        self.assertFalse(switch.hidden)
        self.assertTrue(display.hidden, 'the style kept a row of its own')
        row = switch.makeControl(None)
        self.assertEqual(len(row.controls), 2)
        self.assertEqual(row.controls[1].text(), 'Display style')

    # -- 2. off is off -----------------------------------------------------

    def test_01_the_switch_off_declines(self):
        self.assertEqual(ask(r'\frac{a}{b}', on=False), '')
        plain, _box, seen = self.draw(r'\frac{a}{b}', {'katex': False},
                                      name='katex-off')
        self.assertGreater(plain, 0, 'the label stopped drawing at all')
        self.assertEqual(seen, [],
                         'the native MathML renderer was asked to draw '
                         'something with the feature off')

    # -- 3. on, it is a delegation -----------------------------------------

    def test_02_on_it_hands_veusz_mathml(self):
        self.assertTrue(ask(r'\frac{a}{b}', on=True), 'nothing came back')
        reply = ask(r'\frac{a}{b}', on=True)
        self.assertNotIn('svg', reply,
                         'the feature drew a picture instead of delegating')
        self.assertNotIn('error', reply)
        self.assertTrue(reply.get('delegate', '').startswith('<math'),
                        'the delegated text is not a MathML document: %r'
                        % reply.get('delegate', '')[:60])
        self.assertTrue(reply['delegate'].rstrip().endswith('</math>'))
        self.assertNotIn('<span', reply['delegate'],
                         'the KaTeX wrapper was handed to Veusz as well')
        self.assertNotIn('annotation', reply['delegate'],
                         'KaTeX\'s copy of the LaTeX was handed over: Qt draws '
                         'that element as text, after the formula')

    def test_02b_the_latex_is_not_drawn_after_the_formula(self):
        """KaTeX's `<annotation>` is a note to itself, and Qt draws it.

        Qt's MathML widget does not know the element, so it renders the text
        inside it -- which is the LaTeX the formula came from.  The formula
        therefore came out with its own source after it, and the label was as
        wide as the source: measured for `\\frac{a}{b}`, 212 px against 22 px,
        and for the quadratic 591 px against 272 px.  The width is the pin.
        """
        reply = ask(r'\frac{a}{b}')
        self.assertNotIn('<annotation', reply['delegate'])
        self.assertNotIn(r'\frac{a}{b}', reply['delegate'],
                         'the LaTeX source is still in what Veusz is given')

        count, box, seen = self.draw(r'\frac{a}{b}', {'katex': True},
                                     name='katex-no-annotation')
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], '', 'Veusz refused it: %s' % seen[0][1])
        self.assertGreater(count, 0)
        self.assertLess(box[0], 40,
                        'the drawing is %d px wide, which is the source being '
                        'drawn as well (a bare fraction is about 22 px at '
                        '150 dpi)' % box[0])

    # -- 4. and Veusz really typesets it -----------------------------------

    def test_03_veusz_itself_typesets_it_without_complaining(self):
        count, _box, seen = self.draw(
            r'x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}',
            {'katex': True}, name='katex-quadratic')
        self.assertEqual(len(seen), 1, 'the native renderer did not run')
        self.assertEqual(seen[0][1], '',
                         'Veusz refused the MathML: %s' % seen[0][1])
        self.assertGreater(count, 0)
        self.assertIn('<mfrac', seen[0][0],
                      'the delegated text is not the formula')

    def test_04_the_spaces_are_the_one_thing_that_needed_fixing(self):
        """`a\\,b`: KaTeX writes a thin space as a whitespace-only <mtext>.

        Qt's widget drops that character and then refuses the element for being
        empty, so the formula came out as Veusz's red error text.  It is
        <mspace> now -- and measured, the widget draws it the same way it draws
        nothing at all, so this is about being understood, not about a gap.
        """
        reply = ask(r'a\,b')
        markup = reply.get('delegate', '')
        self.assertIn('<mspace', markup)
        self.assertNotIn('<mtext> </mtext>', markup)
        for bad in ('<mtext>\u2009</mtext>', '<mtext>\u2005</mtext>'):
            self.assertNotIn(bad, markup)

        count, _box, seen = self.draw(r'a\,b', {'katex': True},
                                      name='katex-space')
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], '', 'the space fix was not enough: %s'
                         % seen[0][1])
        self.assertGreater(count, 0)

    def test_05_display_style_is_katex_deciding(self):
        """Display style is KaTeX's, and it reaches the picture.

        There are two things KaTeX does for it: the `<math display="block">`
        attribute, and -- the one that matters -- big-operator limits become
        `munderover` instead of `msubsup`.  Measured: Qt's MathML widget
        ignores the attribute entirely, and it is the structure that moves the
        limits, so this asserts the structure arrives.  What the widget then
        does with it is the widget's business: the ink *count* of the same
        formula is the same either way, while its box is taller (51 px against
        84 px for a sum, measured on a 12x5 cm page).
        """
        inline = ask(r'\sum_{i=1}^{n} i')
        shown = ask(r'\sum_{i=1}^{n} i', display=True)
        self.assertNotIn('display="block"', inline['delegate'])
        self.assertIn('display="block"', shown['delegate'])
        self.assertIn('msubsup', inline['delegate'])
        self.assertIn('munderover', shown['delegate'])

        for label, settings in (('katex-inline', {'katex': True}),
                                ('katex-display', {'katex': True,
                                                   'katexDisplay': True})):
            count, _box, seen = self.draw(r'\sum_{i=1}^{n} i', settings,
                                          name=label)
            self.assertEqual(len(seen), 1, 'the native renderer did not run')
            self.assertEqual(seen[0][1], '', 'Veusz refused it: %s' % seen[0][1])
            self.assertGreater(count, 0)
            wanted = 'munderover' if 'display' in label else 'msubsup'
            self.assertIn(wanted, seen[0][0],
                          'display style did not reach the delegated MathML')

    # -- 5. when it cannot -------------------------------------------------

    def test_06_a_formula_katex_cannot_parse_is_an_error_formula(self):
        """A typo is drawn, not thrown away.

        KaTeX's parse error becomes a MathML `<merror>`, which Veusz's widget
        typesets where the formula would have been -- in the label's font, with
        the label's box.  That is what the parent project's KaTeX engine did
        too, and it beats a message from the platform: the label keeps working
        and the text says what happened.
        """
        reply = ask(r'x^{')
        markup = reply.get('delegate', '')
        self.assertNotIn('error', reply)
        self.assertIn('<merror', markup)
        self.assertIn('KaTeX could not typeset this', markup)
        # the message may contain markup characters: they have to be escaped
        self.assertNotIn('<mtext>KaTeX parse error: Expected', markup)

        count, _box, seen = self.draw(r'x^{', {'katex': True},
                                      name='katex-broken')
        self.assertEqual(len(seen), 1, 'the native renderer did not run')
        self.assertEqual(seen[0][1], '',
                         'Veusz refused the error formula: %s' % seen[0][1])
        self.assertGreater(count, 0, 'the label came out blank')

    def test_06b_dollar_signs_come_off(self):
        """A label written for another engine often carries `$...$`."""
        for wrapped in ('$x^2$', '$$x^2$$', '  $x^2$  '):
            self.assertEqual(ask(wrapped)['delegate'],
                             ask('x^2')['delegate'],
                             '%r was not unwrapped' % wrapped)
        # ...but a dollar that is not a wrapper stays
        self.assertIn('$', ask('a \\$ b')['delegate'])

    def test_07_an_empty_label_is_left_to_veusz(self):
        self.assertEqual(ask(''), '')


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(
        KatexFeatureTests))
    sys.exit(0 if result.wasSuccessful() else 1)
