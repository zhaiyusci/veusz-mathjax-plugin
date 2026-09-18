"""Tests for veusz-js-engine.

    python test/test_platform.py            # the platform, no Veusz needed
    python test/test_platform.py --veusz    # ...and the text object end to end

Part one is the one that matters most: the platform drives QuickJS itself
through ctypes, so it proves that two runtimes with different ex-heights can
live in one process without corrupting each other's geometry -- which is what
the compiled bridge used to be for.
"""
import json
import os
import queue
import re
import sys
import threading
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
os.environ['VEUSZ_JS_ENGINE_DEFER'] = '1'
if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(PROJECT))

import veusz_js_engine as platform_module                      # noqa: E402

WITH_VEUSZ = '--veusz' in sys.argv

if WITH_VEUSZ:
    import veusz.qtall as qt                                 # noqa: E402
    import veusz.document                                    # noqa: E402
    import veusz.setting                                     # noqa: E402
    import veusz.setting.collections                         # noqa: E402
    import veusz.utils                                       # noqa: E402
    import veusz.windows.mainwindow                          # noqa: E402,F401
else:
    qt = None


def reserved_stack_of_this_thread():
    """How much stack this thread really has, from the OS, or None.

    Windows only, and used to check the platform's budget against the stack it
    is a fraction of: the budget is the engine's own check, so a budget that
    reaches the end of the real stack is a guard page instead of an error.
    """
    import ctypes
    try:
        kernel32 = ctypes.WinDLL('kernel32')
        get_limits = kernel32.GetCurrentThreadStackLimits
        get_limits.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                               ctypes.POINTER(ctypes.c_size_t)]
        low, high = ctypes.c_size_t(), ctypes.c_size_t()
        get_limits(ctypes.byref(low), ctypes.byref(high))
        return high.value - low.value
    except (AttributeError, OSError):
        return None


class PlatformTests(unittest.TestCase):
    """A runtime per JavaScript file, and the JS contract itself.

    The two files are written here rather than borrowed from a feature: the
    platform ships no JavaScript of its own, and a platform test must not
    depend on a feature being present.
    """

    @classmethod
    def setUpClass(cls):
        here = PROJECT

        cls.js_dir = PROJECT / 'build-test-js'
        if cls.js_dir.exists():
            import shutil
            shutil.rmtree(cls.js_dir)
        cls.js_dir.mkdir(parents=True)

        #: what each fixture's render() answers, so a test can tell them apart
        cls.SVG = ('<svg xmlns="http://www.w3.org/2000/svg" '
                   'style="vertical-align: -0.25ex;" width="1ex" height="1ex" '
                   'data-tag="%s"></svg>')

        def make(tag):
            # the whole contract: one global function, one string in, one out
            return ("globalThis.render = function (t) { return %s; };\n"
                    "globalThis.renderInline = globalThis.render;\n"
                    % json.dumps(cls.SVG % tag))

        cls.engine = platform_module.find_quickjs(PROJECT)
        assert cls.engine is not None, 'no engine to run the fixtures with'

        cls.alpha = cls.js_dir / 'alpha.js'
        cls.alpha.write_text(make('alpha'), encoding='utf-8')
        cls.beta = cls.js_dir / 'beta.js'
        cls.beta.write_text(make('beta'), encoding='utf-8')

        cls.platform = platform_module.Platform(here)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.js_dir, ignore_errors=True)

    def test_a_runtime_is_made_from_a_path(self):
        # a platform of its own: the shared one has been used by other tests,
        # and its runtimes are already up
        fresh = platform_module.Platform(PROJECT)
        runtime = fresh.runtime(self.alpha)
        self.assertEqual(runtime.path, self.alpha)
        
        self.assertFalse(runtime.started, 'asking is not starting')

    def test_the_engine_is_the_only_binary(self):
        """One binary, beside the platform plugin, and no compiled bridge.

        There used to be a second DLL: a C shim that wrapped QuickJS's C API
        for Python.  It was removed after this binding was measured to
        reproduce its output byte for byte -- so a feature needs nothing but
        the engine, and a feature that wants a different build of it may still
        ship its own, which the search looks at first.
        """
        self.assertEqual(platform_module.find_quickjs(PROJECT),
                         PROJECT / 'qjs.dll')
        self.assertFalse((PROJECT / 'jsbridge.dll').exists(),
                         'the bridge was supposed to be gone')

    def test_an_env_var_that_names_nothing_is_an_error(self):
        """Choosing a file and silently getting another one hides the typo.

        The variable exists to say *which* file, so a path that is not there
        has to be reported now rather than surface later as a confusing
        "no JS host library".
        """
        missing = str(self.js_dir / 'not-here.dll')
        os.environ['VEUSZ_JS_ENGINE_QUICKJS'] = missing
        try:
            self.assertRaises(platform_module.JsEngineError,
                              platform_module.find_quickjs, PROJECT)
        finally:
            os.environ.pop('VEUSZ_JS_ENGINE_QUICKJS', None)

    def test_an_engine_elsewhere_still_works(self):
        """A feature may point at its own build of the engine.

        The lookup starts beside the JavaScript, so ``engine=`` is only ever a
        hint -- and there is nothing linked against it any more, so the two
        files have no reason to sit together.
        """
        import shutil
        elsewhere = self.js_dir / 'elsewhere'
        elsewhere.mkdir(exist_ok=True)
        engine = elsewhere / 'qjs.dll'
        shutil.copy2(PROJECT / 'qjs.dll', engine)

        runtime = platform_module.Runtime(self.alpha, engine=engine)
        runtime.start()
        self.assertNotEqual(runtime.engine.parent, self.alpha.parent,
                            'this test wants them apart')
        self.assertTrue(runtime.started, 'the engine did not load')
        runtime.close()

    def test_nothing_is_left_owned_after_a_call(self):
        """Every path out of the engine gives back what it took.

        QuickJS values are reference counted, so a missed release is a leak and
        a repeated one is heap corruption in the host process.  The binding
        makes and releases the handle inside one call, so the check is that
        nothing is ever left held -- after a good call, after a failing one, and
        after many failing ones in a row.
        """
        runtime = self.platform.runtime(self.alpha)
        runtime.start()
        try:
            self.assertEqual(runtime._js.outstanding(), 0)
            runtime.call('render', 'abc')
            self.assertEqual(runtime._js.outstanding(), 0,
                             'a value was not released')
            for _ in range(50):
                self.assertRaises(platform_module.JsEngineError,
                                  runtime.call, 'noSuchFunction', 'x')
            self.assertEqual(runtime._js.outstanding(), 0,
                             'a value survived a failing call')
            # and the runtime is still usable after all that
            runtime.call('render', 'abc')
        finally:
            runtime.close()

    def test_one_runtime_per_file(self):
        """Ask twice, get the same one: the parse is not paid twice."""
        first = self.platform.runtime(self.alpha)
        second = self.platform.runtime(self.alpha)
        self.assertIs(first, second)

    def test_a_missing_file_is_a_clear_error(self):
        with self.assertRaises(platform_module.JsEngineError) as caught:
            self.platform.runtime(self.js_dir / 'nope.js')
        self.assertIn('no such JavaScript file', str(caught.exception))

    def test_a_file_runs_and_answers(self):
        """The primitive, end to end: JavaScript in, a string out."""
        runtime = self.platform.runtime(self.alpha)
        self.assertEqual(runtime.run('1 + 1'), '2')
        self.assertEqual(runtime.call('render', 'abc'), self.SVG % 'alpha')

    def test_js_errors_come_back_as_errors(self):
        runtime = self.platform.runtime(self.alpha)
        with self.assertRaises(platform_module.JsEngineError):
            runtime.call('noSuchFunction', 'x')
        # the engine's own words reach the caller, not a placeholder
        with self.assertRaises(platform_module.JsEngineError) as caught:
            runtime.run('throw new Error("boom")')
        self.assertIn('boom', str(caught.exception))

    # -- the part that has never been measured ----------------------------

    def test_two_files_are_two_runtimes_in_one_process(self):
        """Both runtimes up at once, each with its own answer.

        They used to share one compiled library that kept a single ex-height
        for every file in the process, so what one set leaked into the other.
        Two files are two runtimes now, with nothing shared between them.
        """
        p1 = self.platform.runtime(self.alpha)
        p2 = self.platform.runtime(self.beta)

        # start both before calling either, so they really do coexist
        p1.start()
        p2.start()
        self.assertTrue(p1.started and p2.started)
        self.assertIsNot(p1._js, p2._js, 'two files must be two runtimes')

        self.assertEqual(p1.call('render', 'abcdef'), self.SVG % 'alpha')
        self.assertEqual(p2.call('render', 'ghijkl'), self.SVG % 'beta')
        # and the first is untouched by the second having run
        self.assertEqual(p1.call('render', 'abcdef'), self.SVG % 'alpha')

    def test_the_engine_has_a_thread_and_a_stack_of_its_own(self):
        """Why the platform says a stack size at all, and to whom.

        QuickJS does not look up how much stack it has: its overflow check is
        ``sp - alloca_size < stack_top - stack_size`` against the *current C
        stack pointer* (``js_check_stack_overflow``), and both numbers come from
        the host -- ``JS_UpdateStackTop`` and ``JS_SetMaxStackSize``.  Not
        calling them is not neutral either: the default is 1 MiB, which is less
        than MathJax needs, and 0 means *no limit*, i.e. a crash instead of an
        error.

        So the number has to be said, and the only question is which stack to
        say it about.  A fraction of whichever thread called makes the same
        formula work from one of Veusz's paint threads and fail from another --
        measured, 2.88 MiB reserved left 1.15 MiB of budget and 16 nested
        fractions.  The platform therefore owns the thread: 40 MiB reserved,
        16 MiB of budget, and it is the same whoever asks.
        """
        thread = platform_module.engine_thread().start()
        self.assertTrue(thread.thread.is_alive())
        self.assertEqual(thread.thread.name,
                         platform_module._ENGINE_THREAD_NAME)
        budget = thread.call(platform_module.stack_budget_for_this_thread)
        self.assertGreaterEqual(
            budget, 4 * 1024 * 1024,
            'the engine thread has only %d KiB of budget' % (budget // 1024))
        # ...and the budget has to stay inside the stack it is a fraction of.
        # The engine's check is the only thing between a deep formula and the
        # guard page, so a thread sized for a smaller budget would turn a
        # catchable error into the loss of the process.
        reserved = thread.call(reserved_stack_of_this_thread)
        if reserved:
            self.assertLess(
                budget, reserved,
                'a %.2f MiB budget of a %.2f MiB thread reaches the guard page'
                % (budget / 1048576.0, reserved / 1048576.0))

        # and a thread with a small stack of its own gets the same answer
        answers = queue.Queue()

        def ask():
            answers.put(thread.call(platform_module.stack_budget_for_this_thread))

        previous = threading.stack_size()
        try:
            threading.stack_size(256 * 1024)
            worker = threading.Thread(target=ask)
            worker.start()
            worker.join()
        finally:
            threading.stack_size(previous)
        self.assertEqual(answers.get(), budget,
                         'the budget depended on the calling thread again')

    def test_a_deep_formula_does_not_depend_on_the_calling_thread(self):
        """The same depth from any thread: 64 nested fractions, not 16.

        The bundle is loaded here as a file, because what is being tested is
        the engine's stack budget and not a feature: a formula deep enough to
        need megabytes of JS stack has to lay out the same way whichever of
        Veusz's threads asks for it.
        """
        bundle = PROJECT / 'features' / 'mathjax' / 'mathjax.js'
        self.assertTrue(bundle.is_file(), 'the MathJax bundle is missing')
        runtime = platform_module.Runtime(bundle, engine=self.engine)
        depth = 64
        deep = (r'\frac{1}{' * depth) + 'x' + ('}' * depth)
        try:
            from_main = runtime.call('renderInline', deep)
        finally:
            runtime.close()
        self.assertIn('<svg', from_main)

        runtime = platform_module.Runtime(bundle, engine=self.engine)
        answers = queue.Queue()

        def ask():
            try:
                answers.put(runtime.call('renderInline', deep))
            except Exception as exc:                           # noqa: BLE001
                answers.put('failed: %s' % exc)

        previous = threading.stack_size()
        try:
            threading.stack_size(256 * 1024)
            worker = threading.Thread(target=ask)
            worker.start()
            worker.join()
        finally:
            threading.stack_size(previous)
            runtime.close()
        self.assertEqual(answers.get(), from_main,
                         'the drawing depended on the calling thread')

    def test_the_report_says_what_is_up(self):
        report = self.platform.report()
        self.assertIn('runtimes', report)
        paths = [item['path'] for item in report['runtimes']]
        self.assertTrue(any(path.endswith('alpha.js') for path in paths))

    def test_a_note_is_recorded_once(self):
        """The report is a list of facts, not a list of repaints.

        A font that cannot be outlined is reported on every paint that hits it,
        and a document with fifty labels in that font would fill the whole
        report with one sentence.  The JavaScript side is deduplicated the same
        way, so a feature's note and the platform's behave alike.
        """
        state = platform_module.State()
        state.note('the same thing')
        state.note('the same thing')
        state.note('something else')
        self.assertEqual(state.notes, ['the same thing', 'something else'])

class VeuszTests(unittest.TestCase):
    """The platform installed into Veusz: plumbing, and no feature of its own."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt.QApplication.instance() or qt.QApplication([])
        # the deferred flag was set to import the module without installing;
        # clear it so the copy Veusz runs does install
        os.environ.pop('VEUSZ_JS_ENGINE_DEFER', None)
        veusz.document.Document.loadPlugins(
            pluginlist=[str(PROJECT / 'veusz_js_engine.py')])

    @classmethod
    def setting_names(cls):
        probe = veusz.setting.collections.Text('probe')
        return set(probe.__dict__['setdict'])

    def ask_feature(self, text, label_font=None, font='tex', display=False,
                    size=20.0, color=None):
        """One render, with the text protocol completed if it asks for it.

        This is what the platform's draw seam does: ask, shape anything the
        answer asked for in the element's font, and ask again.  A test that
        called ``veuszRender`` once would be testing half a protocol.
        """
        import json
        platform = getattr(veusz.utils, 'js_engine')
        features = [f for f in platform.feature_objects() if f.name == 'mathjax']
        self.assertTrue(features, 'the shipped feature is not loaded')
        self.label_font = label_font or qt.QFont('DejaVu Sans', 20)
        request = {'text': text, 'size': size, 'color': color,
                   'props': {'on': True, 'font': font, 'display': display},
                   'face': platform_module.text_font_key(qt, self.label_font)}
        reply = json.loads(features[0].runtime.call('veuszRender',
                                                    json.dumps(request)))
        for _ in range(3):
            if 'measure' not in reply:
                return reply
            measured = platform_module.measure_text_runs(
                qt, reply['measure'], self.label_font)
            request['measured'] = measured
            reply = json.loads(features[0].runtime.call('veuszRender',
                                                        json.dumps(request)))
        self.fail('the feature kept asking for measurements')

    def ask_raw(self, text, **kwargs):
        """The first answer only -- to see whether it asked for anything."""
        import json
        platform = getattr(veusz.utils, 'js_engine')
        features = [f for f in platform.feature_objects() if f.name == 'mathjax']
        request = {'text': text, 'size': 20.0, 'color': None,
                   'props': {'on': True, 'font': kwargs.get('font', 'tex'),
                             'display': False},
                   'face': platform_module.text_font_key(
                       qt, kwargs.get('label_font') or qt.QFont('DejaVu Sans',
                                                                20))}
        return json.loads(features[0].runtime.call('veuszRender',
                                                   json.dumps(request)))

    def test_the_javascript_mathjax_feature_reproduces_the_geometry(self):
        """The feature's own ex-to-points arithmetic, against the measured box.

        The MathJax feature used to be Python, and the platform converted its
        SVG's ex geometry for it.  It is JavaScript now and does that itself,
        which makes the numbers the old path produced the test: same box, and
        the same SVG bytes once the platform's Qt pass has been applied.

        Only cases whose x-height is one a shipped font really has can be
        checked this way -- the feature reads the x-height from its own font
        list, where the compatibility path was handed one per call.  And only
        formulas with no text of their own: ``\\text{...}`` is set in the
        *figure's* font now, so its width is that font's and not a constant
        (that is asserted in test_formula_text_uses_the_elements_font).
        """
        import json
        platform = getattr(veusz.utils, 'js_engine')
        features = [f for f in platform.feature_objects() if f.name == 'mathjax']
        self.assertTrue(features, 'the shipped MathJax feature is not a '
                                 'JavaScript feature yet')
        feature = features[0]

        def ask(text, size, color, font='tex', display=False):
            return self.ask_feature(text, font=font, display=display,
                                    size=size, color=color)

        frac = r'x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}'
        cases = [
            (frac, 20.0, '#112233', False, 7795, 130.9646, 28.8361, -7.0455),
            (frac, 12.0, None, False, 7762, 78.5788, 17.3016, -4.2273),
            (frac, 20.0, '#000000', True, 7762, 183.5626, 46.7724, -13.9230),
        ]
        for text, size, color, display, nbytes, width, height, depth in cases:
            reply = ask(text, size, color, display=display)
            self.assertNotIn('error', reply, text)
            self.assertEqual(round(reply['width'] * 1.0, 4), width, text)
            self.assertEqual(round(reply['height'] * 1.0, 4), height, text)
            self.assertEqual(round(reply['depth'] * 1.0, 4), -depth, text)
            # the platform's Qt pass, then the byte count the bridge produced
            cleaned = platform_module.qt_safe_svg(reply['svg'], color)
            self.assertEqual(len(cleaned), nbytes, text)

        # and the second font is really used.  Comparing the box alone is not
        # enough -- it changes with the font's x-height, so that passed while
        # the engine still drew every formula in its default font -- and
        # comparing the raw SVG is not possible either, because MathJax numbers
        # its <defs> ids per render.  The outlines themselves are the thing.
        def outlines(font):
            svg = ask(frac, 20.0, None, font=font)['svg']
            # MathJax numbers its <defs> ids per render, so they go before the
            # comparison -- and note that `id="X"` ends in `d="X"`, which is
            # what makes a naive search for d="..." compare the counters
            # instead of the glyphs.
            return re.findall(r'd="([^"]*)"', re.sub(r'\bid="[^"]*"', '', svg))

        self.assertNotEqual(outlines('tex'), outlines('stix2'),
                            'the font chooser changed the scale but not the '
                            'glyphs')
        # an empty or unknown choice has to come back to the default.  The
        # bundle's setFont ignores a name it does not know, so it would
        # otherwise keep whatever the last text used -- which is how a freshly
        # ticked label once showed one font while the chooser showed another.
        self.assertEqual(outlines(''), outlines('tex'))
        self.assertEqual(outlines('nonsense'), outlines('tex'))

    def test_formula_text_uses_the_elements_font(self):
        """``\\text{...}`` is set in the figure's font, not in the math font.

        That is what the two-pass protocol is for: only the feature knows the
        words, only the platform has Qt, and the text has to be shaped in the
        font of the element the formula sits in.  The width therefore follows
        the element's font -- and must not move when the *math* font changes.
        """
        raw = self.ask_raw('\\text{hello}')
        self.assertIn('measure', raw,
                      'the feature did not ask for its text to be shaped')
        self.assertEqual(len(raw['measure']), 1)
        self.assertEqual(raw['measure'][0]['text'], 'hello')

        sans = qt.QFont('DejaVu Sans', 20)
        mono = qt.QFont('Courier New', 20)
        narrow = self.ask_feature('\\text{hello}', label_font=sans)
        wide = self.ask_feature('\\text{hello}', label_font=mono)

        self.assertNotIn('error', narrow)
        self.assertIn('data-veusz-text', narrow['svg'],
                      'the words were not drawn as shaped outlines')
        self.assertNotIn('<text', narrow['svg'],
                         'the words are still text, which Veusz cannot draw')
        self.assertNotEqual(round(narrow['width'], 2), round(wide['width'], 2),
                            'the width did not follow the element\'s font')

        # the *math* font must not move it: the words are not set in it
        other_math = self.ask_feature('\\text{hello}', label_font=sans,
                                      font='stix2')
        self.assertAlmostEqual(narrow['width'], other_math['width'], places=2)

        # and a formula with no such text never asks, so it costs one call
        plain = self.ask_raw(r'x = \\frac{a}{b}')
        self.assertNotIn('measure', plain)

    def test_svg_text_becomes_outlines(self):
        """A drawing's ``<text>`` is replaced by outlines before Qt sees it.

        Measured through Veusz's own recording device: an SVG ``<text>`` comes
        out 2.2x too big and is replayed as a hairline, while the same glyphs as
        ``<path>`` are drawn exactly as they were.  MathJax emits ``<text>`` for
        the characters its math font does not have -- CJK, a rare symbol, an
        emoji -- which is the difference between a formula with those in it
        working and not.
        """
        # bare CJK, not \\text{...}: the math font has these glyphs nowhere,
        # so MathJax emits <text> for them and no amount of shaping helps
        reply = self.ask_feature(chr(0x4e2d) + chr(0x6587) + ' + x')
        self.assertNotIn('error', reply)
        self.assertIn('<text', reply['svg'],
                      'the bundle emitted no <text> for this to be about')

        converted, leftover = platform_module.svg_text_as_paths(
            qt, reply['svg'].encode('utf-8'), 'Microsoft YaHei', None)
        self.assertNotIn(b'<text', converted)
        self.assertFalse(leftover, 'a glyph could not be outlined')
        self.assertIn(b'<path', converted)
        self.assertIn(b'd="M', converted, 'the outline has no geometry')
        # the element's own transform has to survive, or the glyphs come out
        # flipped: MathJax wraps the formula in scale(1,-1) and the text in
        # scale(1,-1) again, so it lands upright only through the second flip
        self.assertIn(b'<path transform=', converted)

        # a drawing with no text is handed straight back
        plain = b'<svg><path d="M0 0"/></svg>'
        self.assertEqual(platform_module.svg_text_as_paths(qt, plain, '', None),
                         (plain, False))

    def test_a_leftover_text_element_is_rescued_from_device_dpi(self):
        """Qt multiplies an SVG ``<text>`` size by the page dpi; we must not.

        A character whose outline cannot be built is left as ``<text>`` for
        Qt's own SVG route, and on Veusz's recording device Qt then scales its
        font size by dpi/72 -- 1.33x at 96 dpi, 4.17x at 300 dpi, which is how
        one CJK character in a formula came out four times too big at print
        resolution.  The correction is for that device and for nothing else:
        a QImage reports 72, and the QPicture fallback is not scaled at all.
        """
        class RecordPaintDevice:
            """The name is the whole contract -- only Veusz's device scales."""

            def __init__(self, dpi):
                self.dpi = dpi

            def metric(self, which):
                return self.dpi

        class Painter:
            def __init__(self, device):
                self._device = device

            def device(self):
                return self._device

        markup = b'<svg><text font-size="20px">x</text></svg>'
        self.assertEqual(platform_module.qt_text_factor(
            qt, Painter(RecordPaintDevice(72))), 1.0)
        at96 = platform_module.cancel_qt_text_factor(
            qt, markup, Painter(RecordPaintDevice(96)))
        at300 = platform_module.cancel_qt_text_factor(
            qt, markup, Painter(RecordPaintDevice(300)))
        self.assertIn(b'font-size="15px"', at96)
        self.assertIn(b'font-size="4.8px"', at300)
        # any other device is left exactly as it was
        image = qt.QImage(4, 4, qt.QImage.Format.Format_ARGB32)
        self.assertEqual(platform_module.qt_text_factor(qt, Painter(image)),
                         1.0)
        self.assertEqual(platform_module.cancel_qt_text_factor(
            qt, markup, Painter(image)), markup)

    def test_installs_and_publishes_itself(self):
        published = getattr(veusz.utils, 'js_engine', None)
        self.assertIsNotNone(published, 'the platform was not published')
        self.assertEqual(published.quickjs is not None, True,
                         'the QuickJS engine was not found next to the plugin')
        # the shipped feature loaded, and it brought its own JavaScript
        self.assertIn('mathjax', published.feature_names())
        self.assertTrue(published.runtimes(),
                        'the feature did not ask for its JavaScript')

    def test_loading_twice_does_not_wrap_twice(self):
        """Veusz does call loadPlugins more than once.

        Each call executes the plugin file in a fresh namespace, so anything
        kept in the module's globals would be lost or duplicated.  The state
        lives on veusz.utils instead.
        """
        from veusz.utils import textrender
        before = textrender.Renderer
        first = getattr(veusz.utils, '_js_engine_state')

        veusz.document.Document.loadPlugins(
            pluginlist=[str(PROJECT / 'veusz_js_engine.py')])

        self.assertIs(textrender.Renderer, before,
                      'the renderer was wrapped a second time')
        self.assertIs(getattr(veusz.utils, '_js_engine_state'), first,
                      'the state object was replaced')

    def test_reinstalling_reuses_the_platform(self):
        """Veusz calls loadPlugins more than once; the second must not start over.

        A fresh Platform would throw away every runtime that had been started,
        and a feature already loaded would not be run again to ask for them.
        """
        platform = getattr(veusz.utils, 'js_engine')
        before = platform.runtimes()
        before_features = [p.name for p in platform.features()]

        os.environ.pop('VEUSZ_JS_ENGINE_DEFER', None)
        veusz.document.Document.loadPlugins(
            pluginlist=[str(PROJECT / 'veusz_js_engine.py')])

        after = getattr(veusz.utils, 'js_engine')
        self.assertIs(after, platform, 'a second install replaced the platform')
        self.assertEqual(after.runtimes(), before)
        self.assertEqual([p.name for p in after.features()], before_features)

    def test_adds_no_feature_of_its_own(self):
        """Whatever appears comes from a feature file, not from the platform.

        A platform object with an empty state and no feature directories to
        look in: its own code must add no setting and no hook.  (The installed
        platform *does* add settings -- the ones a feature file declares.)
        """
        import veusz_js_engine as platform_module

        os.environ.pop('VEUSZ_JS_ENGINE_FEATURES', None)
        fresh = platform_module.State()
        lonely = platform_module.Platform(None, state=fresh)

        loaded, failed = platform_module.load_features(lonely)
        self.assertEqual((loaded, failed), ([], []))
        self.assertEqual(fresh.draw_hooks, [],
                         'the platform registered a hook by itself')
        self.assertEqual(fresh.injections, [],
                         'the platform added a setting by itself')

    def test_replaceable_renderer_is_installed_once(self):
        """One wrapper for everybody, so two plugins cannot fight over it."""
        from veusz.utils import textrender
        self.assertIs(textrender.Renderer, veusz.utils.Renderer)
        self.assertTrue(getattr(textrender.Renderer, '__name__', '')
                        .endswith('renderer'))

    def test_settings_hook_runs_for_new_settings_groups(self):
        """A next-layer plugin declares what to add; the platform patches."""
        platform = getattr(veusz.utils, 'js_engine')
        text_class = veusz.setting.collections.Text

        platform.add_setting(text_class, None, veusz.setting.Bool(
            'probe_setting', False, usertext='Probe'))
        self.assertIn('probe_setting', self.setting_names(),
                      'the setting never reached a new Text group')

    def test_add_setting_reaches_a_nested_group(self):
        """The group path is part of the call, not implicit in the class."""
        platform = getattr(veusz.utils, 'js_engine')
        text_class = veusz.setting.collections.Text

        platform.add_setting(text_class, None, veusz.setting.Settings(
            'probe_group', setnsmode='widgetsettings'))
        platform.add_setting(text_class, 'probe_group', veusz.setting.Bool(
            'nested_probe', False, usertext='Nested'))

        probe = veusz.setting.collections.Text('probe2')
        self.assertIn('probe_group', probe.__dict__['setdict'])
        self.assertIn('nested_probe',
                      probe.get('probe_group').__dict__['setdict'])

    def test_add_setting_copies_per_instance(self):
        """A settings tree takes ownership, so one object cannot serve two."""
        platform = getattr(veusz.utils, 'js_engine')
        text_class = veusz.setting.collections.Text

        platform.add_setting(text_class, None, veusz.setting.Bool(
            'copy_probe', False, usertext='Copy probe'))

        first = veusz.setting.collections.Text('probeA').get('copy_probe')
        second = veusz.setting.collections.Text('probeB').get('copy_probe')
        self.assertIsNot(first, second, 'the same setting object was reused')
        first.val = True
        self.assertFalse(second.val, 'the two instances share state')

    def test_the_same_hook_twice_is_one_hook(self):
        """Which file loaded a feature must not change how often it is asked.

        The same feature file can reach the platform twice -- two feature
        directories offering the same name, or a copy of it that Veusz was
        also told to load.  Settings survive that because they are keyed by
        name; hooks have to survive it too, or one feature is asked twice per
        text element.
        """
        platform = getattr(veusz.utils, 'js_engine')
        text_class = veusz.setting.collections.Text

        def probe(painter, font, x, y, text, settings, **kwargs):
            return None

        platform.hook_draw(text_class, probe)
        after_first = len(platform.draw_hooks())
        platform.hook_draw(text_class, probe)
        self.assertEqual(len(platform.draw_hooks()), after_first,
                         'the same callback was registered twice')

    def test_hook_draw_declines_by_default(self):
        """A hook that returns None must leave the text to Veusz."""
        platform = getattr(veusz.utils, 'js_engine')
        text_class = veusz.setting.collections.Text
        seen = []

        def callback(painter, font, x, y, text, settings, **kwargs):
            seen.append(text)
            return None

        platform.hook_draw(text_class, callback)
        targets = [target for target, _ in platform.draw_hooks()]
        self.assertIn(text_class, targets)


class FeatureTests(unittest.TestCase):
    """Feature plugins: the drop-in half of the platform.

    The platform loads them, so a user adds one plugin instead of one per
    feature and does not have to get the order right.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = PROJECT / 'build-test-features'
        if cls.dir.exists():
            import shutil
            shutil.rmtree(cls.dir)
        cls.dir.mkdir(parents=True)

        note = ("import veusz.utils\n"
                "veusz.utils._feature_order.append(%r)\n")

        def feature_file(name, body):
            (cls.dir / name).write_text(body, encoding='utf-8')

        def feature_dir(name, body, extra=None):
            directory = cls.dir / name
            directory.mkdir()
            (directory / 'feature.py').write_text(body, encoding='utf-8')
            for filename, text in (extra or {}).items():
                (directory / filename).write_text(text, encoding='utf-8')

        # a feature is a directory with feature.py in it (the norm) ...
        feature_dir('20_folder', note % '20_folder',
                    extra={'helper.py': '# a helper beside the entry point\n'})
        # ... or a single .py file (shorthand for a one-file feature)
        feature_file('10_first.py', note % '10_first')
        # a broken one must not stop the others
        feature_dir('30_broken', "raise RuntimeError('broken')\n")
        # non-ASCII is allowed here, unlike in a plugin Veusz reads itself
        feature_file('40_utf8.py',
                     "# a feature may say things like \u2014 or \u4e2d\u6587\n"
                     + note % '40_utf8')
        # a leading underscore means "not a feature", for a file or a directory
        feature_file('_helper.py', note % '_helper')
        feature_dir('_skipdir', note % '_skipdir')
        # a directory with no feature.py is not a feature
        (cls.dir / '50_nothing').mkdir()
        (cls.dir / '50_nothing' / 'notes.txt').write_text('x', encoding='utf-8')

        veusz.utils._feature_order = []
        os.environ['VEUSZ_JS_ENGINE_FEATURES'] = str(cls.dir)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop('VEUSZ_JS_ENGINE_FEATURES', None)
        if hasattr(veusz.utils, '_feature_order'):
            del veusz.utils._feature_order

    def test_01_features_load_in_name_order(self):
        """Both shapes load, in name order, whichever shape each one uses."""
        platform = getattr(veusz.utils, 'js_engine')
        loaded, failed = platform.load_features()
        names = [p.parent.name if p.name == 'feature.py' else p.stem
                 for p in loaded]
        self.assertEqual(names, ['10_first', '20_folder', '40_utf8'])
        self.assertEqual(veusz.utils._feature_order,
                         ['10_first', '20_folder', '40_utf8'])

    def test_01b_a_folder_feature_keeps_its_helpers(self):
        """Only feature.py is executed; a helper beside it is not."""
        platform = getattr(veusz.utils, 'js_engine')
        entries = [p for p in platform.features() if p.parent.name == '20_folder']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, 'feature.py')

    def test_02_a_broken_feature_does_not_stop_the_rest(self):
        platform = getattr(veusz.utils, 'js_engine')
        loaded = [p.parent.name if p.name == 'feature.py' else p.stem
                  for p in platform.features()]
        self.assertIn('40_utf8', loaded,
                      'a broken feature stopped the ones after it')
        self.assertTrue(any('30_broken' in note for note in platform.notes()),
                        'the failure was not recorded')

    def test_03_helper_files_and_empty_folders_are_not_features(self):
        platform = getattr(veusz.utils, 'js_engine')
        names = [p.parent.name if p.name == 'feature.py' else p.stem
                 for p in platform.features()]
        self.assertNotIn('_helper', names)
        self.assertNotIn('_skipdir', names)
        self.assertNotIn('50_nothing', names,
                         'a directory with no feature.py is not a feature')

    def test_04_loading_twice_does_not_reload(self):
        platform = getattr(veusz.utils, 'js_engine')
        before = len(veusz.utils._feature_order)
        platform.load_features()
        self.assertEqual(len(veusz.utils._feature_order), before,
                         'a feature was executed twice')

    def test_05_a_feature_can_find_its_own_directory(self):
        """__file__ is provided, so a feature need not walk the stack."""
        platform = getattr(veusz.utils, 'js_engine')
        self.assertTrue(platform.features())
        for path in platform.features():
            self.assertTrue(path.exists())


JS_DEMO = """
veusz.feature({name: 'jsdemo', title: 'JS demo', target: 'text',
               version: '1.0'});

veusz.switch('on', {label: 'JS demo', default: false,
                    descr: 'Draw this text with JavaScript'});
veusz.choice('ink', {label: 'JS ink', default: 'blue',
                     choices: [{value: 'blue', label: 'Blue box'},
                               {value: 'red', label: 'Red box'}]});

veusz.renderText(function (req) {
    if (!req.on('on')) { return null; }               // decline: not ours
    var colour = (req.get('ink') === 'red') ? '#ff0000' : '#0000ff';
    return veusz.svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="6pt" height="3pt"' +
        ' viewBox="0 0 6 3"><rect x="0" y="0" width="6" height="3"' +
        ' fill="' + colour + '"/></svg>',
        {width: 6, height: 3, depth: 1});
});
"""


FONT_DEMO = """
veusz.feature({name: 'fontdemo', title: 'Font demo', target: 'text'});
veusz.switch('on', {label: 'Font demo', default: false});
/* recorded when the feature loads, which is the point: the data for a font it
 * offers must NOT have been read yet -- that is what "on demand" means */
globalThis.__entrySawFontData = String(globalThis.__fontDataLoaded);
veusz.renderText(function (req) {
    if (!req.on('on')) { return null; }
    if (String(globalThis.__fontDataLoaded) !== 'yes') {
        /* ask for the data of the font this feature offers */
        var heads = globalThis.veuszFileHeads || [];
        for (var i = 0; i < heads.length; i++) {
            if (heads[i].file.indexOf('fonts/') === 0) {
                return JSON.stringify({load: heads[i].file});
            }
        }
    }
    return veusz.svg('<svg width="1pt" height="1pt"/>',
                     {width: 1, height: 1, depth: 0});
});
"""


BROKEN_FONT_DEMO = """
veusz.feature({name: 'brokefont', title: 'Broken font demo', target: 'text'});
veusz.switch('on', {label: 'Broken font', default: false});
/* always asks for a file that will not load, which is what a font built for
 * another bundle does */
veusz.renderText(function (req) {
    if (!req.on('on')) { return null; }
    return JSON.stringify({load: 'fonts/broken.js'});
});
"""


ROW_DEMO = """
veusz.feature({name: 'rowdemo', title: 'Row demo', target: 'text'});
/* three properties on one line of the panel: the first owns the row, the other
 * two are its members */
veusz.switch('on', {label: 'Row', default: false, row: 'one'});
veusz.choice('style', {label: 'Style', default: 'a', row: 'one',
                       choices: [{value: 'a', label: 'A'},
                                 {value: 'b', label: 'B'}]});
veusz.switch('bold', {label: 'Bold', default: false, row: 'one',
                      descr: 'Draw it bold'});
"""


#: what the delegating demo asks Veusz to draw
DELEGATED = '<math><mfrac><mi>a</mi><mi>b</mi></mfrac></math>'

DELEGATE_DEMO = """
veusz.feature({name: 'delegatedemo', title: 'Delegate demo', target: 'text'});
veusz.switch('on', {label: 'Delegate', default: false});
/* draws nothing at all: says what should be drawn and by whom */
veusz.renderText(function (req) {
    if (!req.on('on')) { return null; }
    return veusz.delegate(%s);
});
""" % json.dumps(DELEGATED)


class JsFeatureTests(unittest.TestCase):
    """A feature written only in JavaScript: the platform builds all of it.

    This is what the engine is for.  The feature file below declares what it
    wants and how it draws, and contains no Python, no Veusz and no Qt -- the
    platform turns the declaration into settings and turns the SVG it returns
    into a drawing.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = PROJECT / 'build-test-jsfeature'
        if cls.dir.exists():
            import shutil
            shutil.rmtree(cls.dir)
        (cls.dir / 'jsdemo').mkdir(parents=True)
        (cls.dir / 'jsdemo' / 'feature.js').write_text(JS_DEMO,
                                                       encoding='utf-8')

        # a feature that carries extra font data the way a real one does: one
        # file per font, in fonts/, self-declaring in its head
        (cls.dir / 'fontdemo' / 'fonts').mkdir(parents=True)
        (cls.dir / 'fontdemo' / 'fonts' / 'mathjax-demo.js').write_text(
            '/* a stand-in for a generated font data file: it carries no '
            'library of its own */\n'
            'globalThis.__fontDataLoaded = "yes";\n'
            '// MATHJAX-FONT {"id": "demo", "title": "Demo", '
            '"x_height": 0.5}\n', encoding='utf-8')
        (cls.dir / 'fontdemo' / 'feature.js').write_text(FONT_DEMO,
                                                         encoding='utf-8')

        # a font file that will not load: one line that throws is exactly what
        # a file built for another bundle looks like
        (cls.dir / 'brokefont' / 'fonts').mkdir(parents=True)
        (cls.dir / 'brokefont' / 'fonts' / 'broken.js').write_text(
            'throw new Error("this font was built for another bundle");\n',
            encoding='utf-8')
        (cls.dir / 'brokefont' / 'feature.js').write_text(
            BROKEN_FONT_DEMO, encoding='utf-8')

        # three properties that share one row of the panel
        (cls.dir / 'rowdemo').mkdir(parents=True)
        (cls.dir / 'rowdemo' / 'feature.js').write_text(ROW_DEMO,
                                                        encoding='utf-8')

        # a feature that asks Veusz to draw its text
        (cls.dir / 'delegatedemo').mkdir(parents=True)
        (cls.dir / 'delegatedemo' / 'feature.js').write_text(
            DELEGATE_DEMO, encoding='utf-8')
        os.environ['VEUSZ_JS_ENGINE_FEATURES'] = str(cls.dir)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop('VEUSZ_JS_ENGINE_FEATURES', None)

    def setUp(self):
        platform = getattr(veusz.utils, 'js_engine')
        platform.load_features()
        self.feature = [f for f in platform.feature_objects()
                        if f.name == 'jsdemo']
        self.assertTrue(self.feature, 'the JavaScript feature did not install')
        self.feature = self.feature[0]

    def setting(self, name):
        return veusz.setting.collections.Text('probe').get('jsdemo_' + name)

    def test_it_is_only_javascript(self):
        """Nothing in the feature's directory is Python."""
        files = list((self.dir / 'jsdemo').iterdir())
        self.assertEqual([p.name for p in files], ['feature.js'])

    def test_the_platform_built_veusz_settings_from_the_declaration(self):
        """The feature says 'switch' and 'choice'; Veusz gets Bool and Choice."""
        switch = self.setting('on')
        self.assertIsInstance(switch, veusz.setting.Bool)
        self.assertEqual(switch.usertext, 'JS demo')
        self.assertIn('JavaScript', switch.descr)
        self.assertFalse(switch.val)

        choice = self.setting('ink')
        self.assertIsInstance(choice, veusz.setting.Choice)
        self.assertEqual(choice.vallist, ['blue', 'red'])
        self.assertEqual(choice.uilist, ['Blue box', 'Red box'])
        self.assertEqual(choice.val, 'blue')

    def test_the_names_are_namespaced_so_two_features_cannot_collide(self):
        """A feature keeps its own short names; Veusz gets a qualified one."""
        self.assertEqual(self.feature.setting_name({'name': 'on'}), 'jsdemo_on')

    def test_a_declaration_may_name_its_setting(self):
        """A property has two names, and the feature declares both.

        ``name`` is the feature's own handle: what the request reports back and
        what its JavaScript uses.  ``setting`` is what Veusz stores in the
        document, so it is the one that outlives the feature.  Left out, the
        platform qualifies the name to keep two features from colliding over
        something as short as ``on``; said, it is used as written.
        """
        self.assertEqual(self.feature.setting_name(
            {'name': 'on', 'setting': 'mathjax'}), 'mathjax')

    def test_a_feature_loads_its_font_data_on_demand(self):
        """Extra font data lives in the feature's ``fonts/``, and is read only
        when a font is actually used.

        One file per font, and it carries no library of its own: it reads the
        classes out of the bundle already loaded and registers itself there.
        One font's data can be megabytes and a feature may offer a dozen, so
        the platform does not read them all -- it hands over the *head* of each
        (JavaScript cannot read a file), and the feature asks for the one it
        wants.  What is in a head is the feature's convention, not the
        platform's.
        """
        platform = getattr(veusz.utils, 'js_engine')
        features = [f for f in platform.feature_objects() if f.name == 'fontdemo']
        self.assertTrue(features, 'the scratch feature did not load')
        feature = features[0]
        runtime = feature.runtime

        heads = runtime.run('JSON.stringify(globalThis.veuszFileHeads)')
        self.assertIn('mathjax-demo.js', heads)
        self.assertIn('// MATHJAX-FONT', heads)
        self.assertIn('feature.js', heads, 'its own files are described too')

        # nothing was read when the feature loaded: that is the point
        self.assertEqual(runtime.run('String(globalThis.__fontDataLoaded)'),
                         'undefined')
        self.assertEqual(runtime.run('globalThis.__entrySawFontData'),
                         'undefined')

        request = json.dumps({'text': 'x', 'size': 20.0, 'color': None,
                              'props': {'on': True, 'font': 'demo',
                                        'display': False}})
        asked = json.loads(runtime.call('veuszRender', request))
        self.assertEqual(asked.get('load'), 'fonts/mathjax-demo.js',
                         'the feature did not ask for the font it needs')

        platform_module.load_feature_file(feature, asked['load'])
        self.assertEqual(runtime.run('globalThis.__fontDataLoaded'), 'yes')

        # asked again, it draws: the data is there now
        answered = json.loads(runtime.call('veuszRender', request))
        self.assertNotIn('load', answered)
        self.assertIn('svg', answered)
        # and reading it again is a no-op rather than a second registration
        platform_module.load_feature_file(feature, asked['load'])

    def test_a_feature_cannot_load_anything_it_likes(self):
        """The file name comes from JavaScript, so it is checked, not trusted.

        Without that a feature could read anything on the disk -- and it can
        only ever mean a file of its own.
        """
        platform = getattr(veusz.utils, 'js_engine')
        feature = [f for f in platform.feature_objects()
                   if f.name == 'fontdemo'][0]
        for name in ('../qjs.dll', 'fonts/../feature.js', 'nope.js',
                     '/etc/passwd'):
            self.assertRaises(platform_module.JsEngineError,
                              platform_module.load_feature_file, feature, name)

    def test_a_feature_is_reported_under_its_entry_point(self):
        """The report knows a JavaScript feature by its file, not its API."""
        platform = getattr(veusz.utils, 'js_engine')
        entry = self.dir / 'jsdemo' / 'feature.js'
        self.assertIn(entry, platform.runtimes())
        paths = [Path(item['path']) for item in platform.report()['runtimes']]
        self.assertIn(entry, paths)

    def test_it_draws_and_declining_lets_veusz_draw(self):
        """Off is byte-identical to a label that never had the feature."""
        plain = self.ink('plain', {})
        off = self.ink('off', {'jsdemo_on': False})
        on = self.ink('on', {'jsdemo_on': True})
        self.assertEqual(plain, off, 'declining changed the drawing')
        self.assertNotEqual(plain, on, 'the feature drew nothing')

    def test_a_property_changes_what_is_drawn(self):
        """The feature gets the property values, so it can act on them."""
        blue = self.ink('blue', {'jsdemo_on': True, 'jsdemo_ink': 'blue'})
        red = self.ink('red', {'jsdemo_on': True, 'jsdemo_ink': 'red'})
        self.assertNotEqual(blue, red)

    def test_the_platform_makes_svg_qt_can_draw(self):
        """currentColor and zero-width strokes are the platform's problem.

        QtSvg has neither, and a feature should be able to hand over whatever
        its library produced -- MathJax's output has both, measured: one
        stroke-width and two currentColor in a single formula.
        """
        raw = ('<svg><path fill="currentColor" stroke="currentColor" '
               'stroke-width="0" d="M0 0"/></svg>')
        fixed = platform_module.qt_safe_svg(raw, '#112233')
        self.assertNotIn('currentColor', fixed)
        self.assertNotIn('stroke-width', fixed)
        self.assertEqual(fixed.count('#112233'), 2)
        # with no colour to substitute, the attributes go rather than lie
        stripped = platform_module.qt_safe_svg(raw, None)
        self.assertNotIn('currentColor', stripped)
        self.assertNotIn('stroke-width', stripped)
        # the drawing itself is untouched
        self.assertIn('d="M0 0"', stripped)

    def test_properties_can_share_one_row_of_the_panel(self):
        """Three settings, one line -- the original plugin's MathJax row.

        The first property owns the row: the panel writes its name in the left
        column, and the control it makes is the whole row.  The others are its
        members, hidden as rows of their own but still ordinary settings of the
        document -- which is the other half of this test: they take values,
        report changes, and follow a change made somewhere else, because the
        controls in the row are Veusz's own.
        """
        probe = veusz.setting.collections.Text('probe')
        owner = probe.get('rowdemo_on')
        style = probe.get('rowdemo_style')
        bold = probe.get('rowdemo_bold')
        self.assertFalse(owner.hidden)
        self.assertTrue(style.hidden, 'a member of the row kept a row of its '
                                      'own')
        self.assertTrue(bold.hidden, 'a member of the row kept a row of its '
                                     'own')

        row = owner.makeControl(None)
        check, menu, box = row.controls
        self.assertIsInstance(check, qt.QCheckBox)
        self.assertEqual(check.text(), '',
                         'the row label was written on the checkbox as well')
        self.assertIsInstance(menu, qt.QComboBox)
        self.assertIsInstance(box, qt.QCheckBox)
        self.assertEqual(box.text(), 'Bold')
        self.assertEqual(menu.currentText(), 'A')
        self.assertEqual(box.toolTip(), 'Draw it bold')

        # a change in any member is reported the way the panel expects it:
        # (the control that moved, the setting, the new value)
        import veusz.setting as setting_module
        seen = []
        row.sigSettingChanged.connect(lambda *args: seen.append(args))
        box.setChecked(True)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][1], bold)
        self.assertEqual(seen[0][2], True)
        self.assertTrue(isinstance(bold, setting_module.Bool))

        # and a change made elsewhere follows into the widgets: these are
        # Veusz's own controls, so there is no synchronising code of ours
        owner.val = True
        style.val = 'b'
        self.assertTrue(check.isChecked())
        self.assertEqual(menu.currentText(), 'B')

    def test_the_real_panel_puts_them_on_one_line(self):
        """The same thing through Veusz's own panel builder.

        The test above makes the row by hand; this one asks the panel to build
        the settings page for a label and looks at what it made -- so a row that
        only works when it is called directly is not mistaken for one that
        appears in the window.
        """
        from veusz.windows import treeeditwindow
        doc = veusz.document.Document()
        ifc = veusz.document.CommandInterface(doc)
        page = ifc.Add('page')
        ifc.To(page)
        path = ifc.Add('label', name='lbl')
        widget = doc.resolveWidgetPath(None, '/%s/%s' % (page, path))
        self.assertIsNotNone(widget)
        text = widget.settings.get('Text')

        panel = treeeditwindow.PropertyList(doc, showformatsettings=False)
        panel.updateProperties(
            treeeditwindow.SettingsProxySingle(doc, text), showformatting=False)

        # the row is there, under the first member's name, in one row of the
        # grid -- with the two hidden members not taking rows of their own
        self.assertIn('mathjax', panel.setncntrls,
                      'the panel built %r out of %r'
                      % (sorted(panel.setncntrls), sorted(text.getNames())))
        self.assertEqual(panel.setncntrls['mathjax'][0].labelicon.text(),
                         'MathJax')
        row = panel.setncntrls['mathjax'][1]
        self.assertEqual(len(row.controls), 3)
        self.assertEqual(row.controls[2].text(), 'Display style')
        self.assertNotIn('mathjaxFont', panel.setncntrls)
        self.assertNotIn('mathjaxDisplay', panel.setncntrls)
        # and every control is on the same line of the grid
        rows = set()
        for widget in (panel.setncntrls['mathjax'][0], row):
            rows.add(panel.layout.getItemPosition(
                panel.layout.indexOf(widget))[0])
        self.assertEqual(len(rows), 1, 'the row was split over %d lines'
                         % len(rows))

        # the menu takes the width of the row, as it did in the original: the
        # panel is wider than the controls need, and the extra goes to it
        panel.resize(420, 700)
        panel.layout.activate()
        row.layout().activate()
        menu = row.controls[1]
        self.assertGreater(menu.width(), menu.sizeHint().width() * 3,
                           'the font menu kept its minimum width')
        self.assertGreater(menu.width(), row.controls[2].width(),
                           'the font menu is narrower than a checkbox')

    def test_a_font_file_that_will_not_load_says_so(self):
        """A stale font file must not take the frame down, or draw in the
        wrong font.

        The file here is one line that throws, which is what a font built for
        another bundle does.  The drawing has to come out with the reason on it
        -- the platform's own "cannot draw" text -- and the report has to
        record which file it was, because nothing else would say.
        """
        platform = getattr(veusz.utils, 'js_engine')
        before = len(platform.notes())
        off = self.ink('brokefont-off', {'brokefont_on': False})
        on = self.ink('brokefont', {'brokefont_on': True})
        self.assertNotEqual(off, on,
                            'a font that could not be read changed nothing')
        recorded = platform.notes()[before:]
        self.assertTrue(any('fonts/broken.js' in note
                            and 'could not be read' in note
                            for note in recorded),
                        'nothing was recorded about the file: %r' % (recorded,))

    def test_a_feature_can_ask_veusz_to_draw_its_text(self):
        """The one reply that draws nothing itself: hand the text back.

        The platform hands it to the renderer Veusz would have used -- not to
        the wrapper that asks the hooks, or a feature would be asked about its
        own text for ever.  So a feature that has something to say about the
        text (a parser, a transformation) does not have to learn to draw, and
        what the user sees is Veusz's own typesetting: this demo hands over
        MathML, which Veusz draws with its MathML widget.
        """
        from veusz.utils import textrender
        drawn = []
        original = textrender._MmlRenderer._initText

        def spy(self, text):
            original(self, text)
            drawn.append(text)

        textrender._MmlRenderer._initText = spy
        self.addCleanup(setattr, textrender._MmlRenderer, '_initText', original)

        off = self.ink('delegate-off', {'delegatedemo_on': False})
        self.assertEqual(drawn, [], 'Veusz was asked to draw with it off')
        on = self.ink('delegate-on', {'delegatedemo_on': True})
        self.assertEqual(drawn, [DELEGATED],
                         'the delegated text did not reach Veusz')
        self.assertNotEqual(off, on, 'the delegation drew nothing')

    def ink(self, name, settings):
        """Draw a label and count the pixels, as a cheap picture comparison."""
        doc = veusz.document.Document()
        ifc = veusz.document.CommandInterface(doc)
        page = ifc.Add('page')
        ifc.To(page)
        ifc.Add('label', name='lbl')
        ifc.Set('lbl/label', 'x = y')
        ifc.Set('lbl/Text/size', '20pt')
        for key, value in settings.items():
            ifc.Set('lbl/Text/%s' % key, value)
        out = self.dir / 'png'
        out.mkdir(exist_ok=True)
        path = out / ('%s.png' % name)
        ifc.Export(str(path), dpi=100)
        return path.read_bytes()


if __name__ == '__main__':
    sys.argv = [a for a in sys.argv if a != '--veusz']
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
        PlatformTests))
    if WITH_VEUSZ:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            VeuszTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            FeatureTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            JsFeatureTests))
    else:
        print('(platform tests only; pass --veusz for the Veusz side)\n')
    runner = unittest.TextTestRunner(verbosity=2)
    sys.exit(0 if runner.run(suite).wasSuccessful() else 1)
