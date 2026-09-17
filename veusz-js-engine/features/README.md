# Feature plugins

Drop-in features that build on **veusz-js-engine**. A feature put here is
loaded by the platform at Veusz startup, so a user adds **one** plugin in
Preferences -> Plugins (the platform) and everything else is dropped in.

```
features/
  mathjax/                 a feature, in its own directory
    README.md              what it adds
    feature.js             <- the entry point, and the whole feature
    fonts.json             the fonts it offers, as the builder wrote it
    mathjax.js             the bundle it leans on, and the font list
    fonts/                 extra font data, one file per font
    test/                  its own test, beside it
  10_myfeature.js          a one-file feature, as shorthand
  20_other/feature.js      another feature
  _helpers.py              skipped: a leading underscore means "not a feature"
```

Everything in a feature's directory belongs to that feature, including its
README and its tests. Nothing there is loaded by Veusz, and only the entry
point (`feature.js`, or `feature.py` for a feature that has to reach into
Veusz) is executed by the platform.

## A feature is a directory

One directory per feature, with the entry point named **`feature.js`**.
Everything else in the directory belongs to the feature and is **not**
executed: its JavaScript, data files, helpers, a README. The feature finds them
through `__file__`:

```python
from pathlib import Path
HERE = Path(__file__).parent          # the feature's own directory
```

A single `.py` file directly in `features/` also works, as shorthand for a
feature that is one file.

## The JavaScript a feature needs lives here too

A feature is JavaScript, so its own files live beside its entry point and are
not a thing of their own with a name and a place to be discovered. Keep them
there and they are found: `feature.js` is the entry point and runs last, and
every other `*.js` in the directory runs before it in name order. That is how a
bundle and a thin wrapper need no manifest to say which is which, and it keeps
the platform's rule that order comes from the name.

`fonts/` is the one exception, and deliberately so: a font's data can be
megabytes and a feature may offer a dozen, so those files are **not** run at
startup. They are read when the feature asks for one, by replying
`{load: "fonts/x.js"}` to a render request. JavaScript cannot read a file, so
the platform hands over the first 16 KiB of every file the feature carries —
`fonts/` included — as
`globalThis.veuszFileHeads = [{file, head}, …]`. A feature reads a font's name
and description out of that head (its own convention; ours is a
`// MATHJAX-FONT {…}` line), offers it, and pays for its data only when the
user picks it.

There is no header, no `package.json` and no directory to register. The names in
a feature's directory are the whole configuration.

## The engine is the platform's

One binary belongs to the platform, next to `veusz_js_engine.py`, because every
feature needs it and it is not about any particular feature: the engine is
QuickJS. A feature that is only JavaScript carries nothing but its own files.

A feature **may** still ship its own copy — the search looks beside the
feature's JavaScript first — for the case of a feature that genuinely needs a
different build. That is the exception; the platform's copy is what every
feature uses by default, which is what keeps a feature to a directory of
JavaScript, its data, a README and its tests.

If two features wanted the same JavaScript, one would point at the other's file,
or better: the thing they share would become a feature of its own. Two features
sharing one *entry point* is not possible, and would not mean anything.

## What the entry point does

It is an ordinary Python plugin body, but **Veusz never loads it**: the
platform does. Two consequences:

* it reaches the platform through the published name and says what it wants:

```python
import veusz.utils
from veusz.setting import collections

platform = veusz.utils.js_engine          # always up: the platform loads us

platform.add_setting(collections.Text, None, MySwitch(...))
platform.hook_draw(collections.Text, my_draw_callback)
```

* it may contain non-ASCII text, because the platform reads it as UTF-8. The
  ASCII rule belongs to the files **Veusz** reads with no encoding at all — the
  platform's own `veusz_js_engine.py`, and nothing in here.

## Rules

* **Order is the name's** -- the directory's name, or the file's. So it is
  decided without running anything. Prefix with numbers when order matters.
* **One broken feature does not stop the others.** The failure is written to
  `veusz_js_engine.log` beside the platform plugin.
* **Loading twice does nothing.** A feature already loaded is not executed
  again, and two directories offering the same name load it once. This is an
  optimisation as much as a rule: a setting is keyed by name and an identical
  draw hook is kept only once, so a feature that does get run twice still
  behaves as one.
* **Never add a feature in Preferences -> Plugins.** Veusz has no idea what a
  feature is -- the platform is the only plugin it is told about. A feature put
  in that list is loaded a second time by Veusz, in no defined order relative
  to the platform: above it, the feature fails outright for want of a platform;
  below it, the platform loads the very same file again. Drop it in `features/`
  and let the platform do it.

## Where else it looks

This directory (next to the platform plugin) and a sibling `features/`
directory one level up, so several plugins can share one installation. Point
`VEUSZ_JS_ENGINE_FEATURES` at one or more directories (`;`-separated) to add
more, which is also how a feature kept in its own project folder is tried out.
