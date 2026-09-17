# mathjax

MathJax formulas for Veusz — **the concrete half** of the two-layer design in
[the platform](../../README.md). This directory *is* the feature: a feature is a
directory the platform loads, and this one is named `mathjax`.

This feature owns the switch, the font chooser, the Display style box, and the
renderer that paints the formula. The platform owns the machinery: the
JavaScript engine, the settings injection, the font-owner tracking and the
single renderer wrapper.

```
veusz-js-engine/                          the platform
  veusz_js_engine.py                      the host, and the Veusz plumbing
  jsapi.js  qjs.dll                       the API, and the engine
  features/mathjax/                       this feature (concrete)
    README.md                             this file
    feature.js                            the whole feature: what it is, and
                                          how it draws
    mathjax.js                            the bundle it runs — and the font
                                          list, declared in its head
    fonts/                                twenty fonts, one file each, read
                                          only when one is chosen
    fonts.json                            what the *builder* put in the
                                          bundle, as the builder wrote it
    test/test_two_layer.py                its own tests, beside it
    test/test_text_in_formulas.py
```

**Everything that supports MathJax is inside the feature** — and it is all
JavaScript: the entry point, the fonts it was built with, the bundle it runs,
its README and its tests. The JavaScript is
part of this feature, not a thing of its own: the platform is only asked to run
it. The engine is the platform's, because every feature needs it.

This is why the feature is a directory and not a separate plugin release:
everything it needs sits in the one place that gets copied to install it.

The feature's own business here is the geometry: MathJax writes its box in
`ex`, and one ex is *this font's* x-height of an em, which is why each font
declares its own x-height in the head of the file that carries it and
`feature.js` turns the box into points itself. The platform is never told what
an ex is — it only ever sees points — which is what stops one font's geometry
reaching another.

## Install

The feature is already in place — it ships with the platform. Install
[`veusz-js-engine`](../../README.md) and the MathJax row appears; there is
nothing else to add.

To use it with a platform installed elsewhere, drop the `mathjax` directory
into that platform's `features/`, or point `VEUSZ_JS_ENGINE_FEATURES` at the
directory that contains it.

Restart Veusz. Text objects then have a **MathJax** row:

```
MathJax:  [x]   Font [Computer Modern (TeX) v]   [ ] Display style
```

All three are on that one line, as they were in the original plugin: the three
properties name the same `row`, so Veusz shows one row and the switch, the menu
and the style sit together (the menu takes the width the row has to spare).

If the platform is missing, this feature is not loaded at all — nothing loads
it, since the platform is its loader. (Point Veusz at this file directly and it
refuses, writing the reason to `veusz_js_engine_mathjax.log`; but that is not
how a feature is loaded, so the normal symptom of a missing platform is simply
that the MathJax row is absent.)

## What it adds

* **MathJax** — the switch, plus the font chooser and **Display style** beside
  it in the same row of the panel (three settings, one line, as in the original
  plugin: the platform's `row` declaration). All three are per text element, so
  an axis label and its tick numbers are independent.
* **Display style** off (the default) typesets inline, the way a formula sits
  in a sentence; on typesets a displayed equation with larger fractions and
  limits above and below.
* **Font** — twenty of them, in one chooser. Two come from the bundle and are
  there the moment the feature loads; the other eighteen live in `fonts/`, one
  file each, and are read the first time a text uses one and not before (see
  below). Each font is typeset at the size you asked for rather than merely
  scaled: every MathJax font declares its own x-height, in the head of the file
  that carries it, and the feature converts that font's `ex` geometry to points
  with that number. Using one font's x-height for another renders it up to 19%
  off.

## The fonts it offers

A font declares itself in the head of the JavaScript file that carries it — a
`// MATHJAX-FONT {…}` line naming it, its x-height and its title. The bundle
declares the two it was built with; each file in `fonts/` declares one more.
That is the whole font list, and it is nothing anyone has to maintain beside
the fonts themselves.

| id | font | file | MB |
|---|---|---|---|
| `tex` | Computer Modern (TeX) — the default | in the bundle | — |
| `stix2` | STIX Two | in the bundle | — |
| `asana` | Asana | `fonts/mathjax-asana.js` | 1.1 |
| `bonum` | Bonum (bookman-like) | `fonts/mathjax-bonum.js` | 1.9 |
| `dejavu` | DejaVu | `fonts/mathjax-dejavu.js` | 1.1 |
| `euler` | Euler Math — Zapf's upright, calligraphic-flavoured | `fonts/mathjax-euler.js` | 1.5 |
| `fira` | Fira | `fonts/mathjax-fira.js` | 2.7 |
| `lete` | Lete Sans Math | `fonts/mathjax-lete.js` | 1.4 |
| `luciole` | Luciole Math — thick strokes, made for low vision | `fonts/mathjax-luciole.js` | 0.9 |
| `modern` | Modern | `fonts/mathjax-modern.js` | 3.8 |
| `neohellenic` | GFS Neohellenic Math — Greek sans, made for slides | `fonts/mathjax-neohellenic.js` | 2.8 |
| `newcm` | New Computer Modern (MathJax's own default) | `fonts/mathjax-newcm.js` | 10.9 |
| `pagella` | Pagella (Palatino-like) | `fonts/mathjax-pagella.js` | 2.4 |
| `pennstander` | Pennstander Math — a wide, friendly sans | `fonts/mathjax-pennstander.js` | 2.1 |
| `plex` | IBM Plex Math — sans, several weights upstream | `fonts/mathjax-plex.js` | 3.1 |
| `sans1` | Sans 1 — see the licence note; it is KpMath Sans | `fonts/mathjax-sans1.js` | 1.1 |
| `sans2` | Sans 2 — see the licence note; it is ArsenalMath Sans | `fonts/mathjax-sans2.js` | 1.1 |
| `sans3` | Sans 3 — see the licence note; New Computer Modern Sans Math | `fonts/mathjax-sans3.js` | 3.0 |
| `schola` | Schola (Charter-like) | `fonts/mathjax-schola.js` | 2.1 |
| `termes` | Termes (Times-like) | `fonts/mathjax-termes.js` | 2.0 |

Ten of these are sans-serif *faces* — whole fonts whose digits, operators,
brackets and Greek are sans, not just the letters (`\mathsf` gives those in
every font above). `lete`, `luciole`, `pennstander`, `plex`, `neohellenic`,
`euler` and the three Sans fonts come from OpenType math fonts converted for
this project; the rest are MathJax's own packages.

`fonts/mathjax-tex.js` and `fonts/mathjax-stix2.js` are here because the
directory is one file per font *we built*, and those two were built like the
rest. They are never read: the bundle registers both when it loads, and a font
that is already in the engine is never asked for.

### What a font costs

Nothing until it is used. What the platform hands over at startup is the head
of every file the feature carries — 16 KiB each, twenty-two of them, 352 KiB of
text and no glyph data at all — which is enough to list a font and say what its
x-height is.
The first text that uses one has its file read, once: measured 0.03 s for
Luciole (0.9 MB), 0.06 s for Termes, 0.31 s for New Computer Modern (10.9 MB),
after which its outlines are cached per formula like any other font's. All
twenty at once fit inside the engine's memory limit — `build/probe_fonts.py`
beside the platform reads and draws every one of them in a single process.

Two facts about the engine are load-bearing here, and both cost a bug to learn:

* whether a font is *loaded* is `__veuszMathjax.fonts()`, the engine's own list,
  not `fontNames`, which is only what the bundle was built with and never
  learns about a font added afterwards;
* a reply that asks for something (`{load: …}`, `{measure: …}`) is half an
  answer and must not go into the render cache — the platform asks the feature
  again with the same request, and a cached ask is a request to load the file
  for ever.

### Adding and removing one

There is no list to edit. **Adding** a font is dropping a file into `fonts/`:
anything the bundle builder produced with `--font-data`, or any file that calls
`__veuszMathjax.registerFont(...)` and declares itself in its head — it appears
in the chooser the next time Veusz starts, and costs nothing until it is picked.
**Removing** one is deleting its file. The whole directory is 50 MB, and
`newcm` alone is a fifth of it.

A file that will not load — one built for a different bundle, which is what the
header's `"mathjax"` version is for — is reported rather than swallowed: the
drawing carries the reason and the report names the file, because drawing the
formula in another font would be worse than saying what happened.

### Licences

The MathJax fonts are **Apache-2.0** like MathJax; every converted font is
**OFL-1.1** except Sans 3, which is under the **GUST Font License**. KpMath Sans
declares `Reserved Font Name <Kp>`/`<KpMath-Sans>` and New Computer Modern Sans
Math is under the GUST licence, which like the LPPL expects a modified version to
be renamed — and a conversion is a modified version. That is why three of them
are called Sans 1, Sans 2 and Sans 3. Their copyright holders are credited in
the headers of the font files themselves, and the full licence texts are in the
project these files were built in (`licenses/`, one level up from the plugin).

Write the formula on its own — do **not** wrap it in `$...$`; the whole label is
the formula and the dollar signs would be typeset literally.

## Requirements

* The platform, with this feature's directory in `features/`. The feature runs
  the `mathjax.js` beside its own entry point, and converts its `ex` geometry
  to points itself with the font's own x-height; there is nothing to configure.
* The font list comes out of the heads of the files the feature carries — a
  `// MATHJAX-FONT {…}` line in each, declaring the font it holds and its
  x-height. The platform hands every head over (JavaScript cannot read a file),
  which is what fills the chooser without reading a font's data. `fonts.json`
  beside the bundle is what the *builder* put in the bundle; the feature's test
  keeps the declaration in `mathjax.js` and that file equal, and separately
  checks that the chooser is the declaration of every file. With no declaration
  at all the feature still works, with a single anonymous font.

## Tests

```bash
PYTHONPATH=<your Veusz> python veusz-js-engine/features/mathjax/test/test_two_layer.py
PYTHONPATH=<your Veusz> python veusz-js-engine/features/mathjax/test/test_text_in_formulas.py
```

Run them with the Python of the Veusz you use. The first checks what the split
promises:

1. the platform publishes itself and loads the feature it ships — Veusz is told
   about one plugin, not two;
2. the platform's own code contributes nothing (no settings, no hooks) when
   there are no feature directories to look in;
3. the feature registered its settings and a text draw hook, and a text object
   then renders through JavaScript (different ink from ordinary text);
4. a feature already loaded is not executed twice;
5. the hook declines cleanly: a switch that is off gives pixel-identical output
   to a label that never had the setting;
6. switching font changes the picture;
7. the chooser lists every font declared in the feature's own files — the two
   in the bundle and one per file in `fonts/`, no id twice — and the bundle's
   declaration still matches `fonts.json` beside it;
8. a font outside the bundle is read once, when a text first uses it, and then
   draws through a real export;
9. an element font that has no outlines still draws the formula, and says why.

The second is the port of the original's `test/test_text_font.py`: it draws
through the same protocol the platform's seam drives, and reads the outlined
runs back out of the SVG — text in the element's font at the width Qt measured,
emphasis from TeX and from the element, spaces kept, scripts scaled once
(0.707 / 0.5), nested text and maths, a `\newcommand` expanding to text, the
maths after a word run starting where Qt said it ended, underline drawn, and
the size carried in the transform rather than in the outlines.

## Not done, and why

* The legacy `useTeX` setting is **not** forwarded to `mathjax`, so a document
  written before 0.3.0 that used it comes back with the box unticked. Dropped
  on request: the platform's settings are its own, and this feature already
  writes `mathjax` as the setting name, so documents that used that name keep
  working.
* The row does not resynchronise: changing the three settings from elsewhere in
  Veusz (the command line, undo) does not push the values back into the row's
  widgets, and a feature that ships one font still shows a chooser. Dropped on
  request.
* Another typesetter is another feature. This one is MathJax, and its bundle is
  MathJax's: KaTeX or Typst would be a second directory in `features/`, with
  its own bundle and its own settings.
