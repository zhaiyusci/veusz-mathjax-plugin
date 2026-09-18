# veusz-js-engine

A JavaScript engine for Veusz, as a plugin — **the abstract half** of a
two-layer design: this platform, and the features it runs.

There is one JavaScript engine here, and it is **QuickJS**. This plugin owns
exactly one binary, and it is that engine — plus the plumbing a Veusz plugin
needs to put JavaScript behind part of Veusz. It knows nothing about formulas,
fonts, LaTeX or text rendering, and it ships no JavaScript of its own.

```
veusz-js-engine (this plugin)                      the platform
    the engine: QuickJS, driven from the platform itself
    a JavaScript API a feature is written against
    Veusz plumbing, written once for every feature:
        the properties panel, from what the feature declares
        the text seam, and the drawing of the SVG it returns
        the box: where it goes, how big, aligned by its baseline
        |
        v
a feature (e.g. features/mathjax/feature.js)       concrete
    JavaScript only: declares what to add, returns an SVG
        |
        v
the feature the user sees: one row in the Text properties
```

The point of the split is that the awkward part — the monkey patching that
makes JavaScript draw a Veusz object, and all the Qt that goes with it — is
written **once**, here. A feature declares what it wants and draws.

**A feature is JavaScript.** It contains no Python, no Veusz and no Qt: the
engine runs it, and the platform turns what it
declares into Veusz settings and what it returns into a drawing. A feature
*that has to reach into Veusz itself* can still be written in Python
(`feature.py`), and then it uses the plumbing directly — that is the escape
hatch, not the way in.

There is **one binary**, and it is the engine. The platform talks to
QuickJS's C API itself — there is no compiled bridge of our own, and no C in
this project at all. (There used to be one: a shim that wrapped the same API
for Python. It was removed after this binding was measured to reproduce its
output byte for byte, which meant it had been pure overhead.) A feature may
still ship a different build of the engine beside its JavaScript — the search
starts there — but it needs nothing else.

---

## Install

1. Unpack anywhere, e.g. `C:\tools\veusz-js-engine`.
2. In Veusz: **Edit → Preferences → Plugins → Add…** and pick
   `veusz_js_engine.py`. That is the **only** plugin you add by hand.
3. Put features in `features/`. They are found and loaded automatically.
4. Restart Veusz.

On its own, with no features dropped in, this plugin adds **no** user-visible
feature — no settings row, no checkbox. That is the intended behaviour.

```
veusz-js-engine/
  veusz_js_engine.py           the one plugin Veusz is told about
  qjs.dll                      the engine: QuickJS (MIT) — the platform's
  jsapi.js                     the API a feature is written against
  features/                    features: dropped in, auto-loaded
    README.md
    mathjax/                   one feature, one directory
      README.md                what this feature adds
      feature.js               <- the entry point, and the whole feature
      mathjax.js               the bundle it leans on, and the font list
      fonts/                   twenty fonts, one file each, read only when one
                               is chosen (the bundle carries two of them too)
      fonts.json               what the builder put in the bundle, as it wrote it
      test/test_two_layer.py   its own tests, beside it
      test/test_text_in_formulas.py
    katex/                     a second feature: a parser, and no drawing of
      feature.js               its own -- it hands Veusz the MathML
      katex.min.js             and Veusz typesets it with its own widget
      LICENSE-KATEX.txt
  test/test_platform.py        the platform's test
```

Everything the platform owns is above `features/`; everything a feature owns is
inside its own directory — including its README and its test. That line is the
whole split, and there is only one directory here to release.

### How it is loaded

| what | who loads it | what the user does |
|---|---|---|
| this platform (Python) | Veusz | add it once, by hand |
| a **feature** (`feature.js`, the norm) | **the platform** | drop a directory in `features/` |
| any other `*.js` in that directory | the platform, before the entry point, in name order | keep them beside it |
| a **feature** written in Python (`feature.py`) | the platform | the same, when it must reach into Veusz |

A feature is **a directory with a `feature.js` or a `feature.py` in it**.
Everything else in the directory belongs to it: its bundle, its data, its
README, its tests. A single `.js` or `.py` file directly in `features/` works
too, as shorthand for a one-file feature.

Extra font data goes in the feature's **`fonts/`** directory, one file per
font, and it is read **only when the feature asks for it** — one font's data
can be megabytes, and a feature may offer a dozen, so paying for all of them to
draw one formula would be absurd. Such a file carries no library of its own: it
reads the classes out of the bundle that is already loaded and registers itself
there.

To make that possible without giving a feature the disk, the platform hands
over the first 16 KiB of **every** JavaScript file the feature carries — the
entry point's neighbours and `fonts/` alike — as
`globalThis.veuszFileHeads = [{file, head}, …]`, where `file` is the path
relative to the feature, such as `fonts/stix2.js`. What is *in* a head is the
feature's own convention, not the platform's; ours is a
`// MATHJAX-FONT {…}` line, which is how a feature can list a font, and let the
user choose it, without loading it. A feature then asks for the data it needs
by replying `{load: "fonts/stix2.js"}`, and only ever gets a `.js` file out of
its own `fonts/`. If that file will not load, the drawing carries the reason and
the report names the file: a stale font file must not take the frame down, and
it must not be answered by silently drawing in another font.

Its own `fonts/` is where a real feature lives: this platform ships one feature
with twenty fonts, eighteen of which are only files there. Listing all twenty
costs the heads — 16 KiB each, no glyph data — and the first text that uses one
pays for that one file: measured 0.03 s for 0.9 MB, 0.31 s for 10.9 MB. A `{load}`
reply is half an answer like `{measure}`, so a feature must not cache it: the
platform asks again with the same request, and a cached ask would never end.

Order between features is the **name's** (the directory's, or the file's), so
it is decided without running anything. Prefix with numbers when it matters.
Inside a feature, `feature.js` runs last and the rest of its JavaScript runs
before it in name order, so a bundle needs no manifest to say what it is.
`fonts/` is the deliberate exception: it is not on that list at all, and is
read on demand, as described above.

Veusz itself loads only the plugins it is told about, one file at a time in
Preferences → Plugins, and has no plugin-directory discovery. Left at that, a
platform here would be a library rather than a platform: its users would have
to add one Preferences entry per feature **and** get the order right. So the
platform loads its own features, and that is the only way any of them is
loaded.

---

## The engine, and why there is anything between it and us

QuickJS's own idea of its job is simple: hand it some JavaScript and it hands
something back. What it hands back is a **handle into its own heap**, and a
handle has to be given back — that, and not the evaluation, is the source of
every line below. Measured on the build this ships against:

| bound function | why |
|---|---|
| `JS_NewRuntime`, `JS_NewContext`, `JS_FreeContext`, `JS_FreeRuntime` | one runtime per feature |
| `JS_SetMemoryLimit`, `JS_SetMaxStackSize`, `JS_UpdateStackTop` | the engine runs until it overflows, so it needs limits |
| `JS_Eval` | the one entry point: source in |
| `JS_ToCStringLen2`, `JS_FreeCString` | the answer out, as text |
| `JS_FreeValue`, `JS_GetException` | giving the handle back, and reading a failure |

Thirteen functions, one primitive:

```python
text = runtime.run('1 + 2')          # -> '3'
text = runtime.call('render', src)   # -> what that global returned
```

Everything else the platform does with JavaScript — calling a named global
with a payload — is **spelled in JavaScript too** (`globalThis["render"](...)`),
so the engine has one entry point rather than one per C function, and no handle
ever escapes the call that made it. The platform never manages that memory: the
handle is created and released inside `run`, and a test asserts nothing is left
held, including after fifty failing calls in a row.

Two things about the engine are facts rather than choices, and they are why
this is not three lines:

* **`JSValue` is a 16-byte tagged union passed by value**, and which
  representation is in use is a macro at *build* time — `JS_NAN_BOXING` would
  make it a plain `uint64_t`. Guessing wrong does not raise; it corrupts the
  host process.
* **QuickJS is told how much stack it may use, and it cannot find out for
  itself.** Its overflow check is `sp - alloca_size < stack_top - stack_size`
  against the *current C stack pointer* (`js_check_stack_overflow` in
  quickjs.c), and both numbers come from the host — `JS_UpdateStackTop` and
  `JS_SetMaxStackSize`. There is no OS query anywhere in the engine. So the
  platform creates one thread with a stack it chooses (40 MiB), and every
  runtime is created *and* called there: the budget is a constant, and which of
  Veusz's threads asked stops mattering.

That binding is 200 lines of a 2100-line file. The rest is Veusz: the settings
a feature declares, the properties panel, the draw seam, working out where the
box goes, and the feature protocol. It is in the same file only because Veusz
loads one plugin file — the engine part is small, and it is at the top.

---

## The feature API

A feature is written against `veusz`, which the platform puts in its runtime
before the feature's own JavaScript runs. Two questions cross back to the
platform, and the feature answers both with a string:

| the platform calls | the feature answers |
|---|---|
| `veuszDescribe()` | what this feature is and which properties it adds |
| `veuszRender(request)` | an SVG, or `null` to decline |

`veuszDescribe` is asked **once**, before anything is built. It is what lets
the platform make the properties panel without knowing what the feature does:

```js
veusz.feature({name: 'mine', title: 'Mine', target: 'text', version: '1.0'});
veusz.switch('on',    {label: 'Mine', default: false, descr: 'Draw it'});
veusz.choice('style', {label: 'Style', default: 'plain',
                       choices: [{value: 'plain', label: 'Plain'},
                                 {value: 'bold',  label: 'Bold'}]});
```

`target: 'text'` means every text element gets its own copy of those
properties — an axis label and its tick numbers are separate. The kinds are
`switch`, `choice`, `text` and `number`, and which Veusz control each becomes
is the platform's business: a feature says `switch` and gets a checkbox
without ever learning that Veusz calls it `Bool`.

### A property has two names, and the feature declares both

A property is addressed by a name inside a Veusz settings group, and that name
is written into every document the user saves — so it is not the platform's to
choose. But a feature also needs a handle of its own for the value, and the two
are not always the same word:

| | what it is | who uses it |
|---|---|---|
| `name` | the feature's own handle, e.g. `veusz.switch('on', …)` | `req.get('on')` in the feature's JavaScript; never reaches Veusz |
| `setting` | the name Veusz stores, e.g. `setting: 'mathjax'` | the properties panel, and every `.vsz` file |
| the default | the platform qualifies `name` with the feature's own: `mathjax_on` | — |

`setting` is optional. Without it the platform qualifies the name, which is
what keeps two features from colliding over something as short as `on`. Say it
when the document's own words matter — most sharply when a feature has been
writing those words since before it was JavaScript, and renaming them would
silently untick every formula in an old file:

```js
veusz.switch('on',   {setting: 'mathjax',      label: 'MathJax'});
veusz.choice('font', {setting: 'mathjaxFont',  label: 'Font', …});
```

### Several properties, one row of the panel

A property is a row of the properties panel, unless it says which row it shares.
Properties that name the same `row` become **one line**, in the order they were
declared: the first of them owns it — the panel writes its name in the left
column and the control it makes is the whole line — and the rest are its
members, hidden as rows of their own but still ordinary settings of the
document, saved under their own names and set from the console like any other.
This is presentation: what a feature draws does not know about it.

```js
veusz.switch('on',      {label: 'MathJax', row: 'formula', default: false});
veusz.choice('font',    {label: 'Font', row: 'formula', choices: [...]});
veusz.switch('display', {label: 'Display style', row: 'formula'});
```

```
MathJax   [x]   Font [Computer Modern (TeX) v]   [ ] Display style
```

Each member keeps its own control — the same checkbox or menu the panel would
have made for it alone — so a change made anywhere follows into the widget, and
the panel is told about a change through one signal per member, exactly as it
would be without the row. A menu or a field in a row takes the width the row has
to spare; a checkbox keeps its own. A property can also be declared
`hidden: true` on its own, which keeps it out of the panel entirely.

### Drawing text in the element's font

A drawing often contains words -- ``\\text{...}`` in a formula, and any
character the library's own font does not have. Those words belong in the
*reader's* font, not in the library's, and turning them into strokes takes
something the two sides have separately:

| | has |
|---|---|
| the feature | what the drawing says, and which words it needs set |
| the platform | Qt: the family, per-character fallback, kerning and outlines |

So a render can take **two passes**, and the answer says which:

```js
// pass 1: the feature answers with the runs it needs shaped
return JSON.stringify({measure: [{key: '0', text: 'hello', variant: 'text'}]});

// pass 2: the platform has shaped them, and hands them back on the request
//         as `measured`, keyed by the same `key`
var svg = renderVeuszText(JSON.stringify(req.measured));
```

`req.measured` is the answer to `{measure: ...}`; `req.discard` means the
platform could not do it after all (the font has no outlines at all), and the
feature should forget its half-built drawing and render without those words
rather than leave a gap. `req.face` is a string that changes whenever the
element's font would be painted differently -- the words depend on it, so a
feature that caches its results has to include it in the key.

A drawing with no such text finishes in **one** pass: it asks for nothing and
the platform never calls it a second time, which is the common case.

`veusz.note(message)` writes a line to the platform's log, once per distinct
message. It is for facts about the environment ("this bundle is too old for
that") rather than about one drawing.

`veuszRender` is asked **once per text element, per paint**. The request
carries the text, the size in points, the colour, and the property values:

```js
veusz.renderText(function (req) {
    if (!req.on('on')) { return null; }      // decline: Veusz draws it
    var svg = myDrawing(req.text, {bold: req.get('style') === 'bold'});
    return veusz.svg(svg, {width: 12, height: 9, depth: 2});   // points
});
```

**The box is in points**: `width` and `height` are the whole box, `depth` is
how far it reaches below the baseline. The platform puts the box where the
text would have gone, aligns it by that baseline and paints the SVG into it.
Working the box out is the feature's business, because only the feature knows
what its own geometry means: a feature measuring in `ex` converts it itself
(`veusz.ex(value, exPerEm, sizePt)` is the one line that takes), because only
it knows its own fonts' x-heights.

There are three things a feature may return, and the simplest is usually
enough:

| returned | meaning |
|---|---|
| `''` / `null` | not mine — Veusz draws the text as it always would |
| an SVG string | draw this; the box is whatever the SVG says it is |
| `veusz.svg(svg, box)` | draw this, at exactly this box in points |
| `veusz.error(msg)` | cannot draw — the platform shows `msg` where it would have gone |
| `{measure: [...]}` | I need these words shaped by Qt first (see above) |
| `{load: 'fonts/x.js'}` | read that file of mine, then ask me to draw again |
| `veusz.delegate(text)` | **you** draw this text — it goes to Veusz's own renderer |

`{load: …}` is how a feature keeps a font's megabytes out of its startup path.
It names a file under its **own** `fonts/` — the only thing the platform will
ever read on a feature's behalf — and the platform reads it once, then calls
`veuszRender` again with the same request. A feature that needs nothing simply
never says it, which is every feature that is not carrying extra fonts.

`veusz.delegate(text)` is the one reply that draws nothing at all. The platform
hands the text to the renderer Veusz would have used for that label — its own —
so a feature can say *what* should be drawn without knowing *how*, and without
any drawing library on its side. The KaTeX feature uses it to hand over the
MathML that Veusz typesets itself; the platform never learns what the text
means (it does not know a `<math>` element from any other string). Two details
that matter: the delegated text is handed to the *native* renderer rather than
back into the hooks, or a feature would be asked about its own text for ever;
and it is drawn instead of the label's text, so the document keeps the source
the user typed.

> **Underneath.** The primitive is one global function, one string in, one
> string out — the platform looks the name up on the global object (a flat
> name, no `a.b` paths). A Python feature uses it directly through
> `runtime.run()` and `runtime.call()`. What a feature does with the text,
> and what it makes of its own geometry, is entirely its own business: the
> MathJax feature, for instance, converts MathJax's `ex` lengths to points
> inside its own JavaScript, because only it knows its fonts' x-heights.

---

## Writing a feature

A feature is **a directory with a `feature.js` in it**, in `features/`, that
the **platform** loads. It is not a second thing to add in Preferences: the
platform is the only plugin Veusz is told about, and that is the entire point.
This is the whole of one:

```js
// features/mine/feature.js
veusz.feature({name: 'mine', title: 'Mine', target: 'text', version: '1.0'});

veusz.switch('on', {label: 'Mine', default: false,
                    descr: 'Draw this text with JavaScript'});

veusz.renderText(function (req) {
    if (!req.on('on')) { return null; }
    return veusz.svg(drawSomething(req.text), {width: 12, height: 9, depth: 2});
});
```

No Python, no `import veusz`, no Qt, no `painter`, no baseline arithmetic, no
point-to-pixel conversion. Anything else in the directory — a bundle, data, a
README, tests — is loaded before `feature.js` if it is JavaScript, and ignored
if it is not.

A feature that has to reach into Veusz itself can be written in Python
instead. Then it is an ordinary plugin body that the platform executes, and it
builds its own side using the plumbing below:

```python
import veusz.utils
from pathlib import Path
from veusz.setting import collections

HERE = Path(__file__).parent            # everything it carries is in here

platform = veusz.utils.js_engine        # always up: the platform loads us

# 0. the JavaScript this feature runs -- one runtime per file, made once
runtime = platform.runtime(HERE / 'mine.js', x_height=0.442)

# 1. the property this feature adds, and where
platform.add_setting(collections.Text, None, MySwitch(
    'mychoice', False, usertext='My Choice'))

# 2. how it renders
def draw_text(painter, font, x, y, text, settings, **kwargs):
    if not settings.get('mychoice').val:
        return None                      # decline: Veusz draws it
    return MyRenderer(painter, font, x, y, text, **kwargs)

platform.hook_draw(collections.Text, draw_text)
```

This is the escape hatch, and it is the only way to reach the two primitives
below. Everything it makes you know — the Veusz settings classes, the
`makeQFont`/`Renderer` patching, the Qt — is exactly what a JavaScript feature
does not have to know.

The platform owns the patching of `collections.Text.makeQFont`, `Widget.draw`
and `veusz.utils.Renderer`; a Python feature never touches Veusz's internals
itself.

### 1. `add_setting(target, group, setting)`

| argument | meaning |
|---|---|
| `target` | a `Settings` subclass — every instance of it *is* a settings group — or a `Widget` subclass, whose settings tree is then searched |
| `group` | `None`, or a path such as `'Label/TickLabels'` naming the group inside the target |
| `setting` | a setting instance used as a prototype; it is **copied per instance**, because a settings tree takes ownership (`Settings.add` sets `setting.parent`), so one object cannot live in two trees |

The properties panel is generated from the settings tree, so the option appears
in the UI by itself, on the page that group renders as. **Which kind of control
it is comes from the setting's own type** — a `setting.Bool` is a checkbox, a
`setting.Choice` a menu, a `setting.Distance` a spin box — or from a subclass
that overrides `makeControl` when several settings must share one row. Adding
the same name twice is a no-op, so this is safe on every plugin load.

### 2. `hook_draw(target, callback)`

Two seams, chosen by what `target` is. Both are asked once per object, and
**both decline by returning `None`**, so several hooks can coexist and the
first to claim an object wins.

**`target` is a `Settings` subclass** — the callback is asked once per *text
element*, just before its text is rendered:

```python
def callback(painter, font, x, y, text, settings, **kwargs):
    return MyRenderer(...)   # claim it, or None to decline
```

This is the seam a text feature needs, because one widget draws several texts —
an axis paints its tick numbers *and* its label — and each has its own settings
group. `settings` is the group that made this text's font, so the decision is
per element, not per widget. Get it wrong and a switch on an axis label drags
its tick numbers along.

**`target` is a `Widget` subclass** — the callback is asked once per widget draw
and is handed Veusz's own painting as a callable:

```python
def callback(widget, draw_original, *args, **kwargs):
    if not should_take_over(widget):
        return None                        # decline
    ...
```

Note that this seam owns the *whole* painting of that widget, including
everything else it draws. Reach for it only when the widget really is being
replaced.

`register_provider(obj)` remains as a convenience for the common text case: the
object needs `target` (a settings class), `applies(settings)` and
`make_renderer(...)`. It is expressed on top of `hook_draw`.

### Rendering is SVG

The JavaScript turns its input into SVG and the platform hands that SVG to Qt.
A callback therefore never deals in pixels — only in which SVG goes where —
which is what lets one interface serve any JavaScript file.

### 3. The runtime API

```python
runtime = platform.runtime(HERE / 'mine.js')

runtime.run('1 + 1')                                   # '2'
runtime.call('anyGlobalFunction', 'a string')          # string in, string out
runtime.call_json('anyGlobalFunction', {'op': 'x'})    # JSON in, JSON out
runtime.eval_file('extra.js')                          # one more script
runtime.close()
platform.close_all()
```

**There is no unit anything.** The platform used to be handed an ex-height per
call so it could convert a Python feature's geometry; a JavaScript feature
converts its own, so the platform does not take a height, does not know what an
ex is, and has no place left for one font's geometry to reach another's.

One runtime per file: ask twice and the same one comes back, so the parse is
not paid twice. Nothing starts until the first call, so JavaScript a feature
carries but never needs costs nothing.

---

## Why it is published instead of imported

Veusz loads a plugin by executing the file with empty globals —
`Document.loadPlugins` does `exec(f.read(), {})` — and does **not** put the
plugin's directory on `sys.path`. Two plugin files therefore cannot import each
other, and Veusz keeps no registry of what it has loaded.

The only thing both plugins *can* import is Veusz itself. So the platform hands
itself over on `veusz.utils`, and a feature reaches it there. Three rules
follow, and all of them matter:

* **A feature never has to check that the platform is up**, because the
  platform is what runs it: it publishes itself first, then loads features, so
  `veusz.utils.js_engine` cannot be `None` inside one. (A *consumer* plugin
  that Veusz loads itself is a different thing and does have to check — the
  published name is the only way to reach the platform, and Veusz's plugin
  order is the user's to get wrong. Nothing here does that: features are the
  only consumers, and the load order question does not arise for them.)
* **Never keep shared state in the plugin's module globals.** Veusz does call
  `loadPlugins` more than once, and each call re-executes the file in a fresh
  namespace. The platform keeps its registries on `veusz.utils` for that
  reason; a second load wraps nothing again and loses no runtime, because the
  platform that is already up is reused.
* **A feature gets its own path from `__file__`.** The platform's loader sets
  it. Walking the stack for the loader's `plugin` local — the trick a plugin
  needs under Veusz, which passes empty globals — finds Veusz's *outer* loader
  instead, which names whatever Veusz was told to load, not the feature.

Both binaries are *searched for* rather than assumed, so a feature that has to
carry its own can, and a user who keeps them elsewhere can say so. The search
starts beside the JavaScript, then beside this plugin, then one directory up
from each — which makes the normal case, nothing to configure, come out of the
layout:

```
plugins/
  veusz-js-engine/   veusz_js_engine.py  jsapi.js  qjs.dll
  features/
    a-feature/       feature.js  a.js
    b-feature/       feature.js  b.js  fonts.json
```

A feature's own directory is searched **first**, so a feature that ships a
particular build of either binary gets it, and every other feature finds the
platform's.

These environment variables win over the search: `VEUSZ_JS_ENGINE_QUICKJS` (the
engine), `VEUSZ_JS_ENGINE_FEATURES` (more feature directories), and
`VEUSZ_JS_ENGINE_DEFER=1` (do not install on load, which the tests use). An environment variable naming a file that is **not
there** is an error rather than a silent fallback, because a typo in one is
otherwise invisible.

> The older, released `veusz-mathjax-plugin` has its own set of names —
> `VEUSZ_JSENGINES_*` — from the days when it called what it loaded *engines*.
> The two sets are separate on purpose: this platform has exactly one engine
> and everything else is a feature, so it does not inherit a name that says
> otherwise, and nothing here depends on the old plugin being installed.

---

## Things this project learned the hard way

Properties of Veusz and of the engine, not choices. Worth knowing before you
extend this:

* **A plugin `.py` file must be ASCII only.** Veusz opens it with
  `open(plugin)` and no encoding, so on a Chinese or Japanese Windows the file
  is decoded as GBK and a single em dash makes the plugin fail to load with
  `UnicodeDecodeError`. This plugin keeps every byte below 128 for that reason.
  (A **feature** is read by the platform as UTF-8, so a feature may contain
  non-ASCII. This is a consequence of *who reads the file*: Veusz reads the
  platform, the platform reads the feature.)
* **An ex-height belongs to the feature, not to the process.** The compiled
  bridge held a single value for the whole library, so two files with different
  geometry rendered each other's scale — a real bug, and the reason the API now
  asks a feature for its box in **points**. The conversion lives in the
  feature's own JavaScript, beside the font list it needs for it, and the
  platform's half of it — and the concept of an ex — is gone.
* **A bundle must define `render`.** The platform refuses to load a JavaScript
  file without it, because that is what an SVG bundle has always exposed and a
  file that does not is almost certainly not one. It is why `jsapi.js` defines
  `render` (and `renderInline`) as stubs: that is what lets the platform create
  a runtime *with the API* and then add the feature's own files to it. A real
  bundle loaded afterwards overwrites them.
  (Measured a name at a time: `render` alone loads; `renderInline` alone does
  not. An earlier note here said `renderInline` because the experiment that
  found it changed two names at once.)
* **QuickJS has no filesystem, network or `console` here.** The platform
  creates a bare runtime and injects only an `Object.hasOwn` polyfill, so a
  third-party JavaScript file cannot touch anything outside its own
  computation. There is a 256 MB memory cap. What is **not** covered is a
  timeout or an interrupt handler, so a file with an infinite loop hangs Veusz.
* **The JS engine runs on a thread of the platform's own.** QuickJS does not
  look up the stack it has: it compares the current C stack pointer against a
  top and a budget the host gives it, so *whoever calls* decides what the
  engine may do. Veusz's paint threads are not ours to size and are not all the
  same, which made a formula's depth limit depend on the thread — measured, a
  thread with 2.88 MiB reserved left 1.15 MiB of budget and laid out 16 nested
  fractions. The platform now owns a thread with a 40 MiB stack and hands every
  call to it, so the budget is 16 MiB and the depth limit is 288 nested
  fractions (`\sqrt` stops at 128, `\left(` at 320, superscripts at 64), whoever
  asks. The two numbers move together on purpose: the budget is a fraction of
  the thread, and a budget that reached the end of it would be a guard page
  rather than a catchable error.
  Two consequences worth knowing: a call from any thread is marshalled and
  waits (microseconds), and one runtime is still never entered twice at once —
  the engine lock is taken on the engine thread, and a lock held *across* the
  hop would deadlock against it.
* **Under the installed, frozen Veusz there is no `sys.stdout` or
  `sys.stderr`.** The windowed app starts with both set to `None`, so a
  `print()` anywhere on the load path raises `AttributeError: 'NoneType'
  object has no attribute 'write'`, and the plugin simply appears not to load.
  Every message here goes through `_say`/`_warn`, which drop it instead. This
  is why the plugin worked from source and failed once frozen.
* **Releasing engine values is structural, not a habit.** QuickJS is
  reference counted: every call that hands back a value hands over an owned
  reference. The compiled bridge gave them back with a `JS_FreeValue` at each
  call site, and in Python that is worse than tedious — a missed release leaks,
  and a repeated one does not raise, it corrupts the host process. So no call
  site releases anything: the runtime is entered through one scope, everything
  created inside is recorded, and it all goes back in reverse on the way out,
  including when the scope raises. A test asserts that no scope is ever left
  open, after good calls and after fifty failing ones.
* **The engine's C ABI is not guessable from its header.** `JSValue` is 16
  bytes here (a tagged union) because the build is not `JS_NAN_BOXING`, and
  that choice is a macro at *build* time; a returned string can be a rope
  (`JS_TAG_STRING_ROPE`, -6) rather than a string (-7); the predicates
  (`JS_IsException`, `JS_IsString`) are inline tag tests and are not exported
  at all. Two mistakes worth not repeating: a `c_char_p` restype for
  `JS_ToCStringLen2` hands back a Python buffer whose pointer is then *freed*
  as if the engine owned it — that corrupts the heap — and passing a null
  pointer where `JS_UNDEFINED` is expected is an access violation, not an
  error. See the comment block above the binding for the measured details.

## Not done yet

* **A third primitive: registering a new kind of drawing object.** The model
  half is one line — `document.thefactory.register(MyWidget)`, keyed by
  `MyWidget.typename` — and then `ifc.Add('mywidget')`, document save/load and
  scripting all work. The **UI half is the problem**: the Insert menu and the
  Add toolbar do not come from the factory, they come from a hard-coded tuple
  of typenames inside a method of `veusz/windows/treeeditwindow.py`, plus a
  hard-coded action (with an icon named `button_<typename>`). A
  plugin-registered widget is therefore usable from the console and `.vsz`
  files but does not appear in the menu. It also needs
  `allowusercreation = True` and a `willAllowParent` that permits insertion.
  Worth doing, but it needs either an upstream change or a targeted patch of
  that window, so it is a milestone of its own.
* ABI versioning and a capability probe are not in the binding yet; the
  measured ABI it depends on is written down above the binding, so a future
  engine build can be checked against it.
* Only `svg` output is drawn — by decision. A feature answers with an SVG and
  the box it occupies in points, which is the one shape that can look the same
  in the window and in an export. MathML or a raster would each need a second
  route into Veusz's renderer for no drawing this platform needs.
* Nothing stops a feature's JavaScript that never returns. There is no timeout,
  no interrupt and no thread to kill: a `while(1)` in `feature.js` hangs Veusz.
  QuickJS has the hook for it (`JS_SetInterruptHandler`), so this is one more
  binding rather than a design problem — but until it is bound, a broken
  feature is a frozen window.
* Binaries are Windows x64 only.
