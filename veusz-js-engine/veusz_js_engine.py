"""veusz-js-engine -- a JavaScript engine host for Veusz, as a plugin.

Part of the veusz-js-engine project.  Licensed under the Apache License 2.0.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
There is one JavaScript engine here, and it is QuickJS.  This plugin carries
it, and everything a Veusz plugin needs to put JavaScript behind part of Veusz.
It knows nothing about formulas, fonts, LaTeX or text rendering, and it ships
no JavaScript of its own -- except the API a feature is written against.

    veusz-js-engine (this plugin)          the platform
        |  engine: QuickJS, driven from here
        |  a JavaScript API that a feature is written against
        |  Veusz plumbing, written once for every feature:
        |    the properties panel the feature declares
        |    the text seam, and the drawing of the SVG it returns
        |    the box: where it goes, how big, aligned by its baseline
        v
    a feature (e.g. features/mathjax/feature.js)    concrete
        |  JavaScript only: declares what to add and returns an SVG
        v
    the feature the user sees

The point of the split is that the awkward part -- the monkey patching that
makes a JavaScript library draw a Veusz object, and the Qt that goes with it --
is written **once**, here.  A feature declares what it wants and draws; it
contains no Python, no Veusz and no Qt at all::

    veusz.feature({name: 'mine', title: 'Mine', target: 'text'});
    veusz.switch('on', {label: 'Mine', default: false});
    veusz.renderText(function (req) {
        if (!req.on('on')) { return null; }        // decline: Veusz draws it
        return veusz.svg(someDrawing(req.text), {width: 12, height: 9,
                                                 depth: 2});
    });

A feature that has to reach into Veusz itself can still be written in Python
(``feature.py``) and use the plumbing below directly; that is the escape hatch,
not the way in.

Rendering is **SVG everywhere**: the JavaScript turns its input into SVG,
and the platform hands that SVG to Qt.  A callback therefore never deals in
pixels -- only in which SVG goes where -- which is what lets one interface
serve any JavaScript file.

WHY IT MUST PUBLISH ITSELF AT RUN TIME
--------------------------------------
Veusz loads a plugin by executing the file with empty globals
(``Document.loadPlugins`` does ``exec(f.read(), {})``) and does not put the
plugin's directory on ``sys.path``.  Plugin files therefore *cannot import one
another*, and Veusz keeps no registry of what it has loaded.  The only thing
two plugins can both import is Veusz itself, so this one hands a
:class:`Platform` over on ``veusz.utils`` and a feature reaches it there::

    import veusz.utils
    platform = veusz.utils.js_engine        # the channel between the layers

That channel is the whole reason for the publishing.  It is *not* a licence to
have Veusz load the layers separately: **the platform loads its own features**,
so a feature is never in Veusz's plugin list and never has to wonder whether
the platform is up.  ``get_platform()`` still returns ``None`` when there is no
platform, because the published name is the only way to ask and a file can be
loaded by something other than this platform -- but inside a feature that case
cannot arise.  See README.md.
"""

import contextlib
import ctypes
import html
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

__version__ = '0.3.0'

# A QuickJS runtime is not safe to call from two threads at once.  Every call
# runs on the platform's engine thread (:class:`_EngineThread`), so this lock is
# taken **there and only there**, inside :meth:`_QuickJS._run_here`.  Callers on
# other threads take ``_RUNTIME_LOCK`` instead: a lock held across the hop
# would deadlock against the engine thread taking this one.
_LOCK = threading.RLock()

#: Guards a ``Runtime``'s own lifecycle (starting and closing), on whatever
#: thread asked -- never held while the engine works.
_RUNTIME_LOCK = threading.RLock()

# where a consumer looks for the platform once it is installed
PUBLISH_ATTR = 'js_engine'


class JsEngineError(RuntimeError):
    """Anything this platform refuses to do, said in one sentence.

    Raised for a missing engine, a JavaScript file that will not load, a
    feature that cannot be built, and a JavaScript exception coming back out of
    a feature -- so a caller has one thing to catch when it wants to degrade
    instead of failing.
    """


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def _say(message):
    """Write a line, but never raise.

    A frozen Veusz is a windowed application with no console: ``sys.stdout``
    and ``sys.stderr`` are **None** there, so ``print`` raises
    ``AttributeError: 'NoneType' object has no attribute 'write'`` -- which
    would fail the plugin over a log line, and hide whatever the real trouble
    was behind it.
    """
    try:
        if sys.stdout is not None:
            sys.stdout.write(message + '\n')
    except Exception:
        pass


def _warn(message):
    """``_say`` for stderr, with the same care about it being absent."""
    try:
        if sys.stderr is not None:
            sys.stderr.write(message + '\n')
    except Exception:
        pass


def _plugin_path():
    """This file's path.

    ``__file__`` first (an ordinary import sets it), then the loader's
    ``plugin`` local -- Veusz executes a plugin file with empty globals, so
    there is no ``__file__`` there and a frame is the only way to it.

    The order matters.  Walking first would find Veusz's outer loader, whose
    ``plugin`` names whatever Veusz was told to load -- not this file, when
    this file was loaded by something else (the platform loading a feature).
    """
    try:
        return Path(__file__).resolve()
    except NameError:
        pass
    except Exception:
        pass
    try:
        frame = sys._getframe(1)
        while frame is not None:
            candidate = frame.f_locals.get('plugin')
            if isinstance(candidate, str) and candidate.endswith('.py'):
                return Path(candidate).resolve()
            frame = frame.f_back
    except Exception:
        pass
    return None


def _plugin_dir():
    path = _plugin_path()
    return path.parent if path is not None else None


def _first_existing(candidates):
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def _env_file(name):
    """The file an environment variable names, or ``None`` if it is unset.

    A variable that is set but names nothing is an **error**, not a fallback to
    the search: the only reason to set it is to choose that file, so quietly
    using another one hides the typo and reports something confusing much
    later.  (``JsEngineError`` is defined below, which is fine -- this is only
    resolved when called.)
    """
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value)
    if not path.is_file():
        raise JsEngineError('%s names a file that is not there: %s'
                            % (name, path))
    return path


_QUICKJS_NAMES = ('qjs.dll', 'libqjs.so', 'libqjs.dylib', 'libqjs.so.0')


def find_quickjs(*places):
    """The engine: QuickJS, the one binary this platform needs.

    It is looked for beside the JavaScript, then beside this plugin, plus a
    ``data/`` subdirectory of each -- so a feature that needs a particular
    build of it can carry one, and everything else uses the platform's.
    ``VEUSZ_JS_ENGINE_QUICKJS`` overrides the lot.
    """
    chosen = _env_file('VEUSZ_JS_ENGINE_QUICKJS')
    if chosen is not None:
        return chosen
    candidates = []
    for place in places:
        if not place:
            continue
        place = Path(place)
        for directory in (place, place / 'data'):
            candidates += [directory / name for name in _QUICKJS_NAMES]
    return _first_existing(candidates)


# --------------------------------------------------------------------------
# the engine, bound directly
#
# There is no compiled bridge here, and no C of our own: the platform talks to
# QuickJS's C API itself.  A bridge was tried and removed -- it was the same
# marshalling, written in C, plus a lot of MathJax-shaped history that had to
# be deleted out of it.  Measured before removing it: this binding reproduces
# the bridge's output byte for byte on the project's own formulas, so the
# bridge had no capability of its own.
#
# The ABI facts that make this possible, all measured on the build this project
# ships against (a quickjs-ng shared build, **not** JS_NAN_BOXING):
#
#   JSValue          a 16-byte struct, returned and passed **by value**
#   tags             JS_TAG_STRING is -7, but a returned string may be
#                    JS_TAG_STRING_ROPE (-6) -- measured on MathJax's output
#   predicates       JS_IsException and friends are inline tag tests in the
#                    header, not exports, so they are spelled out below
#
# ``JSValue``'s layout is chosen by a macro at *build* time, so it cannot be
# inferred from the header alone; if a future engine is built with NaN boxing
# this is the one place that has to change (JSValue becomes a uint64).
# --------------------------------------------------------------------------

JS_TAG_STRING = -7
JS_TAG_STRING_ROPE = -6
JS_TAG_OBJECT = -1
JS_TAG_INT = 0
JS_TAG_BOOL = 1
JS_TAG_NULL = 2
JS_TAG_UNDEFINED = 3
JS_TAG_EXCEPTION = 6

JS_EVAL_TYPE_GLOBAL = 0

#: Object.hasOwn is ES2022 and the engine does not have it; MathJax uses it.
_JS_POLYFILL = (
    b'if (!Object.hasOwn) { Object.hasOwn = function (o, p) {'
    b' return Object.prototype.hasOwnProperty.call(o, p); }; }\n')

#: QuickJS has no idea how much stack it has, and does not look.  Its overflow
#: check is ``sp - alloca_size < rt->stack_top - rt->stack_size`` against the
#: *current C stack pointer* (``js_check_stack_overflow`` in quickjs.c): the
#: host says where the stack top is (``JS_UpdateStackTop``) and how much of it
#: the engine may use (``JS_SetMaxStackSize``), and that is the whole contract.
#: There is no OS query anywhere in the engine -- no
#: ``GetCurrentThreadStackLimits``, no ``pthread_attr_getstack`` -- so the
#: number is the host's to know or to invent.
#:
#: Not calling it is not neutral either.  The default is
#: ``JS_DEFAULT_STACK_SIZE``, 1 MiB (quickjs.h), which is less than MathJax
#: needs for an ordinary formula; and ``JS_SetMaxStackSize(rt, 0)`` means *no
#: limit*, i.e. recursion until the process dies rather than a catchable error.
#:
#: So the question is not whether to say a number, but which stack to say it
#: about.  A fraction of the *calling* thread is what the old bridge did, and it
#: makes the same formula work from one of Veusz's paint threads and fail from
#: another: measured, a thread with 2.88 MiB reserved leaves a budget of
#: 1.15 MiB and lays out 16 nested fractions, and no more.  The platform
#: therefore owns the thread, and the stack, itself -- and the budget is then a
#: constant rather than a property of whoever asked.
_ENGINE_THREAD_STACK = 40 * 1024 * 1024
_ENGINE_THREAD_NAME = 'veusz-js-engine'

#: How much of that stack the engine may account for.  Two fifths of 40 MiB is
#: 16 MiB, which is the cap; the rest is the margin the C frames themselves
#: need, because walking off the end of the real stack is a crash and not an
#: error.  The two numbers move together: a budget is a fraction of the thread
#: on purpose, so asking for a bigger one means a bigger thread, never a budget
#: that reaches the guard page.  Measured, for ``\frac{1}{\frac{1}{...}}``:
#: 1.15 MiB of budget laid out 16 levels, 2 MiB laid out 32, 3.2 MiB laid out
#: 48, 6 MiB laid out 96, and 16 MiB lays out 288 -- while ``\sqrt`` stops at
#: 128, ``\left(`` at 320 and superscripts at 64, because a level costs what the
#: construct costs (build/probe_depth.py).
_JS_STACK_FRACTION = 0.4
_JS_STACK_MIN = 256 * 1024
_JS_STACK_MAX = 16 * 1024 * 1024
_JS_STACK_FALLBACK = 768 * 1024

_JS_MEMORY_LIMIT = 256 * 1024 * 1024


def stack_budget_for_this_thread():
    """How much JS stack this thread can safely offer the engine.

    Called on the platform's engine thread, so this is a constant in practice;
    it stays a measurement because the engine's check is against the *real*
    stack, and a smaller thread must never be given a budget it cannot hold.
    """
    try:
        import ctypes.wintypes
        kernel32 = ctypes.WinDLL('kernel32')
        get_limits = kernel32.GetCurrentThreadStackLimits
        get_limits.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                               ctypes.POINTER(ctypes.c_size_t)]
        low, high = ctypes.c_size_t(), ctypes.c_size_t()
        get_limits(ctypes.byref(low), ctypes.byref(high))
        reserved = high.value - low.value
        if reserved > 0:
            budget = int(reserved * _JS_STACK_FRACTION)
            return max(_JS_STACK_MIN, min(budget, _JS_STACK_MAX))
    except Exception:                                     # noqa: BLE001
        pass
    return _JS_STACK_FALLBACK


class _Job(object):
    """One call to the engine thread, and where its answer goes."""

    def __init__(self, fn):
        self.fn = fn
        self.done = threading.Event()
        self.value = None
        self.error = None


class _EngineThread(object):
    """The one thread every runtime runs on, with a stack we chose.

    QuickJS's budget is a fraction of the *calling* thread, so whoever calls
    decides what the engine may do -- which is exactly the wrong thing for a
    library that needs three quarters of a megabyte to lay out a formula.
    Veusz's paint threads are not ours to size and are not all the same, so the
    platform makes one thread with a stack it picks and hands every call to it.

    Two things fall out of that: the budget is a constant, and a runtime can no
    longer be touched from the wrong thread at all -- which the engine, being
    single-threaded, would not survive.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._start_lock = threading.Lock()

    @property
    def thread(self):
        return self._thread

    def start(self):
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            # threading.stack_size() is process-wide and only affects threads
            # created after it, so it is set for this one and put back
            previous = threading.stack_size()
            try:
                threading.stack_size(_ENGINE_THREAD_STACK)
                thread = threading.Thread(target=self._work, daemon=True,
                                          name=_ENGINE_THREAD_NAME)
                thread.start()
            finally:
                threading.stack_size(previous)
            self._thread = thread
            return self

    def call(self, fn):
        """Run *fn* on the engine thread and return what it returns."""
        self.start()
        if threading.current_thread() is self._thread:
            return fn()                     # already there: no hop, no deadlock
        job = _Job(fn)
        self._queue.put(job)
        job.done.wait()
        if job.error is not None:
            raise job.error
        return job.value

    def _work(self):
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job.value = job.fn()
            except BaseException as exc:                  # noqa: BLE001
                job.error = exc
            finally:
                job.done.set()


_ENGINE_THREAD = None
_ENGINE_THREAD_LOCK = threading.Lock()


def engine_thread():
    """The thread the engine runs on, started when it is first needed."""
    global _ENGINE_THREAD
    with _ENGINE_THREAD_LOCK:
        if _ENGINE_THREAD is None:
            _ENGINE_THREAD = _EngineThread()
        return _ENGINE_THREAD


class _JsValueUnion(ctypes.Union):
    _fields_ = [('int32', ctypes.c_int32),
                ('float64', ctypes.c_double),
                ('ptr', ctypes.c_void_p)]


class _JsValue(ctypes.Structure):
    """QuickJS's ``JSValue``: a tagged union, 16 bytes, passed by value."""

    _fields_ = [('u', _JsValueUnion), ('tag', ctypes.c_int64)]


def _js_undefined():
    return _JsValue(_JsValueUnion(int32=0), JS_TAG_UNDEFINED)


class _QuickJS(object):
    """QuickJS as this platform needs it: JavaScript in, a string out.

    The C API hands back handles into the engine's own heap, and a handle has to
    be given back -- which is where the bookkeeping in a binding normally comes
    from.  None of it reaches past this class: a call runs JavaScript and returns
    a Python string, and the handle is made and released inside that call.  There
    is one primitive, :meth:`run`, and calling a named global function is spelled
    as JavaScript too (:meth:`call`), so there is only one thing here to get
    wrong rather than one per C function.

    Two facts about the engine are why this is not three lines:

    * ``JSValue`` is a 16-byte tagged union passed **by value**, and which
      representation is in use is decided by a macro at *build* time --
      ``JS_NAN_BOXING`` would make it a single ``uint64_t``.  This build is not
      NaN-boxed.
    * The engine's stack check is against the *calling* thread
      (``sp < stack_top - stack_size``), and it has no way to look up what that
      thread really has.  So every runtime is created *and* called on the
      platform's own thread, whose stack the platform chose: see
      :class:`_EngineThread`.
    """

    def __init__(self, engine, path=None):
        self.engine = Path(engine)
        #: the JavaScript file this runtime was made for, for error messages
        self.path = Path(path) if path else None
        #: how many engine values this object is holding; always zero between
        #: calls, and a test says so
        self._outstanding = 0
        self.rt = None
        self.ctx = None
        lib = ctypes.CDLL(str(self.engine))
        self.lib = lib

        lib.JS_NewRuntime.restype = ctypes.c_void_p
        lib.JS_NewContext.restype = ctypes.c_void_p
        lib.JS_NewContext.argtypes = [ctypes.c_void_p]
        lib.JS_FreeContext.argtypes = [ctypes.c_void_p]
        lib.JS_FreeRuntime.argtypes = [ctypes.c_void_p]
        lib.JS_SetMemoryLimit.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.JS_SetMaxStackSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.JS_UpdateStackTop.argtypes = [ctypes.c_void_p]
        lib.JS_RunGC.argtypes = [ctypes.c_void_p]

        lib.JS_Eval.restype = _JsValue
        lib.JS_Eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                ctypes.c_size_t, ctypes.c_char_p,
                                ctypes.c_int]
        lib.JS_FreeValue.argtypes = [ctypes.c_void_p, _JsValue]
        # c_void_p, not c_char_p: c_char_p would convert the result to a Python
        # bytes object, and the pointer to free would then be Python's own
        # buffer instead of the engine's -- freeing that corrupts the heap.
        lib.JS_ToCStringLen2.restype = ctypes.c_void_p
        lib.JS_ToCStringLen2.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_size_t),
                                         _JsValue, ctypes.c_int]
        lib.JS_FreeCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.JS_GetException.restype = _JsValue
        lib.JS_GetException.argtypes = [ctypes.c_void_p]

        engine_thread().call(self._create)

    def _create(self):
        """Make the runtime and its context -- on the engine thread."""
        lib = self.lib
        self.rt = lib.JS_NewRuntime()
        if not self.rt:
            raise JsEngineError('cannot create a QuickJS runtime')
        lib.JS_SetMemoryLimit(self.rt, _JS_MEMORY_LIMIT)
        self._stack_budget = stack_budget_for_this_thread()
        lib.JS_SetMaxStackSize(self.rt, self._stack_budget)
        self.ctx = lib.JS_NewContext(self.rt)
        if not self.ctx:
            lib.JS_FreeRuntime(self.rt)
            self.rt = None
            raise JsEngineError('cannot create a QuickJS context')
        try:
            self._run_here(_JS_POLYFILL, '<polyfill>')
        except Exception:
            self._close_here()
            raise

    # -- the one primitive -------------------------------------------------

    def run(self, source, name='<javascript>'):
        """Evaluate *source* and return the result as text.

        Everything the platform asks of the engine goes through here, so the
        engine handle exists only between these lines and nothing outside has
        to know that engine values need releasing.  The work happens on the
        engine thread, whatever thread asked for it.
        """
        if isinstance(source, str):
            source = source.encode('utf-8')
        return engine_thread().call(lambda: self._run_here(source, name))

    def _run_here(self, source, name):
        with _LOCK:
            self._anchor_to_this_thread()
            value = self.lib.JS_Eval(self.ctx, source, len(source),
                                     name.encode('utf-8')[:120],
                                     JS_EVAL_TYPE_GLOBAL)
            self._outstanding += 1
            try:
                if self.is_exception(value):
                    raise JsEngineError('%s: %s' % (name,
                                                    self._exception_message()))
                text = self.text(value)
                if text is None:
                    raise JsEngineError('%s did not produce a string' % name)
                return text
            finally:
                self.lib.JS_FreeValue(self.ctx, value)
                self._outstanding -= 1

    def run_file(self, path):
        """Evaluate a JavaScript file in this runtime, in the global scope."""
        path = Path(path)
        return self.run(path.read_bytes(), str(path))

    def call(self, fn_name, payload=''):
        """Call a global function with one string, get one string back.

        This is the whole interface between the platform and a feature's
        JavaScript, and it is spelled as JavaScript -- one call expression --
        so that it and an ordinary evaluation are the same thing all the way
        down.  The two ``json.dumps`` calls matter: ``ensure_ascii`` (the
        default) escapes the line separators that JSON allows raw and
        JavaScript does not, and quoting the name keeps a feature from being
        able to reach anything but a global of its own.
        """
        return self.run('globalThis[%s](%s)' % (json.dumps(fn_name),
                                                json.dumps(payload)),
                        name=fn_name)

    def is_exception(self, value):
        return value.tag == JS_TAG_EXCEPTION

    def text(self, value):
        """A string value as Python text; None if it is not a string.

        ``JS_ToCStringLen2`` is what decides -- a returned string may be a rope
        (``JS_TAG_STRING_ROPE``, -6, which is what MathJax's render actually
        returns), and asking the tag alone would miss it.  The C string is
        copied and released here, because it is not held past this line.
        """
        length = ctypes.c_size_t()
        pointer = self.lib.JS_ToCStringLen2(self.ctx, ctypes.byref(length),
                                            value, 0)
        if not pointer:
            return None
        try:
            return ctypes.string_at(pointer, length.value).decode('utf-8',
                                                                  'replace')
        finally:
            self.lib.JS_FreeCString(self.ctx, pointer)

    def outstanding(self):
        """Engine values still held.  Between calls this is zero, always."""
        return self._outstanding

    def _exception_message(self):
        """The pending exception as text, and clear it.

        ``JS_GetException`` is what fetches the error object: what an
        evaluation returns for a failure is only a marker, so asking *it* for a
        message gets nothing -- which is how a binding ends up reporting
        "unknown JavaScript exception" for every real error.  Its text form
        already reads ``Error: ...``, so no property is read.
        """
        try:
            exc = self.lib.JS_GetException(self.ctx)
            self._outstanding += 1
            try:
                return self.text(exc) or 'unknown JavaScript exception'
            finally:
                self.lib.JS_FreeValue(self.ctx, exc)
                self._outstanding -= 1
        except Exception:                                 # noqa: BLE001
            return 'unknown JavaScript exception'

    def _anchor_to_this_thread(self):
        """Say where the stack top is, and that this is the right thread.

        Every call arrives on the platform's engine thread, so the top is the
        same one every time and the budget is a constant.  The check is kept
        anyway: it is three lines, and it is where a call from anywhere else --
        which the engine, being single-threaded, would not survive -- is
        noticed instead of corrupting something quietly.
        """
        thread = engine_thread()
        if threading.current_thread() is not thread.thread:
            raise JsEngineError(
                'the engine was called from thread %r, not from its own (%r)'
                % (threading.current_thread().name, _ENGINE_THREAD_NAME))
        self.lib.JS_UpdateStackTop(self.rt)
        budget = stack_budget_for_this_thread()
        if budget != self._stack_budget:
            self.lib.JS_SetMaxStackSize(self.rt, budget)
            self._stack_budget = budget

    def _close_here(self):
        if self.ctx:
            self.lib.JS_RunGC(self.rt)
            self.lib.JS_FreeContext(self.ctx)
            self.ctx = None
        if self.rt:
            self.lib.JS_FreeRuntime(self.rt)
            self.rt = None

    def close(self):
        """Free the runtime -- on the engine thread, where it lives."""
        if self.rt is None and self.ctx is None:
            return
        thread = _ENGINE_THREAD
        if thread is None or thread.thread is None \
                or not thread.thread.is_alive():
            self._close_here()          # shutting down: nobody else is left
            return
        thread.call(self._close_here)






class Runtime(object):
    """A QuickJS runtime holding one JavaScript file.

    Start is lazy: the runtime, the parse and the memory that goes with them
    are only paid when the file is first called, so a JavaScript file a
    feature carries but never needs costs nothing.
    """

    def __init__(self, path, engine=None, label=None):
        self.path = Path(path)
        #: what to call this runtime in a message.  A JavaScript feature's
        #: runtime is created with the platform's API file and then has the
        #: feature's files added, so its ``path`` is not what the user
        #: recognises; the entry point is.
        self.label = Path(label) if label else Path(path)
        #: the engine to load; the platform works it out and passes it
        self.engine = Path(engine) if engine else None
        self._js = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def started(self):
        return self._js is not None

    def start(self):
        with _RUNTIME_LOCK:
            self._start_locked()
        return self

    def _start_locked(self):
        if self.started:
            return self
        if self.engine is None:
            raise JsEngineError(
                'no QuickJS engine for %s. Put one beside it (%s), or set '
                'VEUSZ_JS_ENGINE_QUICKJS.'
                % (self.path.name, ' / '.join(_QUICKJS_NAMES[:2])))
        if not self.path.is_file():
            raise JsEngineError('no such JavaScript file: %s' % self.path)
        js = _QuickJS(self.engine, self.label)
        try:
            js.run_file(self.path)
            # A bundle has always been expected to define render(); the probe
            # is kept because a file that does not is almost certainly not one.
            if js.run('typeof render === "function"') != 'true':
                raise JsEngineError('%s does not define render()'
                                    % self.path.name)
        except Exception:
            js.close()
            raise
        self._js = js
        return self

    def close(self):
        with _RUNTIME_LOCK:
            if self._js is not None:
                self._js.close()
            self._js = None

    # -- the primitive -----------------------------------------------------

    def run(self, source, name='<javascript>'):
        """Evaluate JavaScript in this runtime and return the result as text.

        The whole engine is this: JavaScript in, a string out.  The result is
        converted to text by the engine, so an expression returning a number
        gives ``'3'`` and one returning an object gives something only useful
        for display -- return a string (JSON, typically) when the value
        matters.
        """
        with _RUNTIME_LOCK:
            if not self.started:
                self._start_locked()
            return self._js.run(source, name)

    def call(self, fn_name, payload=''):
        """Call a global function with one string, get one string back.

        This is the whole interface between the platform and a feature's
        JavaScript.  JSON inside the string is a convention between the two of
        them, not something the engine knows about; see ``call_json``.
        """
        with _RUNTIME_LOCK:
            if not self.started:
                self._start_locked()
            return self._js.call(fn_name, payload)

    def call_json(self, fn_name, request):
        """``call`` with JSON on both sides; returns the decoded reply."""
        raw = self.call(fn_name, json.dumps(request))
        try:
            return json.loads(raw)
        except ValueError:
            raise JsEngineError('%s() did not return JSON: %.120r'
                                % (fn_name, raw))

    def eval_file(self, path):
        """Run one more script in this runtime, in the global scope.

        How a JavaScript file adds an optional part of itself (a bundle, a
        font, a plugin) without the feature having to know what it is.
        """
        with _RUNTIME_LOCK:
            if not self.started:
                self._start_locked()
            self._js.run_file(path)
            return True

    # -- rendering, the compatibility path ---------------------------------



# --------------------------------------------------------------------------
# the platform
# --------------------------------------------------------------------------

class Platform(object):
    """A QuickJS host, and the Veusz plumbing a feature builds on.

    A feature reaches this through ``veusz.utils.js_engine`` (or
    :func:`get_platform`); see the module docstring for why it is published
    rather than imported.

    The platform owns one binary -- the engine (QuickJS) -- and drives it
    itself.  It owns the Veusz side too: a feature declares what it wants and
    returns an SVG, and everything between those two facts is here.
    """

    def __init__(self, here, state=None):
        self.here = Path(here) if here else None
        #: the engine, next to the platform itself -- the platform's own file
        self.quickjs = find_quickjs(self.here)
        self._runtimes = {}
        self._features = []
        self.log = []
        self.state = state if state is not None else State()

    # -- using a JavaScript file ------------------------------------------

    def runtime(self, path):
        """A runtime for the JavaScript file at *path*, started on first use.

        One runtime per file: ask twice and the same one comes back, so a
        feature does not pay the parse (143 ms to 457 ms, measured, and up to
        32 MB) more than once.  The engine is looked for beside the JavaScript,
        then beside the platform (see :func:`find_quickjs`), so a caller
        normally says nothing at all.

        """
        path = Path(path).resolve()
        if not path.is_file():
            raise JsEngineError('no such JavaScript file: %s' % path)
        # the platform's own file, but a feature may ship its own copy, so
        # the feature's directory is looked at first
        quickjs = find_quickjs(path.parent, self.here)
        key = ('file', path)
        runtime = self._runtimes.get(key)
        if runtime is None:
            runtime = Runtime(path, engine=quickjs)
            self._runtimes[key] = runtime
        return runtime

    def js_api(self):
        """The platform's own JavaScript, or None if it is not beside us.

        It is what a feature is written against, so a runtime is created with
        it and the feature's files are added afterwards -- see
        :meth:`feature_runtime`.
        """
        if self.here is None:
            return None
        path = Path(self.here) / JS_API_FILE
        return path if path.is_file() else None

    def _svg_renderer_class(self):
        """The one SVG renderer class, built on first use.

        Built here rather than imported at the top because it needs Qt, which
        this module must not touch until Veusz is up.
        """
        cls = getattr(self, '_svg_renderer', None)
        if cls is None:
            import veusz.qtall as qt
            from veusz.utils import textrender
            cls = make_svg_renderer(textrender, qt)
            self._svg_renderer = cls
        return cls

    def feature_runtime(self, entry, feature_dir=None):
        """A runtime for one JavaScript feature, loaded and ready to ask.

        The order is: the platform's API, then every other ``*.js`` of the
        feature in name order, then its entry point -- so ``feature.js`` may
        lean on a bundle beside it and on ``veusz`` without either being a
        special case.

        Keyed by the **entry point**, not by the API file: every feature shares
        one ``jsapi.js``, and keying by it would run them all in a single
        namespace, each overwriting the last one's `veuszDescribe`.
        """
        entry = Path(entry).resolve()
        if not entry.is_file():
            raise JsEngineError('no such JavaScript file: %s' % entry)
        quickjs = find_quickjs(entry.parent, self.here)

        key = ('feature', entry)
        runtime = self._runtimes.get(key)
        if runtime is None:
            api = self.js_api()
            runtime = Runtime(api if api is not None else entry,
                              engine=quickjs, label=entry)
            runtime.feature_entry = entry
            runtime.loaded_scripts = set()
            self._runtimes[key] = runtime

        api = self.js_api()
        heads = feature_js_heads(feature_dir)
        if heads and 'veuszFileHeads' not in runtime.loaded_scripts:
            # JavaScript cannot read a file, and a feature needs to know what
            # it carries before it declares anything, so the heads are handed
            # over here.  What is in one is the feature's convention.
            runtime.run('globalThis.veuszFileHeads = %s;'
                        % json.dumps(heads), '<file heads>')
            runtime.loaded_scripts.add('veuszFileHeads')
        for script in _js_load_order(entry, feature_dir):
            script = Path(script).resolve()
            if api is not None and script == Path(api).resolve():
                continue                    # the runtime was created with it
            if script in runtime.loaded_scripts:
                continue
            runtime.eval_file(script)
            runtime.loaded_scripts.add(script)
        return runtime

    def runtimes(self):
        """``(js path, started)`` for every file that has been asked for.

        A JavaScript feature is reported under its **entry point**, which is
        what it is known by, even though its runtime was created with the
        platform's API file and then had the entry added to it.
        """
        return dict((path, runtime.started)
                    for (_kind, path), runtime in self._runtimes.items())

    def start_all(self):
        """Start every runtime asked for so far (a warm-up, or a check)."""
        for runtime in self._runtimes.values():
            runtime.start()
        return self

    def close_all(self):
        for runtime in self._runtimes.values():
            runtime.close()
        self._runtimes.clear()

    def report(self):
        return {
            'engine': str(self.quickjs) if self.quickjs else None,
            'runtimes': [
                {'path': str(path), 'kind': kind,
                 'size': path.stat().st_size if path.exists() else 0,
                 'started': runtime.started}
                for (kind, path), runtime
                in sorted(self._runtimes.items(), key=lambda item: str(item[0]))],
        }

    # -- the abstract plumbing, reached through the object a consumer has --
    # A next-layer plugin only ever holds this object (it finds it at run
    # time), so the plumbing is offered here rather than only as module
    # functions.

    def add_setting(self, target, group, setting):
        """Add a setting to every instance of *target*.

        See :func:`add_setting`.
        """
        return add_setting(target, group, setting)

    def hook_draw(self, target, callback):
        """Let *callback* decide how instances of *target* are drawn.

        See :func:`hook_draw`.
        """
        return hook_draw(target, callback)

    def register_provider(self, provider):
        """Add a text provider object.  See :func:`register_provider`."""
        return register_provider(provider)

    def providers(self):
        return providers()

    def draw_hooks(self):
        """The registered (target, callback) pairs, for introspection."""
        return [(hook.target, hook.callback)
                for hook in get_state().draw_hooks]

    def features(self):
        """The feature entries that loaded, in the order they were loaded."""
        return list(get_state().features)

    def feature_objects(self):
        """The installed **JavaScript** features, in load order.

        Each is what the feature's own declaration said, plus the machinery the
        platform built from it.  A Python feature is not here: it builds itself,
        so there is nothing for the platform to hold.
        """
        return list(getattr(self, '_features', ()))

    def feature_names(self):
        """The names of the features that loaded, in load order."""
        return [feature_name(path) for path in get_state().features]

    def load_features(self):
        """Load any feature plugins not loaded yet.

        See :func:`load_features`.
        """
        return load_features(self)

    def notes(self):
        return notes()


def get_platform():
    """The installed platform, or None.

    The supported way for a next-layer plugin to find this one::

        import veusz.utils
        platform = getattr(veusz.utils, 'js_engine', None)
    """
    try:
        import veusz.utils
    except ImportError:
        return None
    return getattr(veusz.utils, PUBLISH_ATTR, None)


# --------------------------------------------------------------------------
# the abstract Veusz plumbing a next-layer plugin builds on
#
# Two things, and both take the class of object they apply to:
#
#   1. add_setting(target, group, setting)   put a property on a class
#   2. hook_draw(target, callback)           decide how its instances render
#
# Rendering is SVG everywhere: the feature's JavaScript turns text into SVG,
# and the platform hands that SVG to Qt.  A callback therefore never has to
# think about pixels, only about which SVG goes where.
# --------------------------------------------------------------------------

class _Injection(object):
    """One setting to add to every instance of a target class."""

    def __init__(self, target, group, setting):
        self.target = target
        self.group = group
        self.setting = setting
        self.wrapped = False


class _DrawHook(object):
    """One callback that may take over drawing, for a target class."""

    def __init__(self, target, callback, text_seam):
        self.target = target
        self.callback = callback
        self.text_seam = text_seam
        self.wrapped = False


_STATE = None


class State(object):
    """The registries, the font-owner slot and the "already patched" flag.

    These must **not** live in this module's globals.  Veusz loads a plugin by
    executing the file with empty globals (``exec(f.read(), {})``), so a second
    ``loadPlugins`` -- which Veusz does do -- would run this file again in a
    fresh namespace: the flag would be False again and the plugin would wrap
    Veusz's settings and renderer a second time, while the wrapper installed by
    the first run kept dispatching to the *first* run's registries.  So the
    state is kept on ``veusz.utils``, where there is only ever one, and every
    wrapper closes over the state object rather than over a list.
    """

    def __init__(self):
        self.injections = []          # _Injection
        self.draw_hooks = []          # _DrawHook
        self.features = []            # entry paths of feature plugins loaded
        self.feature_names = set()    # their names, for idempotency
        self.notes = []
        self.hooked = False
        self.platform = None
        #: Veusz's own ``textrender.Renderer``, from before the platform
        #: replaced it with the wrapper that asks the hooks.  A feature that
        #: says "you draw this" is handed it directly.
        self.native_renderer = None
        # which settings group made which font -- per thread, because Veusz
        # paints from more than one
        self.owner = threading.local()

    def note(self, message):
        """Record a line for the report, once.

        The report is a list of facts about the environment, and a fact does
        not become more true by being recorded again -- a document with fifty
        labels in a font that cannot be outlined would otherwise fill the whole
        list with one sentence.  The JavaScript side is deduplicated the same
        way, so a feature's note and the platform's behave alike.
        """
        if message in self.notes:
            return
        self.notes.append(message)
        if len(self.notes) > 64:
            del self.notes[:-64]

    def text_draw_hooks(self):
        return [hook for hook in self.draw_hooks if hook.text_seam]


def get_state():
    """The one state object: on ``veusz.utils`` if Veusz is up, else here."""
    global _STATE
    try:
        import veusz.utils
    except ImportError:
        if _STATE is None:
            _STATE = State()
        return _STATE
    state = getattr(veusz.utils, '_js_engine_state', None)
    if state is None:
        state = State()
        setattr(veusz.utils, '_js_engine_state', state)
    _STATE = state
    return state


def _is_settings_class(target):
    try:
        from veusz.setting.settings import Settings
    except ImportError:
        return False
    return isinstance(target, type) and issubclass(target, Settings)


# --------------------------------------------------------------------------
# 1. add a property to a class of object
# --------------------------------------------------------------------------

def add_setting(target, group, setting):
    """Add a setting to **every instance** of *target*.

    ===================  =====================================================
    ``target``           a ``Settings`` subclass -- every instance of it *is*
                         a settings group -- or a ``Widget`` subclass, whose
                         settings tree is then searched for *group*.
    ``group``            ``None``, or a path such as ``'Label/TickLabels'``
                         naming the group inside the target to add into.
    ``setting``          a setting instance to use as a prototype.  It is
                         copied per instance: a settings tree takes ownership
                         (``Settings.add`` sets ``setting.parent``), so one
                         object cannot live in two trees.
    ===================  =====================================================

    The properties panel is generated from the settings tree, so the option
    appears in the UI by itself, on the page that group renders as.  Which
    *kind* of control it is comes from the setting's own type -- a
    ``setting.Bool`` is a checkbox, a ``setting.Choice`` a menu -- or from a
    subclass that overrides ``makeControl`` for a composite row.

    Adding the same name twice is a no-op, so this is safe to call on every
    plugin load.
    """
    state = get_state()
    for existing in state.injections:
        if (existing.target is target and existing.group == group
                and existing.setting.name == setting.name):
            return setting
    injection = _Injection(target, group, setting)
    state.injections.append(injection)
    _wrap_for_injection(injection)
    return setting


def _container_for(obj, group):
    """The settings group in *obj* named by *group*, or None."""
    container = obj
    if not _is_settings_class(type(obj)):
        container = getattr(obj, 'settings', None)
    if container is None:
        return None
    if not group:
        return container
    for part in str(group).replace('\\', '/').split('/'):
        if not part:
            continue
        try:
            container = container.get(part)
        except Exception:                                 # noqa: BLE001
            return None
        if container is None:
            return None
    return container


def _apply_injections(state, obj):
    for injection in list(state.injections):
        if not isinstance(obj, injection.target):
            continue
        container = _container_for(obj, injection.group)
        if container is None:
            continue
        name = injection.setting.name
        if name in container:
            continue
        try:
            container.add(injection.setting.copy())
        except Exception as exc:                          # noqa: BLE001
            state.note('could not add setting %r to %s: %s'
                       % (name, type(obj).__name__, exc))


def _wrap_for_injection(injection):
    """Wrap the target class so new instances get the setting."""
    if injection.wrapped:
        return
    target = injection.target
    original = target.__init__

    def _init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        _apply_injections(get_state(), self)

    _init._js_engine_injection = True
    target.__init__ = _init
    injection.wrapped = True


# --------------------------------------------------------------------------
# 2. decide how an object renders
# --------------------------------------------------------------------------

def hook_draw(target, callback):
    """Let *callback* decide how instances of *target* are drawn.

    Two seams, chosen by what *target* is.  Both are asked once per object,
    and **both decline by returning None**, so several hooks can be registered
    and the first one that claims the object wins.

    ``target`` a ``Settings`` subclass (``collections.Text`` and friends)
        The callback is asked once per **text element**, just before its text
        is rendered, and may return a renderer to use instead::

            def callback(painter, font, x, y, text, settings, **kwargs):
                if not settings.get('myflag').val:
                    return None                    # decline
                return MyRenderer(painter, font, x, y, text, **kwargs)

        This is the seam a text feature needs, because one widget draws
        several texts -- an axis paints its tick numbers and its label -- and
        each has its own settings group.  ``settings`` is the group that made
        this text's font, so the decision is per element, not per widget.

    ``target`` a ``Widget`` subclass
        The callback is asked once per widget draw and gets Veusz's own
        painting as a callable::

            def callback(widget, draw_original, *args, **kwargs):
                if not should_take_over(widget):
                    return None                    # decline
                paint_it_another_way(widget)
                return None if False else widget.drawResult

        Use this to replace or wrap how a whole object renders.  Note that it
        owns the *whole* painting of that widget, including whatever else the
        widget normally draws.

    Registering the **same callback** for the same target twice is one hook.
    That is what makes a feature safe to load twice: a setting is keyed by
    name and survives it, so a hook has to survive it too, or one feature ends
    up asked twice per text element.  Two *different* callbacks on one target
    are both kept, and both asked, in the order they were registered.
    """
    state = get_state()
    for existing in state.draw_hooks:
        if existing.target is target and existing.callback is callback:
            return callback
    text_seam = _is_settings_class(target)
    hook = _DrawHook(target, callback, text_seam)
    state.draw_hooks.append(hook)
    if not text_seam:
        _wrap_widget_draw(hook)
    return callback


def _wrap_widget_draw(hook):
    """Wrap a widget class's draw so the hook is asked first."""
    if hook.wrapped:
        return
    cls = hook.target
    original = cls.draw

    def draw(self, *args, **kwargs):
        for candidate in list(get_state().draw_hooks):
            if candidate.text_seam or not isinstance(self, candidate.target):
                continue
            try:
                result = candidate.callback(self, original, *args, **kwargs)
            except Exception as exc:                      # noqa: BLE001
                get_state().note('%s draw hook failed: %s'
                                 % (candidate.target.__name__, exc))
                continue
            if result is not None:
                return result
        return original(self, *args, **kwargs)

    draw._js_engine_hook = True
    cls.draw = draw
    hook.wrapped = True


def register_provider(provider):
    """Add a text provider object; lower ``priority`` is asked first.

    A convenience for the common case, expressed on top of :func:`hook_draw`:
    the object needs ``target`` (a settings class), ``applies(settings)`` and
    ``make_renderer(painter, font, x, y, text, settings, **kwargs)``.
    """
    target = getattr(provider, 'target', None)
    if target is None:
        raise ValueError('a provider needs a target settings class; use '
                         'hook_draw() for anything else')

    def callback(painter, font, x, y, text, settings, **kwargs):
        try:
            if not provider.applies(settings):
                return None
        except Exception as exc:                          # noqa: BLE001
            get_state().note('%s.applies() failed: %s'
                             % (getattr(provider, 'name', '?'), exc))
            return None
        return provider.make_renderer(painter, font, x, y, text, settings,
                                      **kwargs)

    callback.priority = getattr(provider, 'priority', 0)
    callback.provider = provider
    hook_draw(target, callback)
    _providers_by_priority(get_state())
    return provider


def _providers_by_priority(state):
    """Keep the provider hooks in priority order (text seam only)."""
    state.draw_hooks.sort(key=lambda hook: (
        getattr(hook.callback, 'priority', 0) if hook.text_seam else 0))


def providers():
    """The provider objects registered with :func:`register_provider`."""
    return [hook.callback.provider for hook in get_state().draw_hooks
            if getattr(hook.callback, 'provider', None) is not None]


def notes():
    """Errors from hooks and callbacks, newest last (for the log and tests)."""
    return list(get_state().notes)


def _note(message):
    get_state().note(message)


def settings_of(font):
    """The text settings group a QFont was made from, or None.

    A widget can hold several text elements -- an axis has tick numbers and a
    label -- and each has its own settings group.  Every widget builds an
    element's font from that element's own group just before drawing it
    (``s.get('TickLabels').makeQFont(painter)``), so remembering which group
    made the font is what lets a provider answer per element.  Without it, a
    switch on an axis label would drag its tick numbers along.
    """
    try:
        pair = getattr(get_state().owner, 'value', None)
    except Exception:
        return None
    if pair is None:
        return None
    settings, owner_font = pair
    return settings if font is owner_font else None


def _hooked_makeQFont(collections, state):
    original = collections.Text.makeQFont

    def _makeQFont(self, painthelper):
        font = original(self, painthelper)
        state.owner.value = (self, font)
        return font

    collections.Text.makeQFont = _makeQFont


def _hooked_draw(thefactory, state):
    """Wrap every widget's draw to clear the font-owner as a paint starts.

    Not all text is painted with a ``makeQFont`` of its own, so the owner from
    the previous draw must not be inherited by this one.
    """
    wrapped = []

    def _wrap(cls):
        if getattr(cls.draw, '_js_engine_plugin', False):
            return
        original = cls.draw

        def draw(self, *args, **kwargs):
            previous = getattr(state.owner, 'value', None)
            state.owner.value = None
            try:
                return original(self, *args, **kwargs)
            finally:
                state.owner.value = previous

        draw._js_engine_plugin = True
        cls.draw = draw
        wrapped.append(cls.__name__)

    for cls in thefactory.listWidgetClasses():
        if hasattr(cls, 'draw'):
            _wrap(cls)
    return wrapped


def make_renderer_wrapper(textrender, qt, state):
    """Build the one ``Renderer`` that asks the text hooks.

    Every plugin that wants to draw text would otherwise wrap
    ``veusz.utils.Renderer`` for itself; doing it once here means the order
    between two such plugins is decided by priority instead of by which one
    happened to be loaded last, and two of them cannot end up wrapping each
    other into a loop.

    The wrapper closes over *state*, not over a list, so a second
    ``loadPlugins`` cannot leave it dispatching to a stale registry.
    """
    original = textrender.Renderer
    # a feature may answer "you draw this" -- and then it must be *this*
    # function it is handed: passing the wrapper would ask the hooks again,
    # about text a feature has already had its say over
    state.native_renderer = original

    def _renderer(painter, font, x, y, text,
                  alignhorz=-1, alignvert=-1, angle=0, usefullheight=False,
                  doc=None, **kwargs):
        settings = settings_of(font)
        if settings is not None and text and not text.lstrip().startswith('<'):
            for hook in state.text_draw_hooks():
                if not isinstance(settings, hook.target):
                    continue
                try:
                    renderer = hook.callback(
                        painter, font, x, y, text, settings,
                        alignhorz=alignhorz, alignvert=alignvert, angle=angle,
                        usefullheight=usefullheight, doc=doc, **kwargs)
                except Exception as exc:                  # noqa: BLE001
                    # fall through to ordinary text rather than break the plot
                    state.note('%s: %s'
                               % (getattr(hook.target, '__name__', '?'), exc))
                    break
                if renderer is not None:
                    return renderer
        return original(painter, font, x, y, text,
                        alignhorz=alignhorz, alignvert=alignvert, angle=angle,
                        usefullheight=usefullheight, doc=doc, **kwargs)

    return _renderer


def make_svg_renderer(textrender, qt):
    """The platform's drawing: an SVG and the box it occupies, in points.

    One class for every JavaScript feature.  A feature never sees a painter, a
    QFont, a baseline or a point-to-pixel conversion: it says how big the
    drawing is and how far it reaches below the baseline, and this puts it
    where the text would have gone and paints it.

    ``reply`` is what the feature's ``veuszRender`` returned, decoded:
    ``{'svg': ..., 'width': ..., 'height': ..., 'depth': ...}`` in points, or
    ``{'error': ...}`` when the feature could not draw.  A missing box is not
    an error -- the SVG is still drawn, at whatever size it says it is.
    """

    class SvgRenderer(textrender._Renderer):
        """Paints one SVG, placed by the baseline the feature reported."""

        def __init__(self, *args, **kwargs):
            reply = kwargs.pop('reply', None) or {}
            # set before super(): its __init__ calls _initText(), which
            # measures, and that needs all of these.
            self.svg = reply.get('svg') or ''
            self.message = reply.get('error') or ''
            self.width_pt = _as_float(reply.get('width'))
            self.height_pt = _as_float(reply.get('height'))
            self.depth_pt = _as_float(reply.get('depth')) or 0.0
            self.renderer = None
            super().__init__(*args, **kwargs)

        # -- points to pixels ----------------------------------------------

        def _pixperpt(self):
            """How many device pixels one point is, as far as the painter knows."""
            painter = self.painter
            value = getattr(painter, 'pixperpt', None)
            if value:
                return float(value)
            dpi = getattr(painter, 'dpi', None)
            if dpi:
                return float(dpi) / 72.0
            try:
                return painter.device().logicalDpiY() / 72.0
            except Exception:                             # noqa: BLE001
                return 96.0 / 72.0

        # -- the _Renderer interface ---------------------------------------

        def _initText(self, text):
            self.error = self.message
            self.text = text
            self.color = None
            scale = self._pixperpt()
            # Qt cannot draw currentColor, and mishandles zero-width strokes;
            # the painter's own colour is what the feature meant by them.
            markup = qt_safe_svg(self.svg, _pen_color(self.painter))
            if isinstance(markup, str):
                markup = markup.encode('utf-8')
            # and it cannot draw SVG text on a recorded device either, so any
            # text in the drawing becomes outlines first (see the section above)
            size_pt = _font_size_pt(self.font, self.painter)
            em_units = em_in_user_units(markup, self.width_pt or 0, size_pt)
            markup, leftover = svg_text_as_paths(
                qt, markup, self.font.family(), em_units)
            if leftover:
                # only for characters that could not be outlined at all
                markup = cancel_qt_text_factor(qt, markup, self.painter)
            self.renderer = _svg_renderer(qt, markup)
            if self.width_pt is None or self.height_pt is None:
                # no box: fall back on the SVG's own idea of its size
                size = self.renderer.defaultSize() if self.renderer else None
                width = size.width() if size is not None else 1
                height = size.height() if size is not None else 1
                self.w = max(float(width), 1.0)
                self.h = max(float(height), 1.0)
            else:
                self.w = max(self.width_pt * scale, 1.0)
                self.h = max(self.height_pt * scale, 1.0)
            descent = max(self.depth_pt * scale, 0.0)
            self.ascent = max(self.h - descent, 1.0)

        def _getWidthHeight(self):
            # ascent as the total height: the framework's (xi, yi) is then the
            # baseline, which is what the feature reported.
            return self.w, self.ascent, 0.0

        def render(self):
            if self.calcbounds is None:
                self.getBounds()
            painter = self.painter
            painter.save()
            if self.renderer is None or not self.renderer.isValid():
                painter.setPen(qt.QPen(qt.QColor('red')))
                painter.drawText(
                    qt.QRectF(self.xi, self.yi, 400, 100),
                    qt.Qt.AlignmentFlag.AlignLeft
                    | qt.Qt.AlignmentFlag.AlignTop,
                    'cannot draw: %s' % (self.message or 'invalid SVG'))
                painter.restore()
                return self.calcbounds
            painter.translate(self.xi, self.yi)
            painter.rotate(self.angle)
            painter.translate(0.0, -self.ascent)
            self.renderer.render(painter, qt.QRectF(0.0, 0.0, self.w, self.h))
            painter.restore()
            return self.calcbounds

    return SvgRenderer


def _as_float(value):
    """A number from a JavaScript reply, or None.  ``null`` means None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _svg_renderer(qt, markup):
    """A Qt SVG renderer for *markup*, or None if it cannot be built."""
    if not markup:
        return None
    if isinstance(markup, str):
        markup = markup.encode('utf-8')
    try:
        from PyQt6.QtSvg import QSvgRenderer
        return QSvgRenderer(qt.QByteArray(markup))
    except Exception:                                     # noqa: BLE001
        return None


def _erase_attribute(markup, prefix, value_included=True):
    """Remove every ``prefix``, then one following space if there is one.

    ``value_included`` says whether the prefix already reaches the closing
    quote (``fill="currentColor"``) or stops at the opening one
    (``stroke-width="``), in which case the value and its quote go too.  Both
    callers exist, and telling them apart by looking at the prefix does not
    work: ``stroke-width="`` also ends with a quote, and guessing that way
    leaves an orphaned ``0"`` behind -- which is exactly how this was found,
    three bytes longer than it should have been.

    Character for character what the old bridge did: the tests compare this
    path against what it produced, so being tidier here would mean being
    different.
    """
    pos = 0
    while True:
        found = markup.find(prefix, pos)
        if found < 0:
            return markup
        if value_included:
            end = found + len(prefix)
        else:
            stop = markup.find('"', found + len(prefix))
            if stop < 0:
                return markup
            end = stop + 1
        markup = markup[:found] + markup[end:]
        if found < len(markup) and markup[found] == ' ':
            markup = markup[:found] + markup[found + 1:]
        pos = found


def qt_safe_svg(markup, color=None):
    """The same SVG, in the shape QtSvg can actually draw.

    Two things a browser does for free and QtSvg does not:

    * ``fill``/``stroke`` of ``currentColor`` -- which means "the painter's
      colour" -- and a hard-coded ``#000000``.  Both are replaced by the colour
      the caller is drawing in, or dropped when there is none so the SVG's own
      default applies.
    * ``stroke-width``: zero-width strokes are drawn wrongly by QtSvg, so the
      attribute goes and the SVG's default is left to apply.

    This is the platform's business rather than a feature's, because the
    platform is what hands the SVG to Qt.  It is why a feature can return the
    SVG its library produced and nothing else.
    """
    if not markup:
        return markup
    for name in ('fill', 'stroke'):
        for value in ('currentColor', '#000000'):
            pattern = '%s="%s"' % (name, value)
            if color:
                markup = markup.replace(pattern, '%s="%s"' % (name, color))
            else:
                markup = _erase_attribute(markup, pattern)
    return _erase_attribute(markup, 'stroke-width="', value_included=False)


# --------------------------------------------------------------------------
# SVG <text> -> <path>
#
# MathJax draws every character its bundled math font has as a <path>, and
# emits <text> for the ones it does not have -- CJK, a rare symbol, an emoji,
# whenever the chosen font lacks them.  Text is the one thing in the SVG that
# is not a path, and it does not survive Veusz's painting: every widget is
# recorded onto a device and replayed, and Qt sizes SVG text against the paint
# device's resolution rather than in the SVG's own units, and replays it as a
# hairline outline rather than the filled glyph that was drawn.  Measured
# through Veusz's own recording device, inside the installed Veusz:
#
#     <text> straight          216 x  68 px FILLED
#     <text> via recording     478 x 243 px FILLED   (2.2x too big)
#     <path> straight          216 x  68 px FILLED
#     <path> via recording     216 x  68 px FILLED   <- what we want
#
# So it is converted here, once, into the outlines Qt would have used.  The
# conversion is faithful: the same glyphs as <text> and as <path> differ by one
# pixel in 5393.
#
# This belongs to the platform and not to a feature: it is Qt that cannot draw
# the text, and every feature that returns an SVG meets the same problem.
# --------------------------------------------------------------------------

#: glyphs are built at this pixel size and scaled down, so the outlines do not
#: depend on the size that was asked for
_OUTLINE_PX = 1000.0

#: families that name a *kind* of font rather than one.  MathJax writes these
#: for characters its math font lacks, and the text element's own font is
#: substituted for them.
_GENERIC_FAMILIES = frozenset((
    '', 'serif', 'sans-serif', 'monospace', 'cursive', 'fantasy',
    'system-ui', 'ui-serif', 'ui-sans-serif', 'ui-monospace',
    'ui-rounded', 'math', 'emoji', 'fangsong'))

_TEXT_ELEM_RE = re.compile(rb'<text\b([^>]*)>(.*?)</text>', re.S)
_ATTR_RE = re.compile(rb'([A-Za-z-]+)\s*=\s*"([^"]*)"')
_TEXT_SIZE_RE = re.compile(rb'font-size="([0-9.]+)px"')

#: converted SVG by (markup, family, em); cleared wholesale when it grows
_CONVERTED = {}


def _svg_path_data(qt, path):
    """A ``QPainterPath`` as SVG path data (glyph contours are closed)."""
    parts = []
    move = qt.QPainterPath.ElementType.MoveToElement
    line = qt.QPainterPath.ElementType.LineToElement
    curve = qt.QPainterPath.ElementType.CurveToElement
    count = path.elementCount()
    index = 0
    while index < count:
        element = path.elementAt(index)
        if element.type == move:
            if parts:
                parts.append('Z')
            parts.append('M%.2f %.2f' % (element.x, element.y))
        elif element.type == line:
            parts.append('L%.2f %.2f' % (element.x, element.y))
        elif element.type == curve:
            one = path.elementAt(index + 1)
            two = path.elementAt(index + 2)
            parts.append('C%.2f %.2f %.2f %.2f %.2f %.2f'
                         % (element.x, element.y, one.x, one.y,
                            two.x, two.y))
            index += 2
        index += 1
    parts.append('Z')
    return ' '.join(parts)


def em_in_user_units(markup, width_pt, size_pt):
    """How many SVG user units one em is worth in this drawing.

    MathJax lays its maths out with one em equal to the size that was asked
    for, and the platform paints the SVG into a rect of the reported box, so
    the box and the viewBox give the scale.  ``None`` when the SVG does not
    say, and then the drawing's own font size is used instead.
    """
    match = re.search(rb'viewBox="([^"]*)"', markup)
    if match is None or not width_pt or not size_pt or width_pt <= 0 \
            or size_pt <= 0:
        return None
    try:
        viewbox_width = float(match.group(1).split()[2])
    except (IndexError, ValueError):
        return None
    if viewbox_width <= 0:
        return None
    return size_pt * (viewbox_width / width_pt)


def _text_to_path_data(qt, text, attrs, label_family, em_units):
    """Outline for one ``<text>`` element, in the element's own coordinates.

    ``em_units`` is what one em is worth in this SVG's user units.  MathJax
    sizes the text of a character it has no glyph for as two ex of the chosen
    math font, so the same CJK comes out 17% larger under a font whose ex is
    0.527 of an em than under one at 0.441 -- and 12% smaller than a plain
    label of the same size.  Drawing it at one em instead makes those
    characters match the text around them, in every math font.
    """
    family = (attrs.get('font-family') or '').split(',')[0]
    family = family.strip().strip('\'"')
    # MathJax writes a *generic* family for the characters its own math font
    # does not have -- it has no idea what the figure is set in.  Draw those in
    # the font this text element is set in instead, so a formula's CJK matches
    # the text around it.  An explicit family from the library still wins.
    if family.lower() in _GENERIC_FAMILIES and label_family:
        family = label_family
    size = (attrs.get('font-size') or '').strip().rstrip('px')
    try:
        size = float(size)
    except ValueError:
        return None
    if em_units is None or em_units <= 0:
        em_units = size
    if size <= 0 or em_units <= 0 or not text:
        return None

    font = qt.QFont(family) if family else qt.QFont()
    font.setPixelSize(int(_OUTLINE_PX))
    weight = (attrs.get('font-weight') or '').strip().lower()
    if weight in ('bold', 'bolder') or (weight.isdigit()
                                       and int(weight) >= 600):
        font.setBold(True)
    if (attrs.get('font-style') or '').strip().lower() in ('italic', 'oblique'):
        font.setItalic(True)
    # outlines, not hinted bitmaps: this is geometry, and it has to match what
    # Qt's SVG renderer would have drawn
    strategy = qt.QFont.StyleStrategy
    font.setStyleStrategy(strategy.PreferOutline | strategy.ForceOutline)
    if hasattr(font, 'setHintingPreference'):
        font.setHintingPreference(qt.QFont.HintingPreference.PreferNoHinting)

    # QPainterPath.addText does not fall back to another font for a character
    # this one lacks -- it would draw a box.  Qt's own SVG text route would
    # find a system font, so those are left to it.
    try:
        metrics = qt.QFontMetrics(font)
        if any(not metrics.inFontUcs4(ord(char)) for char in text):
            return None
    except Exception:                                     # noqa: BLE001
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
    return _svg_path_data(qt, path).encode('utf-8')


def svg_text_as_paths(qt, markup, label_family='', em_units=None):
    """Every ``<text>`` in *markup* replaced by equivalent ``<path>`` outlines.

    Returns ``(markup, leftover)``: ``leftover`` is True when some element
    could not be converted, and is left as text -- the caller then has to undo
    Qt's device-dpi scaling of it.

    The element's own ``transform`` is kept, or the glyphs come out flipped:
    MathJax wraps the formula in ``scale(1,-1)`` and each text element in
    ``scale(1,-1)`` again, so text-local coordinates (y down, baseline at the
    origin, which is what ``QPainterPath.addText`` produces too) land upright
    only through the second flip.
    """
    key = (markup, label_family, em_units)
    cached = _CONVERTED.get(key)
    if cached is not None:
        return cached
    if b'<text' not in markup:
        _CONVERTED[key] = (markup, False)
        return markup, False
    state = {'leftover': False}

    def one_element(match):
        attrs = dict((k.decode('ascii', 'replace'), v.decode('utf-8'))
                     for k, v in _ATTR_RE.findall(match.group(1)))
        text = html.unescape(match.group(2).decode('utf-8'))
        try:
            data = _text_to_path_data(qt, text, attrs, label_family, em_units)
        except Exception:                                 # noqa: BLE001
            data = None
        if data is None:
            state['leftover'] = True
            return match.group(0)
        transform = attrs.get('transform')
        if transform:
            return (b'<path transform="' + transform.encode('utf-8')
                    + b'" d="' + data + b'"/>')
        return b'<path d="' + data + b'"/>'

    converted = _TEXT_ELEM_RE.sub(one_element, markup)
    if len(_CONVERTED) > 128:
        _CONVERTED.clear()
    _CONVERTED[key] = (converted, state['leftover'])
    return converted, state['leftover']


def qt_text_factor(qt, painter):
    """What Qt multiplies SVG ``<text>`` font sizes by, or 1.0 if it will not.

    Only Veusz's recording device reports the page dpi in that metric.  A
    QImage reports 72 (so there is nothing to cancel), and the QPicture
    fallback used when Veusz's native recording device is missing is not scaled
    this way at all, so both are left alone.
    """
    try:
        device = painter.device()
        if type(device).__name__ != 'RecordPaintDevice':
            return 1.0
        dpi = float(device.metric(qt.QPaintDevice.PaintDeviceMetric.PdmDpiY))
    except Exception:                                     # noqa: BLE001
        return 1.0
    return dpi / 72.0 if dpi > 0 else 1.0


def cancel_qt_text_factor(qt, markup, painter):
    """Undo Qt's device-dpi scaling of any ``<text>`` left in *markup*.

    Only needed for characters whose outlines could not be built above.
    """
    factor = qt_text_factor(qt, painter)
    if abs(factor - 1.0) < 1e-6 or b'<text' not in markup:
        return markup
    inverse = 1.0 / factor

    def one_tag(match):
        def one_size(size):
            try:
                value = float(size.group(1))
            except ValueError:
                return size.group(0)
            return b'font-size="%gpx"' % (value * inverse)
        return _TEXT_SIZE_RE.sub(one_size, match.group(0))

    return re.compile(rb'<text\b[^>]*>').sub(one_tag, markup)


# --------------------------------------------------------------------------
# text of the drawing's own
#
# A drawing may contain text that is set in the *figure's* font rather than in
# the library's: MathJax writes ``\text{...}`` that way, and so does anything
# else that means "upright text, the reader's font".  Only Qt can shape that --
# it has to find the family, fall back per character, kern, and produce
# outlines -- so the platform does it and the feature asks.
#
# The shapes of the two halves are different and that is the whole design:
#
#   the feature knows *which* text it needs, because only it knows what its
#   drawing says;
#   the platform knows *how to shape* it, because only it has Qt.
#
# So a feature answers a render request with the runs it wants measured, the
# platform shapes them in the element's own font, and the feature is asked
# again with the measurements.  Nothing is cached across that boundary except
# the outlines themselves, which do not depend on the size (they are built at
# one size and scaled).
# --------------------------------------------------------------------------


class TextOutlineUnavailable(RuntimeError):
    """The font this text is set in has glyphs but no outlines.

    A raster-only font draws fine as text and cannot be turned into a path, so
    the feature is told to drop its half-built drawing and render without its
    own text -- which is better than a blank space where the words should be.
    """


#: families already complained about, so the log gets one line each
_WARNED_FONTS = set()

#: outlined runs by (font key, text, variant, bold, italic); cleared wholesale
#: when it grows
_TEXT_RUNS = {}


def _warn_unoutlinable(family, text):
    """Say once, in the log, that a font cannot be outlined.

    Once per family: the message is about the font, and a formula would
    otherwise write it on every repaint.
    """
    if family in _WARNED_FONTS:
        return
    _WARNED_FONTS.add(family)
    _warn('veusz-js-engine: the font %r has no glyph outlines, so text set in '
          'it cannot be drawn inside a formula (it will be drawn in the '
          'drawing\'s own font instead).  %r was the first to hit it.  A '
          'TrueType or OpenType font with outlines avoids this.'
          % (family, text[:40]))


def text_font_key(qt, font):
    """A string that changes whenever *font* would be painted differently.

    ``QFont.toString()`` leaves out several properties that change the picture
    (letter and word spacing, kerning, stretch, the named style, the
    decorations), and a cache keyed on it would hand back another font's
    outlines.
    """
    return '|'.join(str(part) for part in (
        font.toString(), font.styleName(), font.kerning(), font.stretch(),
        font.letterSpacingType().value, font.letterSpacing(),
        font.wordSpacing(), font.capitalization().value,
        font.underline(), font.strikeOut(), font.overline()))


def measure_text_runs(qt, requests, label_font):
    """Shape whole text runs with Qt, in the font the element is set in.

    Returns one entry per request, keyed by the request's own ``key``: its
    advance, height and depth in the units the drawing lays out with, and its
    glyph outlines in the drawing's own coordinates.  Both come from the same
    shaped glyphs, so the space the drawing reserves and the ink drawn in it
    cannot disagree.

    Raises :class:`TextOutlineUnavailable` if the font has no outlines, which
    the caller turns into "draw it without the text" rather than a blank.
    """
    measured = {}
    for request in requests:
        if not isinstance(request, dict) or 'key' not in request:
            continue
        text = request.get('text', '')
        variant = request.get('variant', '')
        bold = request.get('bold', 'bold' in variant)
        italic = request.get('italic', 'italic' in variant)
        cache_key = (text_font_key(qt, label_font), text, variant,
                     bool(bold), bool(italic))
        run = _TEXT_RUNS.get(cache_key)
        if run is None:
            font = qt.QFont(label_font)
            original_px = qt.QFontInfo(font).pixelSize()
            # outlines are built at one size and scaled, so they do not depend
            # on the size that was asked for
            font.setPixelSize(int(_OUTLINE_PX))
            if original_px > 0:
                ratio = _OUTLINE_PX / original_px
                if font.letterSpacingType() == \
                        qt.QFont.SpacingType.AbsoluteSpacing:
                    font.setLetterSpacing(font.letterSpacingType(),
                                          font.letterSpacing() * ratio)
                font.setWordSpacing(font.wordSpacing() * ratio)
            if bold or italic:
                # emphasis augments the element's font; the family stays the
                # figure's, including for textsf/texttt, and a named Regular
                # face would otherwise override setBold/setItalic
                font.setStyleName('')
            if bold:
                font.setBold(True)
            if italic:
                font.setItalic(True)
            font.setStyleStrategy(qt.QFont.StyleStrategy.PreferOutline)
            font.setHintingPreference(
                qt.QFont.HintingPreference.PreferNoHinting)

            layout = qt.QTextLayout(text, font)
            layout.beginLayout()
            line = layout.createLine()
            if line.isValid():
                line.setLineWidth(1e9)
            layout.endLayout()

            path = qt.QPainterPath()
            advance = 0.0
            if line.isValid():
                advance = line.horizontalAdvance()
                baseline = line.ascent()
                # each glyph run carries its own fallback face and shaped
                # positions, so kerning, ligatures, RTL and CJK survive
                for glyph_run in layout.glyphRuns():
                    raw = glyph_run.rawFont()
                    positions = glyph_run.positions()
                    for glyph, position in zip(glyph_run.glyphIndexes(),
                                               positions):
                        outline = raw.pathForGlyph(glyph)
                        moved = qt.QTransform.fromTranslate(
                            position.x(), position.y() - baseline)
                        path.addPath(moved.map(outline))

            # decorations are not part of a glyph outline, so the element's
            # underline and friends are rectangles
            metrics = qt.QFontMetricsF(font)
            thickness = max(metrics.lineWidth(), 1.0)
            for enabled, y in ((font.underline(), metrics.underlinePos()),
                               (font.strikeOut(), -metrics.strikeOutPos()),
                               (font.overline(), -metrics.overlinePos())):
                if enabled and advance > 0:
                    path.addRect(0.0, y - thickness / 2.0, advance, thickness)

            box = path.boundingRect()
            # a raster-only font has glyphs but no outlines, so the run would
            # occupy its advance and draw nothing.  A run that is all
            # whitespace legitimately has no outline.
            if path.isEmpty() and text.strip():
                _warn_unoutlinable(label_font.family(), text)
                raise TextOutlineUnavailable(
                    'no glyph outlines for %r in %r'
                    % (text[:40], label_font.family()))
            run = {
                # advance is not ink width: this keeps spaces and italic
                # bearings without shifting the origin or pushing the next run
                'w': advance / _OUTLINE_PX,
                'h': max(0.0, -box.top()) / _OUTLINE_PX,
                'd': max(0.0, box.bottom()) / _OUTLINE_PX,
                'path': _svg_path_data(qt, path) if not path.isEmpty() else '',
            }
            if len(_TEXT_RUNS) >= 512:
                _TEXT_RUNS.clear()
            _TEXT_RUNS[cache_key] = run
        measured[request['key']] = run
    return measured


#: how much of a JavaScript file is read to see what it declares.  A font's
#: data is megabytes of glyph outlines with a line of description at the top,
#: so the head is all anyone needs to list it.
HEAD_BYTES = 16384


def feature_js_heads(feature_dir):
    """The JavaScript a feature carries, and the head of each.

    Two places: the files beside its entry point, which run when it loads, and
    the ``fonts/`` directory beside them, which does **not** -- a font's data
    is large and a feature may offer a dozen fonts, so one is read only when
    something asks for it (see ``load`` in :meth:`Platform.feature_runtime`).

    JavaScript cannot read a file, so the heads are handed over instead.  What
    is *in* a head -- how a font describes itself -- is the feature's own
    convention; the platform only passes the text along.
    """
    if feature_dir is None:
        return []
    directory = Path(feature_dir)
    found = []
    for path in sorted(directory.glob('*.js')) + \
            sorted((directory / 'fonts').glob('*.js')):
        try:
            head = path.read_bytes()[:HEAD_BYTES].decode('utf-8', 'replace')
        except OSError:
            continue
        found.append({'file': path.relative_to(directory).as_posix(),
                      'head': head})
    return found


# --------------------------------------------------------------------------
# feature plugins: the drop-in half
# --------------------------------------------------------------------------

def feature_name(path):
    """A feature's name: its directory's, or the file's for a one-file feature.

    The name is what identifies a feature -- it is what order is decided by,
    and what keeps the same feature from being loaded twice.
    """
    path = Path(path)
    if path.name in _FEATURE_ENTRIES:
        return path.parent.name
    return path.stem


#: a directory holding one of these is a feature, and the file is its entry
_FEATURE_ENTRIES = ('feature.js', 'feature.py')

#: the platform's own JavaScript, run in a feature's runtime before the
#: feature's files, so that ``veusz`` exists by the time they run
JS_API_FILE = 'jsapi.js'


def find_feature_dirs(here):
    """Where **feature** plugins live: the drop-in half of the platform.

    A feature is a plugin that builds on this one.  Veusz itself loads only the
    plugins it is told about, one file at a time in Preferences -> Plugins, so
    without this a user would have to add one entry per feature *and* get their
    order right.  The platform scans these directories instead, so the user
    adds one plugin and everything else is dropped in.

    ``features/`` beside the plugin, and a sibling one level up so several
    plugins can share one installation; ``VEUSZ_JS_ENGINE_FEATURES`` adds more.
    """
    directories = []
    if here is not None:
        here = Path(here)
        directories += [here / 'features', here.parent / 'features']
    extra = os.environ.get('VEUSZ_JS_ENGINE_FEATURES') or ''
    for item in extra.split(os.pathsep):
        if item:
            directories.append(Path(item))
    return directories


def _feature_entries(directory):
    """The features in *directory*, in load order.

    Each entry is ``(name, entry_point, feature_dir)``.  ``feature_dir`` is the
    feature's own directory, or None for a one-file feature -- it is where the
    feature keeps everything that is its business, its JavaScript included.

    A feature is **a directory with a ``feature.js`` or ``feature.py`` in it**
    -- the norm, because a feature usually has more than one file: a bundle
    beside its entry point, assets, a README.  Everything in the directory
    belongs to the feature, and only the entry point is executed.

    ``feature.js`` is preferred when both are there: a feature that can be
    written in JavaScript should be, and the Python entry point exists only for
    a feature that has to reach into Veusz itself.

    A single ``.py`` or ``.js`` file is accepted too, as shorthand for a
    one-file feature.

    Order is the **name's** (the directory's, or the file's), so it is decided
    without running anything.  A leading underscore or dot means "not a
    feature", so helpers and README directories can sit here.
    """
    entries = []
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return entries
    for child in children:
        if child.name.startswith('_') or child.name.startswith('.'):
            continue
        if child.is_dir():
            for entry_name in _FEATURE_ENTRIES:
                entry = child / entry_name
                if entry.is_file():
                    entries.append((child.name, entry, child))
                    break
        elif child.suffix.lower() in ('.py', '.js'):
            entries.append((child.stem, child, None))
    return entries


def _js_load_order(entry, feature_dir):
    """The JavaScript files of a feature, in the order they must run.

    ``feature.js`` is the entry point and goes **last**; every other ``*.js``
    beside it is loaded before it, in name order.  That is what lets a feature
    be a bundle plus a thin wrapper without a manifest to say so, and it keeps
    the platform's rule that order comes from the name and is decided without
    running anything.

    A one-file feature is just its own file.
    """
    entry = Path(entry)
    if feature_dir is None:
        return [entry]
    directory = Path(feature_dir)
    others = sorted(p for p in directory.glob('*.js') if p.name != entry.name)
    # fonts/*.js is deliberately absent: a font's data is large and a feature
    # may carry many, so it is read when something asks for that font, not on
    # the chance that it will be
    return others + [entry]


# --------------------------------------------------------------------------
# a JavaScript feature: what it declares, and how it is asked
# --------------------------------------------------------------------------

#: what kind of thing a feature may apply to.  ``text`` means every text
#: element -- an axis label, a tick number, a key -- has its own copy of the
#: properties, and the renderer is asked once per element.
JS_TARGETS = ('text',)


class JsFeature(object):
    """One feature written in JavaScript, as the platform sees it.

    It is built from the feature's own declaration (``veuszDescribe()``), which
    is the only thing the platform needs from it: what to call it, what it
    applies to, and which properties to add.  Then the platform owns every part
    of the Veusz side -- the settings, the properties panel, the seam that asks
    it to draw -- so the feature itself contains no Veusz at all.
    """

    def __init__(self, name, title, target, properties, entry, runtime,
                 renderer_class, version=None, feature_dir=None):
        self.name = name
        self.title = title
        self.target = target
        self.properties = properties
        self.entry = entry
        #: the directory the feature owns, or None for a one-file feature
        self.feature_dir = feature_dir
        self.runtime = runtime
        self.renderer_class = renderer_class
        self.version = version

    def setting_name(self, prop):
        """The Veusz setting one of this feature's properties is stored as.

        A property has two names because it has two audiences, and the feature
        declares both.  Its ``name`` is the feature's own handle: what the
        request reports back, and what the feature's JavaScript uses.  Its
        ``setting`` is what Veusz writes into the document -- so it is the
        name that outlives the feature, and only the feature can decide it.

        ``setting`` is optional.  Left out, the platform qualifies the name
        with the feature's own (``mathjax_on``), which is what stops two
        features from colliding over something as short as ``on``; a feature
        that wants the document's own words names them itself.
        """
        return prop.get('setting') or '%s_%s' % (self.name, prop['name'])

    def read(self, settings):
        """This feature's property values for one text element, or None.

        None means the element does not carry them at all -- it was not built
        from a settings group this feature was added to -- which is a decline,
        not an error.
        """
        values = {}
        for prop in self.properties:
            try:
                values[prop['name']] = settings.get(
                    self.setting_name(prop)).val
            except Exception:                             # noqa: BLE001
                return None
        return values

    def render(self, text, size_pt, color, props, face=None, measured=None,
               discard=False):
        """Ask the feature to draw *text*; the raw reply, or '' to decline.

        ``face`` identifies the font the text element is set in.  A feature
        whose drawing contains text of its own needs it, because its result
        depends on that font and it cannot see the font itself.

        ``measured`` answers a reply that asked for text to be shaped, and
        ``discard`` tells the feature to forget a half-built drawing -- the one
        case where the platform cannot do what it was asked.
        """
        request = {
            'text': text,
            'size': size_pt,
            'color': color,
            'props': props,
            'feature': self.name,
            'version': self.version,
        }
        if face is not None:
            request['face'] = face
        if measured is not None:
            request['measured'] = measured
        if discard:
            request['discard'] = True
        return self.runtime.call('veuszRender', json.dumps(request))


def _decode_reply(raw):
    """What the feature returned, as a dict, or None if it declined.

    Three shapes are accepted, in the order a feature is likely to reach for
    them: nothing at all (decline), an SVG on its own (the common case, and the
    box is then whatever the SVG says), or the JSON envelope ``veusz.svg()``
    builds, which carries the exact box in points.
    """
    if not raw or not raw.strip():
        return None
    text = raw.lstrip()
    if text.startswith('{'):
        try:
            reply = json.loads(text)
        except ValueError:
            return {'svg': raw}
        if not isinstance(reply, dict):
            return {'svg': raw}
        return reply
    return {'svg': raw}


def _painter_pixperpt(painter):
    """How many device pixels one point is, as far as the painter knows."""
    value = getattr(painter, 'pixperpt', None)
    if value:
        return float(value)
    dpi = getattr(painter, 'dpi', None)
    if dpi:
        return float(dpi) / 72.0
    try:
        return painter.device().logicalDpiY() / 72.0
    except Exception:                                     # noqa: BLE001
        return 96.0 / 72.0


def _font_size_pt(font, painter):
    """The point size of *font*, whichever way Qt recorded it."""
    try:
        size = font.pointSizeF()
    except Exception:                                     # noqa: BLE001
        size = None
    if size is None or size <= 0:
        try:
            size = font.pixelSize() / _painter_pixperpt(painter)
        except Exception:                                 # noqa: BLE001
            size = 12.0
    return max(float(size), 1.0)


def _pen_color(painter):
    """The painter's colour as ``#rrggbb``, or None if it draws nothing."""
    try:
        pen = painter.pen()
        if pen.style() != pen.style().NoPen:
            return pen.color().name()
    except Exception:                                     # noqa: BLE001
        return None
    return None


def _property_setting(setting, kind, name, spec):
    """A Veusz setting for one declared property.

    Which Veusz class a property becomes is the platform's business, and so is
    the label: a feature says ``switch`` and gets a checkbox, and never learns
    that Veusz calls it ``Bool``.
    """
    label = spec.get('label') or name
    descr = spec.get('descr') or ''
    default = spec.get('default')
    args = {'usertext': label}
    if descr:
        args['descr'] = descr
    if spec.get('hidden'):
        # a property that shares another's row is hidden as a row of its own;
        # a feature may also ask for that on its own behalf
        args['hidden'] = True
    if kind == 'switch':
        return setting.Bool(name, bool(default), **args)
    if kind == 'choice':
        choices = spec.get('choices') or []
        values = [c.get('value') if isinstance(c, dict) else c
                  for c in choices]
        labels = [c.get('label') if isinstance(c, dict) else str(c)
                  for c in choices]
        if not values:
            raise JsEngineError('choice %r declares no choices' % name)
        if default not in values:
            default = values[0]
        return setting.Choice(name, values, default, uilist=labels, **args)
    if kind == 'text':
        return setting.Str(name, '' if default is None else str(default),
                           **args)
    if kind == 'number':
        return setting.Float(name, 0.0 if default is None else float(default),
                             **args)
    raise JsEngineError('unknown property kind %r for %r' % (kind, name))


def _row_control_class(qt):
    """A widget holding several settings' own controls, side by side.

    Qt owns the controls and the settings own the values, so this adds nothing
    but a row: each member is asked for the control the panel would have made
    for it, and they are laid out next to one another.  Because they are
    Veusz's own controls, each keeps itself in step with its setting -- a change
    made from the console, by an undo, or by another view of the same document
    follows into the widget with no code here at all -- and one signal per
    change is all the panel asks for.

    The first member owns the row: the panel has already written its name in
    the left column, so its control is drawn bare (*own_control* is that
    setting's own ``makeControl``, from before this row existed -- asking the
    row again would make a row inside itself), and every other member carries
    its own label: a checkbox says it, anything else gets one beside it.
    """

    class RowControl(qt.QWidget):
        """One row of the properties panel, for several settings.

        Built from the settings of *one* settings tree, which is why the row's
        setting constructs it rather than a fixed list of members being closed
        over: a settings tree owns a copy of every setting per instance, and a
        control that wrote to the prototype would move every text element's
        settings at once.
        """

        # what the panel connects to its own change handler; forwarded from
        # whichever member control moved, with that control as the sender
        sigSettingChanged = qt.pyqtSignal(qt.QObject, object, object)

        def __init__(self, members, own_control, parent=None):
            qt.QWidget.__init__(self, parent)
            # the row fills the panel, so whatever can take the slack does
            self.setSizePolicy(qt.QSizePolicy.Policy.Expanding,
                               qt.QSizePolicy.Policy.Preferred)
            layout = qt.QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            #: the members' controls, in declaration order -- the labels beside
            #: them are decoration and are not in here
            self.controls = []
            for index, (member, spec) in enumerate(members):
                control = (own_control if index == 0
                           else member.makeControl(None))
                if control is None:
                    continue
                if index:
                    text = (spec.get('label') or member.usertext
                            or member.name)
                    if isinstance(control, qt.QCheckBox):
                        # a checkbox can carry its own name
                        control.setText(text)
                    else:
                        layout.addWidget(qt.QLabel(text, self))
                if spec.get('descr'):
                    control.setToolTip(spec['descr'])
                control.sigSettingChanged.connect(self.sigSettingChanged)
                # a menu or a field takes the width of the row; a checkbox
                # keeps its own size, which is what the original plugin's row
                # did with its font menu
                layout.addWidget(control,
                                 0 if isinstance(control, qt.QCheckBox) else 1)
                self.controls.append(control)
            self.setLayout(layout)

    return RowControl


def _merge_into_one_row(qt, members):
    """Put several declared properties in one row of the panel.

    *members* is ``[(setting, declaration), ...]`` in the order the feature
    declared them.  The first owns the row -- the panel writes its name on the
    left and the control it makes *is* the row -- and the rest are hidden as
    rows of their own.

    This is presentation and nothing else: every member is still an ordinary
    setting of the document, saved under its own name, set from the console,
    and undone like any other.  A feature asks for it by naming the same
    ``row`` on each property.
    """
    names = [member.name for member, _spec in members]
    specs = [spec for _member, spec in members]
    prototype = dict(zip(names, [member for member, _spec in members]))
    for member, _spec in members[1:]:
        member.hidden = True
    base = type(members[0][0])
    base_make_control = base.makeControl
    control_class = _row_control_class(qt)

    class RowSetting(base):
        """The row's own setting: the control it makes is the whole row."""

        def makeControl(self, *args, **kwargs):
            # the members are this instance's own copies of the settings, found
            # by name in the group this one was added to
            group = getattr(self, 'parent', None)
            found = []
            for name, spec in zip(names, specs):
                member = None
                if group is not None and name in group:
                    member = group.get(name)
                found.append((member or prototype[name], spec))
            return control_class(found, base_make_control(self, None), *args)

    members[0][0].__class__ = RowSetting


def load_feature_file(feature, name):
    """Read one of a feature's deferred JavaScript files, once.

    A feature's ``fonts/`` is not read when the feature loads -- one font's
    data can be megabytes, and a feature may offer a dozen -- so a feature asks
    for the one it needs and gets it here.

    The name comes from JavaScript, so it is checked rather than trusted, and
    the check is narrow: a ``.js`` file under the feature's own ``fonts/``.
    Nothing else is ever deferred, so nothing else may be asked for -- which
    also keeps a feature from reading the disk, or from re-running its own
    entry point by naming it.
    """
    directory = feature.feature_dir
    if directory is None:
        raise JsEngineError('%s is a one-file feature, and has nothing to '
                            'load' % feature.name)
    directory = Path(directory).resolve()
    fonts = directory / 'fonts'
    candidate = (directory / str(name)).resolve()
    try:
        candidate.relative_to(fonts)
    except ValueError:
        raise JsEngineError('%s asked for a file outside its fonts/: %r'
                            % (feature.name, name))
    if candidate.suffix.lower() != '.js' or not candidate.is_file():
        raise JsEngineError('%s asked for a font file it does not have: %r'
                            % (feature.name, name))
    if candidate in feature.runtime.loaded_scripts:
        return
    feature.runtime.eval_file(candidate)
    feature.runtime.loaded_scripts.add(candidate)


def install_js_feature(platform, entry, feature_dir, name):
    """Install one JavaScript feature from what it declares about itself.

    Everything Veusz-shaped lives here: the feature's JavaScript is asked what
    it wants, and the platform makes it so.  A feature that fails to install
    leaves a note and changes nothing, so one broken file does not take the
    others down with it.
    """
    import veusz.qtall as qt
    from veusz import setting
    from veusz.setting import collections
    from veusz.utils import textrender

    runtime = platform.feature_runtime(entry, feature_dir)
    raw = runtime.call('veuszDescribe', '')
    declaration = json.loads(raw) if raw.strip() else {}
    if not isinstance(declaration, dict):
        raise JsEngineError('%s: veuszDescribe() did not return an object'
                            % entry.name)

    target = declaration.get('target') or 'text'
    if target not in JS_TARGETS:
        raise JsEngineError(
            '%s: unknown target %r (this platform knows %s)'
            % (entry.name, target, ', '.join(JS_TARGETS)))

    properties = declaration.get('properties') or []
    if not isinstance(properties, list):
        raise JsEngineError('%s: properties must be a list' % entry.name)

    target_class = collections.Text
    renderer_class = platform._svg_renderer_class()

    feature = JsFeature(
        name=declaration.get('name') or name,
        title=declaration.get('title') or name,
        target=target,
        properties=properties,
        entry=Path(entry),
        runtime=runtime,
        renderer_class=renderer_class,
        version=declaration.get('version'),
        feature_dir=feature_dir)

    declared = []
    for prop in properties:
        kind = prop.get('kind')
        prop_name = prop.get('name')
        if not prop_name:
            raise JsEngineError('%s: a property has no name' % entry.name)
        declared.append((_property_setting(
            setting, kind, feature.setting_name(prop), prop), prop))

    # Properties that name the same `row` become one row of the panel, in the
    # order they were declared -- the first of them owns it.  A row of one is
    # left alone: its own control is what the panel would have made anyway.
    rows = {}
    for item in declared:
        row = item[1].get('row')
        if row:
            rows.setdefault(row, []).append(item)
    for members in rows.values():
        if len(members) > 1:
            _merge_into_one_row(qt, members)

    for member, _prop in declared:
        platform.add_setting(target_class, None, member)

    def draw_text(painter, font, x, y, text, settings, **kwargs):
        props = feature.read(settings)
        if props is None:
            return None
        size_pt = _font_size_pt(font, painter)
        color = _pen_color(painter)
        face = text_font_key(qt, font)
        reply = _decode_reply(feature.render(text, size_pt, color, props,
                                             face=face))
        if reply is None:
            return None
        if 'load' in reply:
            # the feature wants the data for a font it offers; it is read once
            # and then the question is asked again, because the answer may
            # differ now that the font is registered
            try:
                load_feature_file(feature, reply['load'])
            except (JsEngineError, OSError) as exc:
                # A font file that will not load must not take the frame down
                # with it, and the formula must not be drawn in the wrong font
                # either: say why, on the drawing and in the report.  This is
                # what a file built for another bundle looks like.
                message = '%s could not be read: %s' % (reply['load'], exc)
                platform.state.note('%s: %s' % (feature.name, message))
                return renderer_class(painter, font, x, y, text,
                                      reply={'error': message}, **kwargs)
            reply = _decode_reply(feature.render(text, size_pt, color, props,
                                                 face=face))
            if reply is None:
                return None
        if 'measure' in reply:
            # the drawing contains text of its own, and only the platform can
            # shape it in the element's font
            try:
                measured = measure_text_runs(qt, reply['measure'], font)
            except TextOutlineUnavailable as exc:
                # that font has no outlines: tell the feature to drop what it
                # built and draw without its own text, rather than draw blank
                platform.state.note(str(exc))
                reply = _decode_reply(feature.render(
                    text, size_pt, color, props, face=face, discard=True))
            else:
                reply = _decode_reply(feature.render(
                    text, size_pt, color, props, face=face,
                    measured=measured))
            if reply is None:
                return None
        if reply.get('note'):
            platform.state.note('%s: %s' % (feature.name, reply['note']))
        delegated = reply.get('delegate')
        if delegated:
            # The feature says: Veusz, draw *this* text yourself.  What comes
            # back is the native renderer, so a MathML document among it is
            # drawn by Veusz's own MathML widget, in the element's font, with
            # its own error text if it does not understand it -- the platform
            # never learns what a `<math>` element is.
            native = platform.state.native_renderer
            if native is None:
                raise JsEngineError('%s asked Veusz to draw its text, but the '
                                    'platform has not replaced the renderer '
                                    'yet' % feature.name)
            return native(painter, font, x, y, delegated, **kwargs)
        return renderer_class(painter, font, x, y, text, reply=reply,
                              **kwargs)

    platform.hook_draw(target_class, draw_text)
    platform._features.append(feature)
    return feature


def load_features(platform, here=None):
    """Load the features found in the feature directories.

    A feature is loaded by the **platform**, never pointed at in Veusz: that is
    what makes "one plugin in Preferences, everything else dropped in" true.

    A feature may be written in **JavaScript** (``feature.js``, the norm, and
    the whole point of the platform: the engine runs JavaScript, so the person
    writing a feature should not have to write Python or know Veusz) or in
    **Python** (``feature.py``, for a feature that has to reach into Veusz
    itself).  The JavaScript one is asked what it wants and the platform builds
    it; the Python one is executed and does its own building.

    A Python feature is read as **UTF-8**, which is worth noting: Veusz opens a
    plugin with no encoding and therefore needs ASCII, but a feature loaded
    from here may contain non-ASCII.  (This plugin is still read by Veusz, so
    it must stay ASCII.)

    One feature failing does not stop the others: the failure is recorded and
    the rest are still loaded.
    """
    state = platform.state
    loaded, failed = [], []
    for directory in find_feature_dirs(here if here is not None else platform.here):
        if not directory.is_dir():
            continue
        for name, plugin, feature_dir in _feature_entries(directory):
            if name in state.feature_names:
                # This feature is already up, or a second directory offered one
                # of the same name.  Skipping avoids paying the JavaScript
                # parse again; it is no longer load-bearing for correctness,
                # since settings are keyed by name and draw hooks are now
                # idempotent for the same callback.
                continue
            try:
                if plugin.suffix.lower() == '.js':
                    install_js_feature(platform, plugin, feature_dir, name)
                else:
                    _run_python_feature(plugin, name)
            except Exception as exc:                      # noqa: BLE001
                failed.append(plugin)
                state.note('feature %s failed: %s' % (name, exc))
            else:
                loaded.append(plugin)
                state.features.append(plugin)
                state.feature_names.add(name)
    return loaded, failed


def _run_python_feature(plugin, name):
    """Execute one ``feature.py`` the way a plugin would be executed."""
    source = plugin.read_text(encoding='utf-8')
    # __file__ is provided, so a feature can find its own directory (and its
    # assets); Veusz's exec gives empty globals and no __file__, which is why
    # plugins usually have to walk the stack for it.
    namespace = {
        '__file__': str(plugin),
        '__name__': 'veusz_js_engine_feature_%s' % name,
    }
    exec(compile(source, str(plugin), 'exec'), namespace)


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def install(verbose=True):
    """Install the platform: the QuickJS host, the plumbing, self-publication.

    This installs no feature of its own: it makes the platform ready and then
    loads whatever features it finds beside it.  There is nothing to check
    here -- a feature that needs a binary the platform does not have gets a
    clear error when it asks (see :meth:`Platform.feature_runtime`).

    Calling it twice is safe and does not wrap Veusz twice: the state lives on
    ``veusz.utils``, where there is only one.  Veusz does call ``loadPlugins``
    more than once, and each call executes this file in a fresh namespace.
    """
    here = _plugin_dir()

    import veusz.qtall as qt
    import veusz.utils as utils
    from veusz.document import thefactory
    from veusz.setting import collections
    from veusz.utils import textrender

    state = get_state()

    # Re-installing must reuse the platform that is already up: a fresh one
    # would throw away every runtime that had been started.
    existing = getattr(utils, PUBLISH_ATTR, None)
    if existing is not None:
        platform = existing
        reused = True
    else:
        platform = Platform(here, state=state)
        reused = False

    # publish first, so that if the plumbing below throws, a feature still
    # finds a host rather than nothing
    state.platform = platform
    setattr(utils, PUBLISH_ATTR, platform)

    if not state.hooked:
        state.hooked = True
        _hooked_makeQFont(collections, state)
        wrapped = _hooked_draw(thefactory, state)
        renderer = make_renderer_wrapper(textrender, qt, state)
        textrender.Renderer = renderer
        utils.Renderer = renderer      # widgets resolve the name from here
    else:
        wrapped = []

    # ---- features, once the plumbing is in place ------------------------
    # Loaded last: a feature adds settings and hooks, so the wrapping has to
    # exist first.  They find the platform on veusz.utils, which was set just
    # above.  Loading is idempotent, so a second install is harmless.
    loaded, failed = load_features(platform, here)

    if verbose:
        _say('veusz-js-engine %s: platform%s, %d feature(s)'
             % (__version__, ' (already up)' if reused else '', len(loaded)))
        _say('  engine  : %s'
             % (platform.quickjs or '(MISSING -- the engine is not there)'))
        _say('  features: %s'
             % (', '.join(feature_name(p) for p in loaded) or '(none)'))
        if failed:
            _say('  FAILED  : %s'
                 % ', '.join(feature_name(p) for p in failed))
        _say('  widgets : %d wrapped' % len(wrapped))
        _say('  published at veusz.utils.%s' % PUBLISH_ATTR)
    _write_log(here, platform, state)
    return platform


def _write_log(here, platform, state):
    if here is None:
        return
    try:
        lines = ['veusz-js-engine %s installed at %s'
                 % (__version__, time.strftime('%Y-%m-%d %H:%M:%S')),
                 'engine: %s' % (platform.quickjs or '(not found)')]
        for path in state.features:
            lines.append('feature: %s  (%s)' % (feature_name(path), path))
        for note in state.notes:
            lines.append('note: %s' % note)
        (here / 'veusz_js_engine.log').write_text('\n'.join(lines) + '\n',
                                                  encoding='utf-8')
    except Exception:
        pass


def _auto_install():
    try:
        install()
    except Exception as exc:                              # noqa: BLE001
        _warn('veusz-js-engine: not installed: %s' % exc)
        try:
            import traceback
            base = _plugin_dir()
            if base is not None:
                (base / 'veusz_js_engine.log').write_text(
                    'not installed: %s\n%s' % (exc, traceback.format_exc()),
                    encoding='utf-8')
        except Exception:
            pass


if os.environ.get('VEUSZ_JS_ENGINE_DEFER') != '1':
    _auto_install()
