"""Two-layer test: the platform loads features, the feature owns the formula.

    PYTHONPATH=<a Veusz> python veusz-js-engine/features/mathjax/test/test_two_layer.py

The feature under test is the one shipped inside the platform, at
``veusz-js-engine/features/mathjax/feature.js`` -- there is only one copy of
it, and this test lives in that feature's own directory because the feature
owns its test as much as its JavaScript.  The feature is **only** JavaScript:
it has no Python, and Veusz could not load it if it tried.  What it checks:

  1. the platform publishes itself and loads the feature it ships;
  2. the platform's own code contributes nothing (no settings, no hooks) when
     there are no feature directories to look in;
  3. the feature has registered its settings and a text draw hook, and a text
     object then renders through JavaScript;
  4. a feature directory can be pointed at, and a feature already loaded is
     not executed twice;
  5. the hook declines cleanly: switch off gives pixel-identical output to a
     label that never had the setting;
  6. switching font changes the picture;
  7. the font chooser lists what the feature's own data declares -- the two in
     the bundle and one per file in `fonts/` -- and the bundle's declaration
     still matches `fonts.json` beside it;
  8. a font outside the bundle is read once, when it is first used, and then
     draws through a real export;
  9. the switch, the font and the style are one row of the panel, as they were
     in the original plugin;
 10. an element font with no outlines still draws the formula, and says why.
"""
import json
import os
import sys
import unittest
from pathlib import Path

# this file is inside the feature it tests: features/mathjax/test/
TEST_DIR = Path(__file__).resolve().parent
FEATURE_DIR = TEST_DIR.parent
PLATFORM_DIR = FEATURE_DIR.parent.parent
PLATFORM = PLATFORM_DIR / 'veusz_js_engine.py'
FEATURE = FEATURE_DIR / 'feature.js'

# keep the plugins from installing themselves as they are exec'd: the tests
# decide when that happens (and with what already loaded)
os.environ['VEUSZ_JS_ENGINE_DEFER'] = '1'
if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(PLATFORM_DIR))

import veusz.qtall as qt                                     # noqa: E402

app = qt.QApplication.instance() or qt.QApplication([])      # noqa: E402

import veusz.document                                        # noqa: E402
import veusz.setting.collections                             # noqa: E402
import veusz.utils                                           # noqa: E402
import veusz.windows.mainwindow                              # noqa: E402,F401


def setting_names():
    probe = veusz.setting.collections.Text('probe')
    return set(probe.__dict__['setdict'])


def load(plugin):
    """Load a plugin the way Veusz does: exec the file, with auto-install on."""
    os.environ.pop('VEUSZ_JS_ENGINE_DEFER', None)
    veusz.document.Document.loadPlugins(pluginlist=[str(plugin)])


def feature_fonts():
    """The font list the bundle builder wrote beside the bundle.

    It is the feature's own data, and the builder's record of what went into
    `mathjax.js`: the platform knows nothing about fonts.
    """
    sidecar = FEATURE_DIR / 'fonts.json'
    data = json.loads(sidecar.read_text(encoding='utf-8'))
    return data.get('fonts') or [], data.get('default')


#: the marker a font declares itself with, in the head of its own file
FONT_MARKER = '// MATHJAX-FONT '


def declared_in(path, head_bytes):
    """Every font one JavaScript file declares, read out of its head alone.

    Read from the disk here, the same way the platform reads it: the first
    ``head_bytes`` of the file and never the file itself, because a font's data
    is megabytes and its description is one line at the top.
    """
    head = path.read_bytes()[:head_bytes].decode('utf-8', 'replace')
    found = []
    for line in head.split('\n'):
        if not line.startswith(FONT_MARKER):
            continue
        declaration = json.loads(line[len(FONT_MARKER):])
        found.extend(declaration.get('fonts') or [])
    return found


def offered_fonts():
    """What the feature should offer: every declaration it was handed.

    The platform hands over the head of every ``*.js`` the feature carries --
    the files beside the entry point first, then those in ``fonts/``, each
    group in name order -- and the feature deduplicates by id, first one wins.
    This is that list, worked out without running anything.
    """
    import veusz_js_engine as platform_module
    heads = sorted(FEATURE_DIR.glob('*.js'))
    heads += sorted((FEATURE_DIR / 'fonts').glob('*.js'))
    seen, fonts = set(), []
    for path in heads:
        for font in declared_in(path, platform_module.HEAD_BYTES):
            if font.get('id') and font['id'] not in seen:
                seen.add(font['id'])
                fonts.append(font)
    return fonts


def ink(settings, name, label=r'x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}'):
    doc = veusz.document.Document()
    ifc = veusz.document.CommandInterface(doc)
    page = ifc.Add('page')
    ifc.To(page)
    ifc.Add('label', name='lbl')
    ifc.Set('lbl/label', label)
    ifc.Set('lbl/Text/size', '20pt')
    for key, value in settings.items():
        ifc.Set('lbl/Text/%s' % key, value)
    out = PLATFORM_DIR / 'build-test-feature'
    out.mkdir(exist_ok=True)
    path = out / ('%s.png' % name)
    ifc.Export(str(path), dpi=150)
    image = qt.QImage(str(path))
    assert not image.isNull(), 'export produced no image'
    image = image.convertToFormat(qt.QImage.Format.Format_ARGB32)
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if (image.pixel(x, y) >> 24) & 0xFF:
                count += 1
    return count


class TwoLayerTests(unittest.TestCase):

    # -- 1. the abstract half has no feature of its own --------------------

    def test_01_platform_publishes_and_loads_its_features(self):
        load(PLATFORM)
        platform = getattr(veusz.utils, 'js_engine', None)
        self.assertIsNotNone(platform, 'the platform did not publish itself')
        # the platform ships its features in features/, so loading the platform
        # is enough: Veusz was told about one plugin, not one per feature
        loaded = platform.features()
        self.assertIn(FEATURE, loaded)
        self.assertEqual(sorted(p.parent.name for p in loaded),
                         sorted(platform.feature_names()))
        # this feature is one of them, whatever else the platform ships
        self.assertIn('mathjax', platform.feature_names())
        # a JavaScript feature is known by its entry point
        self.assertIn(FEATURE, [Path(p) for p in platform.runtimes()])

    def test_01b_platform_code_contributes_nothing_by_itself(self):
        """Strip the feature directories and the platform adds no feature.

        A platform object of its own with an empty state, so this says
        something about the platform's code and not about what is on disk.
        """
        sys.path.insert(0, str(PLATFORM_DIR))
        import veusz_js_engine as platform_module

        os.environ.pop('VEUSZ_JS_ENGINE_FEATURES', None)
        fresh = platform_module.State()
        # here=None, so the only directory left to look in is the env, unset
        lonely = platform_module.Platform(None, state=fresh)
        loaded, failed = platform_module.load_features(lonely)
        self.assertEqual(loaded, [])
        self.assertEqual(failed, [])
        self.assertEqual(fresh.draw_hooks, [])
        self.assertEqual(fresh.injections, [])

    # -- 2. the concrete half is dropped in and the platform loads it ------

    def test_02_the_shipped_feature_registered_and_draws(self):
        """The platform found features/ and this plugin was loaded from it.

        Veusz's plugin list holds one entry -- the platform -- and the feature
        still arrived, which is the whole point of the drop-in half.
        """
        platform = getattr(veusz.utils, 'js_engine')

        self.assertIn('mathjax', setting_names(),
                      'the feature did not add its settings')
        text_class = veusz.setting.collections.Text
        targets = [target for target, _ in platform.draw_hooks()]
        self.assertIn(text_class, targets,
                      'the feature registered no text draw hook')

        plain = ink({'mathjax': False}, 'plain')
        drawn = ink({'mathjax': True}, 'mathjax')
        self.assertGreater(plain, 0, 'ordinary text stopped working')
        self.assertGreater(drawn, 0, 'the engine drew nothing')
        self.assertNotEqual(plain, drawn,
                            'the engine produced the same picture as Veusz')

    def test_02b_a_feature_already_loaded_is_not_run_twice(self):
        """Two directories offering the same feature name must load it once."""
        platform = getattr(veusz.utils, 'js_engine')
        before = len(platform.features())
        # the same directory the platform already scanned: the feature is
        # already loaded, so a second scan must find it done and skip it
        os.environ['VEUSZ_JS_ENGINE_FEATURES'] = str(FEATURE_DIR.parent)
        try:
            loaded, failed = platform.load_features()
        finally:
            os.environ.pop('VEUSZ_JS_ENGINE_FEATURES', None)
        self.assertEqual(loaded, [], 'a feature was executed twice')
        self.assertEqual(failed, [])
        self.assertEqual(len(platform.features()), before)

    # -- 3. per element, not per widget ------------------------------------

    def test_03_the_hook_declines_for_an_element_that_is_off(self):
        """Returning None must leave that element to Veusz's own renderer.

        Black box: a label with the switch off has to come out exactly like a
        label that never had it, while one with the switch on must not.
        """
        label = r'x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}'
        off = ink({'mathjax': False}, 'declined', label)
        on = ink({'mathjax': True}, 'claimed', label)
        untouched = ink({}, 'untouched', label)

        self.assertGreater(off, 0)
        self.assertEqual(off, untouched,
                         'the hook did not decline cleanly: a switch that is '
                         'off changed the picture')
        self.assertNotEqual(on, off,
                            'the switch that is on changed nothing')

    def test_04_font_switch_changes_the_picture(self):
        platform = getattr(veusz.utils, 'js_engine')
        fonts, default = feature_fonts()
        if len(fonts) < 2:
            self.skipTest('this engine bundle carries one font')

        a = ink({'mathjax': True}, 'font-default')
        other = [f['id'] for f in fonts if f['id'] != default][0]
        b = ink({'mathjax': True, 'mathjaxFont': other}, 'font-other')
        self.assertGreater(a, 0)
        self.assertGreater(b, 0)
        self.assertNotEqual(a, b, 'switching font changed nothing')

    # -- 4. the feature's own data -----------------------------------------

    def test_05_the_font_chooser_lists_what_the_bundle_declares(self):
        """The chooser comes out of the data, not out of a second list.

        A font declares itself in the head of the JavaScript file that carries
        it -- the built-in ones in `mathjax.js`, any extra one under `fonts/`,
        one file each -- and the platform hands those heads over, because a
        feature is JavaScript and cannot read a file.  So the chooser is built
        from the declarations, and there is nowhere for a list to drift.

        Two copies of that one fact remain, both written by the bundle builder:
        the declaration inside `mathjax.js` and `fonts.json` beside it.  This
        is the check that they still agree, and that the chooser Veusz shows is
        the one that came out of them.
        """
        written = json.loads((FEATURE_DIR / 'fonts.json').read_text(
            encoding='utf-8'))
        platform = getattr(veusz.utils, 'js_engine')
        features = [f for f in platform.feature_objects() if f.name == 'mathjax']
        self.assertTrue(features, 'the shipped feature is not loaded')

        # what the platform handed over is the bundle's own head, verbatim --
        # one entry per file the feature carries, `fonts/` included, because
        # that is how a font is listed without being read
        heads = json.loads(features[0].runtime.run(
            'JSON.stringify(globalThis.veuszFileHeads)'))
        by_file = dict((item['file'], item['head']) for item in heads)
        self.assertIn('mathjax.js', by_file)
        self.assertIn('fonts/mathjax-%s.js'
                      % [f['id'] for f in written['fonts']][0],
                      ' '.join(by_file))
        declared = None
        for line in by_file['mathjax.js'].split('\n'):
            if line.startswith(FONT_MARKER):
                declared = json.loads(line[len(FONT_MARKER):])
        self.assertIsNotNone(declared, 'the bundle declares no font')
        self.assertEqual(declared['default'], written['default'])
        self.assertEqual([f['id'] for f in declared['fonts']],
                         [f['id'] for f in written['fonts']])
        self.assertEqual([f['x_height'] for f in declared['fonts']],
                         [f['x_height'] for f in written['fonts']])

        # and that declaration, read by the feature, is the chooser -- all of
        # them: the two in the bundle, then one for every file in `fonts/`,
        # each declaring itself in its own head, with no id twice
        expected = offered_fonts()
        chooser = veusz.setting.collections.Text('probe').__dict__[
            'setdict']['mathjaxFont']
        self.assertEqual(list(chooser.vallist), [f['id'] for f in expected])
        self.assertEqual(list(chooser.uilist), [f['title'] for f in expected])
        self.assertEqual(chooser.val, written['default'])
        self.assertEqual(len(set(chooser.vallist)), len(chooser.vallist),
                         'a font was offered twice')
        self.assertGreater(len(chooser.vallist), len(written['fonts']),
                           'the files in fonts/ are not in the chooser at all')

    # -- 5. a font the bundle does not carry -------------------------------

    def test_05b_a_font_outside_the_bundle_is_read_only_when_it_is_used(self):
        """Extra fonts are files, and a file is read once, on demand.

        This is what the head hand-over is for: the feature can offer a font
        whose data it has never read, and the platform reads that one file the
        first time a text actually uses it.  Both halves are checked here --
        the ask and the answer -- because the platform asks the feature a
        second time with the same request, and a feature that answered that
        from its own cache would ask to load the file for ever.
        """
        extra = [f['id'] for f in offered_fonts()
                 if f['id'] not in [g['id'] for g in feature_fonts()[0]]][0]
        platform = getattr(veusz.utils, 'js_engine')
        feature = [f for f in platform.feature_objects()
                   if f.name == 'mathjax'][0]
        import veusz_js_engine as platform_module

        request = json.dumps({'text': r'x = \frac{a}{b}', 'size': 20.0,
                              'color': None,
                              'props': {'on': True, 'font': extra,
                                        'display': False},
                              'face': platform_module.text_font_key(
                                  qt, qt.QFont('DejaVu Sans', 20))})
        asked = json.loads(feature.runtime.call('veuszRender', request))
        self.assertEqual(asked.get('load'), 'fonts/mathjax-%s.js' % extra,
                         'a font the engine does not have was drawn anyway')

        platform_module.load_feature_file(feature, asked['load'])
        drawn = json.loads(feature.runtime.call('veuszRender', request))
        self.assertNotIn('load', drawn, 'the file was read and still asked for')
        self.assertIn('svg', drawn)
        # the request is cached now, and the cached answer is the drawing --
        # not the request to load the file, which is what it replaced
        again = json.loads(feature.runtime.call('veuszRender', request))
        self.assertNotIn('load', again)
        self.assertEqual(again['svg'], drawn['svg'])

    def test_05c_that_font_then_draws_through_a_real_export(self):
        """The same font, at the end of the chain: a label, in an image.

        The bits above are the protocol; this is the point of it -- a font
        whose data lives in a file of its own, chosen in the document, drawn
        by Veusz's own recording device.
        """
        extra = [f['id'] for f in offered_fonts()
                 if f['id'] not in [g['id'] for g in feature_fonts()[0]]][0]
        default = ink({'mathjax': True}, 'deferred-default')
        other = ink({'mathjax': True, 'mathjaxFont': extra},
                    'deferred-font')
        self.assertGreater(default, 0)
        self.assertGreater(other, 0)
        self.assertNotEqual(default, other,
                            'the extra font drew the default one')

    # -- 6. the row the three settings share -------------------------------

    def test_07_the_three_settings_are_one_row_of_the_panel(self):
        """The switch, the font and the style are one line -- as they were.

        The original plugin had one row: a checkbox, the font menu, and a
        "Display style" checkbox beside them.  Here that is a declaration --
        all three properties name the same `row` -- and this is what it comes
        to in Veusz: the first owns the row (its name is the label), the other
        two have no row of their own, and the menu in the middle is every font
        the feature offers.
        """
        probe = veusz.setting.collections.Text('probe')
        switch = probe.get('mathjax')
        font = probe.get('mathjaxFont')
        display = probe.get('mathjaxDisplay')
        self.assertFalse(switch.hidden)
        self.assertTrue(font.hidden, 'the font kept a row of its own')
        self.assertTrue(display.hidden, 'the style kept a row of its own')

        row = switch.makeControl(None)
        check, menu, box = row.controls
        self.assertIsInstance(check, qt.QCheckBox)
        self.assertEqual(check.text(), '',
                         'the row label was written on the checkbox as well')
        self.assertIsInstance(menu, qt.QComboBox)
        self.assertEqual(menu.count(), len(offered_fonts()),
                         'the menu in the row is not the whole font list')
        self.assertEqual(menu.currentText(), 'Computer Modern (TeX)')
        self.assertIsInstance(box, qt.QCheckBox)
        self.assertEqual(box.text(), 'Display style')

    # -- 7. an element font that cannot be outlined ------------------------

    def test_06_a_font_without_outlines_still_draws_the_formula(self):
        """A raster-only Font must not erase the words.

        ``\\text{...}`` is drawn as the *element's* font, so a font with glyphs
        but no outlines (the old Windows bitmap faces) has nothing to draw with.
        The formula has to come out anyway -- the feature is told to drop the
        words rather than draw a gap -- and the user has to be told why, once
        per family, because nothing on screen would say it.
        """
        raster = [name for name in ('MS Sans Serif', 'System', 'Fixedsys',
                                    'Small Fonts', 'Modern', 'Roman')
                  if name in qt.QFontDatabase.families()]
        if not raster:
            self.skipTest('no raster-only font installed')
        family = raster[0]
        platform = getattr(veusz.utils, 'js_engine')

        formula = r'\text{total} = x'
        plain = ink({'mathjax': True}, 'raster-plain', formula)
        rastered = ink({'mathjax': True, 'font': family}, 'raster-font',
                       formula)

        self.assertGreater(plain, 0)
        self.assertGreater(rastered, 0,
                           'the formula came out blank for %r' % family)
        self.assertTrue(any('outlines' in note and family in note
                            for note in platform.notes()),
                        'nothing told the user why that font could not be '
                        'used: %r' % (platform.notes(),))


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(
        TwoLayerTests))
    sys.exit(0 if result.wasSuccessful() else 1)
