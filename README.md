# veusz-mathjax-plugin

MathJax-rendered math for [Veusz](https://veusz.github.io/), as a plugin.

Veusz's built-in math support is MathML plus some Unicode tricks. This plugin
adds a **"MathJax" checkbox** to text objects and renders the formula with
**MathJax 4** running inside an embedded **QuickJS** engine, so nothing has to
be installed alongside it: no LaTeX distribution, no Node.js, no external
process.

The renderer is MathJax's TeX input, not a LaTeX run — what you can write is
what MathJax implements (see *What it renders* and *Limitations*).

Nothing in Veusz has to change for this: the plugin carries the whole feature
(settings, renderer, widget wiring) itself.

## Platforms

Prebuilt binaries: **Windows x64 only**. That is what the author builds and
runs, and the only combination that has been tried: **Windows, Veusz 4.2.1,
Qt 6**.

On other systems there is no binary. The Python plugin and the MathJax bundle
are platform-independent, so two things have to be built: the bridge (one C++
file plus its `CMakeLists.txt`) and QuickJS itself, which upstream compiles as
a shared library with a single cmake flag. Drop both into `data/` and it works
(see *Build from source*). Linux is the one the author might get to.

## Requirements

* **Veusz 4.2.1 on Qt 6** — the version this has been tested with. Other
  versions have not been tried, so they may work, or they may not.
* Nothing else: no TeX distribution, no Python packages, no administrator
  rights. The plugin is one Python file plus a `data/` folder, and it never
  writes into the Veusz installation.

---

## Which package?

Three kinds come out of the same source. All of them install the same way and
contain the same plugin; only the fonts inside `data/` differ, so the choice is
about what the formulas should look like next to the rest of your figure.

**`veusz-mathjax-plugin-<version>.zip` — 1.8 MB — "I just want proper-looking formulas."**
One font, **Computer Modern (TeX)**: the face LaTeX has used for decades, and
what most people expect a formula to look like. Nothing to choose, smallest
download, and the lightest: 0.13 s to start, +14 MB of memory.

**`veusz-mathjax-plugin-<version>-allfonts.zip` — 16.4 MB — "the math should
match the rest of the figure."**
Twenty faces, chosen per text element in the MathJax row: New Computer Modern
(MathJax's own default), Computer Modern (TeX), Modern, **STIX Two** and
**Termes** (Times-like), **Pagella** (Palatino-like), **Schola** (Charter-like),
**Bonum** (bookman-like), **Fira**, **DejaVu**, **Asana** (sans), and the
sans-serif math faces **Lete Sans Math**, **Luciole Math**, **Euler Math**,
**Pennstander Math**, **GFS Neohellenic Math**, **IBM Plex Math** and three
published under names of their own — **Sans 1**, **Sans 2**, **Sans 3** (KpMath
Sans, ArsenalMath Sans and NewCM Sans Math; `fonts/README.md` says why the names
differ).
If the text around your formulas is not Computer Modern — a Times-like figure, a
sans-serif poster, whatever the journal asks for — this is how you stop the math
from clashing with it. Cost: 1.4 s to parse the bundle at startup, measured, and
the memory that goes with 47 MB of inlined glyph data.

**`fonts/mathjax-<name>.js` — in this repository, 0.9 to 10.9 MB each.**
One font on its own, for a plugin you already have: download the file, put it in
the plugin's `data/` folder and restart Veusz, and that font joins the chooser —
no second file to keep in step, nothing to configure.  The file carries no
MathJax: it registers into the copy the plugin already loaded, so every font
added this way shares one core. Useful when the one font
you want is not in the package you downloaded (the `text` one, say, or a figure
that has to match a specific journal face). They live in the repository rather
than in a release, so the link to one never changes; `fonts/README.md` lists
them. Take any number; they do not interfere with each other or with the fonts
you already have. See *Adding another font*.

| | fonts | download | loads in |
|---|---|---|---|
| `…-<version>.zip` | 1 — Computer Modern (TeX) | 1.8 MB | 0.11 s |
| `…-<version>-allfonts.zip` | 20 | 16.4 MB | 1.4 s |
| `fonts/mathjax-<name>.js` | 1, added to whatever you have | 0.9–10.9 MB | — |

Those costs are measured, and they are only paid once a label actually asks for
MathJax; a plot with no TeX text never loads the engine at all. An added font is
only loaded the first time something is drawn with it, so carrying one you do not
use costs nothing.

You can change your mind later: download another zip and replace (or add to) the
plugin folder. Documents keep their settings, and a document that names a font the
package does not carry falls back to that package's font. The one-font package
still shows the chooser — it just has a single entry.

## Install (from a release zip)

1. Unpack the zip anywhere, e.g. `C:\tools\veusz-mathjax-plugin`.
   Keep `veusz_mathjax.py` and `data/` together.
2. In Veusz: **Edit → Preferences → Plugins → Add…** and pick
   `veusz_mathjax.py`. Press OK, then restart Veusz.
3. Select any text object (label, axis label, tick labels, key, contour
   label, …) and open its **Text** properties. There is now one row:

   ```
   MathJax:  [x]        [Computer Modern (TeX) v]     [ ] Display style
   ```

   Tick **MathJax** and type a formula, e.g.

   ```
   x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}
   ```

   Write the formula on its own — do **not** wrap it in `$…$`; the whole label
   is the formula, and dollar signs would be typeset literally.

To remove the plugin again: **Preferences → Plugins**, select it, **Remove**,
then restart Veusz. Nothing is written into the Veusz installation, so that is
the whole uninstall.

To check that it worked from a console (and to see errors), start Veusz with

```
veusz --veusz-plugin "<path>\veusz_mathjax.py"
```

It prints the bridge and the bundle it loaded, and how many widgets it wired.
If the checkbox does not appear, look at `veusz_mathjax.log` next to the plugin
file.

## Use

* The **MathJax** row carries three things at once: the switch, the font
  chooser, and **Display style**. They are independent per text element, so an
  axis label and its tick numbers can be set up differently.
* **Display style** is off by default, which typesets the formula *inline*, the
  way it would look in a sentence: compact fractions and sub/superscripts
  instead of limits above and below. Turn it on for a standalone equation, where
  LaTeX would use `\[ … \]` rather than `$ … $`. It only changes formulas that
  contain something style-sensitive; a plain `\alpha (rad)` looks identical
  either way. Measured on `\frac{a}{b}` at 20pt: 21.6pt of paper inline against
  36.5pt with Display style — the same 1.7× you get from `$…$` versus `\[…\]` in
  LaTeX.
* The **font** list is whatever the package carries. Each font is typeset at the
  size you asked for, not merely scaled: every MathJax font declares its own
  x-height (measured 0.441 to 0.527), and the plugin hands the right value to
  the renderer when the font changes. Using one font's value for another would
  render it up to 19% off — that is the bug class that made formulas 33% too
  large in 0.2.0.
* The formula takes its colour from the current pen; changing the colour, the
  size or the font re-renders it, and results are cached per (text, size,
  colour, style, font).
* If anything goes wrong (a MathJax error, a missing data file), the plugin falls
  back to the normal text renderer instead of breaking the plot — the label then
  shows the source text, and the reason appears at startup and in the log.

## Upgrading from 0.2.x

The switch used to be called **Use TeX** and always typeset in display style.
It is now called **MathJax**, there is a separate **Display style** box, and
inline is the default. Documents keep the switch they had — the old setting name
is still read and forwarded — but formulas with fractions or limits will come
out shorter than before, because they now default to inline. Tick **Display
style** on those labels to get the old look back.

## What it renders

MathJax 4.1.3, TeX input, with these input packages enabled:

```
base  ams  newcommand  boldsymbol  braket  cancel  color  enclose  extpfeil
html  mhchem  noerrors  noundefined  physics  mathtools  amscd  action  bbox
unicode  verb  textmacros  textcomp  cases
```

That covers AMS math, matrices and `cases`, `\mathbb`/`\mathfrak`/`\mathcal`/
`\mathsf`/`\mathtt`, `\xrightarrow`, `\ce{2H2 + O2 -> 2H2O}`, `\dv{f}{x}`,
`\qty(…)` and the rest of those packages.

The fonts are MathJax's own, with their glyph ranges inlined (the embedded
engine cannot fetch anything at render time), so Greek, Cyrillic, Hebrew,
Devanagari and Cherokee inside a formula come out as real glyphs. Which fonts
you have depends on the package: Computer Modern (TeX) alone in the small one,
twenty in the `allfonts` one. The glyph ranges shipped are the font's own full
set — 40 of 40 for New Computer Modern, for instance.

### Adding another font

Download the font's file from the `fonts/` folder of this repository
(`fonts/mathjax-termes.js`, say — right-click *Raw* and choose "Save link as…",
or a browser will show you the JavaScript), put it in the `data/` folder of your
plugin, and restart Veusz:

```
veusz-mathjax-plugin/
  veusz_mathjax.py
  data/
    mathjax_bundle.js     the fonts that came with your package
    mathjax-termes.js     <- the one you just added
```

The font is in the chooser of the MathJax row from then on, on every text
element. Nothing is configured and no list is updated: a font bundle says what it
carries in its first line, and the plugin reads that (without loading the file).
Take as many as you like — they do not interfere with each other, or with the
fonts your package already had — and a font nobody selects is never loaded, so an
unused one costs nothing. Delete the file again to remove the font; documents
that asked for it then fall back to the package's font.

If the font you want is not one of the twenty we ship, it can be converted from
an OpenType math font and bundled — `tools/build_mathjax_font.py` writes the
MathJax data from the `.otf`, and

```
python tools/build_bundle.py --font-package <the converted package folder> \
    --out data/mathjax-mine.js
```

turns that into a drop-in file like the one above. `local-fonts/README.md`
describes what a conversion involves — and the source font's licence decides
whether the result can be passed on.

The formula is placed using the baseline MathJax reports, so TeX labels line up
with each other and with plain text, and it is drawn as paths, so vector exports
stay vector. The one exception is a character the chosen math font does not
have: that one is drawn as an outline at the size you asked for, in the font the
text element is set in (the **Font** row), so you get a real character rather
than a box, matching the text around it — the same in every resolution and under
every math font. See *Limitations*.

## Package layout

A release zip unpacks to exactly this:

```
veusz-mathjax-plugin/
  veusz_mathjax.py       the plugin — the file you add to Veusz
  data/
    mathjax_bundle.js    MathJax 4 + the package's fonts, bundled with esbuild
                         (~3 MB in the one-font package, ~48 MB with all twenty)
    fonts.json           which fonts are in the bundle, and their x-heights
    mathjaxbridge.dll    the JS host, built from src/mathjax_bridge.cpp
    qjs.dll              QuickJS (quickjs-ng), imported by the bridge (~1 MB)
  README.md
  LICENSE                Apache-2.0
  NOTICE                 attribution for MathJax, mhchemParser and QuickJS
  THIRD_PARTY.md         what is inside, and one licence per artefact
  licenses/              QuickJS (MIT) and MathJax (Apache-2.0) texts
```

The source repository additionally has `src/` (the bridge), `tools/` (the build
scripts), `test/`, `VERSION`, and `fonts/` — the single-font bundles people
download to add a font to a package they already have — and `local-fonts/`, the
fonts converted from OpenType for this project.

The data files are found through `VEUSZ_JSENGINES_BRIDGE` /
`VEUSZ_JSENGINES_BUNDLE` / `VEUSZ_JSENGINES_QUICKJS` (and `VEUSZ_JSENGINES_FONTS`
for the font list) first, then in `data/` next to the plugin file (and next to
its parent, so several plugins can share one `data/`). The folder can therefore
be moved anywhere, as long as it is kept together.

## Troubleshooting

| Symptom | What to do |
|---|---|
| No **MathJax** checkbox | The plugin did not install. Read `veusz_mathjax.log` next to the plugin file, or start Veusz from a console for the message. |
| The checkbox appears, but the label shows the TeX source | Rendering failed and the plugin fell back to plain text on purpose. The log names the reason; a missing `data/qjs.dll` or `data/mathjax_bundle.js` shows up at startup. |
| The label shows `$x^2$`, dollars included | Drop the `$…$` — the whole label is the formula. |
| Nothing happens at all, no log | The plugin was probably not added in Preferences → Plugins, or Veusz was not restarted. |
| A fix does not seem to be in effect | Veusz loads plugins **once, at startup**: editing `veusz_mathjax.py` does nothing to a running Veusz. Restart it and check the first lines of `veusz_mathjax.log` — the plugin records which copy it loaded, with its size and modification time. |
| "qjs.dll was not found" | `data/` was separated from `veusz_mathjax.py`, or a copy step skipped `qjs.dll`. Keep the folder together. |
| Missing MSVC runtime DLLs | Install the Microsoft Visual C++ 2015–2022 x64 redistributable and try again. |

## Build from source

This is the Windows recipe — the one that has been used.

Requirements: Python 3.8+ (build scripts only; the plugin itself needs no
Python packages), Node.js + npm for the JavaScript bundle, and cmake with MSVC
for the native parts.

Three artefacts are built separately, one licence each:

| Artefact | Built from | Licence |
|---|---|---|
| `data/qjs.dll` | quickjs-ng, unmodified, `-DBUILD_SHARED_LIBS=ON` | MIT |
| `data/mathjax_bundle.js` | MathJax 4 + fonts, bundled with esbuild | Apache-2.0 |
| `data/mathjaxbridge.dll` | `src/mathjax_bridge.cpp` (this project) | Apache-2.0 |

```bash
# 1. QuickJS (quickjs-ng), once — the shared build, it stays a separate library
git clone https://github.com/quickjs-ng/quickjs.git quickjs-src
src/build-quickjs-windows.cmd          # builds and installs data/qjs.dll

# 2. build everything and stage a release zip in dist/
python tools/build_all.py                 # basic: one font (--font, default tex)
python tools/build_all.py --flavor allfonts   # every font we know, ~16 MB zip
python tools/build_all.py --flavor fonts      # one file per font, into fonts/
                                          # add --trim to drop rare scripts
                                          #     --skip-quickjs / --skip-bridge
                                          #     to rebuild only part of it
```

`--flavor allfonts` builds one bundle holding every font (New Computer Modern,
Computer Modern (TeX), STIX Two, Modern, Fira, Pagella, Schola, Termes, Bonum,
DejaVu, Asana, the sans-serif math faces, Lete Sans Math) — 48 MB of JavaScript in
one file, 1.4 s to load, measured. The basic build is one font and loads in
0.11 s. Both expose the same `setFont()` to the plugin, so the plugin
file does not depend on which flavour it is given.

`--flavor fonts` writes those same fonts one file each into `fonts/`, for people
to download from the repository rather than from a release. It is not part of
every release build: an existing file is kept as long as the MathJax version it
embeds is the current one, and `--rebuild-fonts` forces it.

Just the bundle, for a quick experiment:

```bash
python tools/build_bundle.py --fonts tex,stix2     # or --all-fonts, --font NAME
python tools/build_bundle.py --list-fonts          # what is available
```

The scripts look for the quickjs-ng checkout in `quickjs-src/` **inside** this
project first, then in `../quickjs-src` **beside** it, and put the build in the
matching `quickjs-build-shared/` — so the `git clone` above works whether you
run it inside the project directory or next to it. `QUICKJS_SRC` / `QUICKJS_LIB`
override the search. Behind a proxy, npm needs `HTTPS_PROXY`.
The build scripts find `vcvars64.bat` and cmake themselves (`VCVARS` and `CMAKE`
override the search).

Already have the MathJax packages somewhere (offline build, vendored tree)?
Point the builder at them and skip npm entirely:

```bash
python tools/build_bundle.py --packages /path/to/node_modules \
                             --esbuild /path/to/esbuild
# several trees may be given, separated by ';' (or repeat --packages)
```

### Building the bridge on another platform

`src/CMakeLists.txt` compiles `mathjax_bridge.cpp` with any C++17 compiler; it
needs QuickJS as a shared library (`-DBUILD_SHARED_LIBS=ON`), passed as
`-DQUICKJS_SRC` (headers), `-DQUICKJS_LIB` (import library) and optionally
`-DQUICKJS_DLL` (the engine itself, copied into `data/` next to the bridge).
The plugin already looks for `libmathjaxbridge.so` and `libmathjaxbridge.dylib`
as well as the Windows name, so no Python change is needed.

Finding the engine is where the platforms differ. On Windows the loader
resolves the bridge's `qjs.dll` import from the bridge's own directory — the
current directory and `PATH` are not consulted (measured) — so putting
`qjs.dll` next to the bridge is enough. On Linux and macOS the loader does
**not** search the loading library's directory, which is why `veusz_mathjax.py`
loads the engine itself, by absolute path, before it loads the bridge; it looks
for `libqjs.so` / `libqjs.dylib` next to the bridge. An rpath of `$ORIGIN`
would work as well.

### Verify

```bash
python test/smoke_test.py       # renders a TeX label + a TeX axis label
```

Run it with the Python of the Veusz you actually use (or with `PYTHONPATH`
pointing at a Veusz checkout). It loads the plugin the way Veusz does, builds a
document with a TeX label and a TeX axis label, exports a PNG and reports the
ink it found; a non-zero exit code means the plugin did not install or nothing
was rendered.

## How it works

Veusz's plugin system executes a Python file at startup, so this plugin has
access to Veusz's internals. It uses exactly six hooks:

1. **Settings** — wraps `veusz.setting.collections.Text.__init__` to add three
   settings to every text-bearing widget: `mathjax` (the switch, whose row also
   carries the other two), `mathjaxDisplay` and `mathjaxFont`, the last two
   hidden so the panel shows a single line. A hidden `useTeX` forwards to
   `mathjax`, so documents written with the older name keep working. The
   properties panel is generated from the settings tree, so the row appears by
   itself and is saved in `.vsz` files like any other setting.
2. **One row, three controls** — the switch's `makeControl` returns a small
   composite widget holding the check box, the font chooser and the Display
   style box. It emits `sigSettingChanged` naming *whichever* of the three
   settings changed, which is how a single row can drive three of them through
   Veusz's normal command/undo path. If that widget cannot be built for any
   reason, the plugin falls back to a plain check box rather than losing the
   switch.
3. **Which text is this?** — wraps `makeQFont` of the text settings class.
   Every widget builds the font of a text element from that element's own
   settings group just before drawing it (`s.get('TickLabels').makeQFont(
   painter)` for the tick numbers, `s.get('Label').makeQFont(painter)` for an
   axis label, `s.get('Text')` for a label or a key), so recording which group
   made the font tells the renderer which group the text belongs to. Without
   this, an axis's *MathJax* on the label would drag its tick numbers along.
4. **Widget wiring** — wraps `draw` of every registered widget class to publish
   the widget's own settings as a fallback, for text painted without a
   `makeQFont` of its own. No widget internals are touched. The search also
   looks into nested groups (`settings.ticklabels`, `settings.label`, …),
   because an axis keeps its text settings there.
5. **Renderer** — wraps `veusz.utils.Renderer`, the single place every widget
   paints text through: for text whose settings group asked for MathJax it
   returns its own renderer, otherwise the original one.
6. **Drawing** — the renderer asks the bridge for the SVG of the formula, draws
   it with Qt's SVG renderer, rotates it if the label is rotated, and positions
   it using the baseline MathJax reports (`vertical-align`). Colour comes from
   the current pen, and results are cached per (text, size, colour, style,
   font).

The bridge is called through its multi-bundle API (`js_host_*`) rather than the
older `mathjax_*` one, because that can call *any* function the bundle defines —
`setFont` included. Switching font also re-sets the bridge's ex-height from
`fonts.json`, since `1ex = size × x_height` decides how MathJax's geometry
becomes points and every font declares its own (0.441 to 0.527 measured).

The native bridge (`src/mathjax_bridge.cpp`) is a small **JS host**: it
evaluates a bundle inside QuickJS and calls the `render()` / `renderInline()`
functions the bundle defines, returning the string they produce. It knows
nothing about Veusz, Qt or MathJax — Veusz is never linked against; the library
is loaded with `ctypes` at run time.

```
veusz_mathjax.py ──ctypes──▶ mathjaxbridge.dll ──imports──▶ qjs.dll
      │                            │                            │
  settings/                     JS host                   QuickJS (MIT)
  renderer                    (render/free)                     │
      └────────────────────────────┴──▶ mathjax_bundle.js ◀─────┘
                                        MathJax 4 + newcm
                                           → SVG string
```

The engine is its own file: the bridge is linked against QuickJS, not against a
copy of it, so `data/` holds three artefacts with one licence each (see
`THIRD_PARTY.md` for why).

## Limitations

* **MathJax is not LaTeX.** There is no `\usepackage`, no `\includegraphics`, no
  TikZ/pgfplots, no counters or references, no shell escape — only the packages
  listed above and what they implement.
* It is a plugin, so it reaches into Veusz internals (the `Text` settings
  class, `Widget.draw`, `Renderer`, `_Renderer`). A major Veusz refactor can
  break it; updating the plugin is then needed. It does **not** patch anything
  on disk.
* Rendering is synchronous: the first paint of a formula costs a few
  milliseconds (then it is cached). Very large documents with hundreds of
  distinct TeX labels will feel that on the first draw.
* What it costs: the one-font package adds 14 MB of memory once MathJax is used
  and 0.11 s to startup; the `allfonts` package takes 1.4 s, because
  every font's glyph tables have to be in the engine at once. Nothing of that is
  in upstream Veusz, and none of it is paid at all until a label asks for
  MathJax.
* **Characters the math font lacks come from another font.** CJK inside a
  formula (`\text{中文}`), a rare symbol, an emoji: MathJax hands those to the
  SVG as a `<text>` element, and they are drawn at the font size you asked for,
  in the font the text element is set in — the **Font** row in the formatting
  panel — so a formula's CJK matches the text around it. (MathJax itself names
  only a generic family for them, and sizes them as *2ex* of the chosen math
  font, which made the same characters 17% larger under Fira than under Termes
  and 12% smaller than a plain label at that size; the plugin substitutes the
  element's own font and one em, so neither the font nor the math font moves
  them. If that font has no such character either, Qt's system fallback supplies
  it, as it always did.) Measured in Veusz 4.2.1/Qt 6.10.2, a 20pt `\text{珠子}`
  is 39.6x19.4pt of paper under every one of the twenty math fonts, and 39.6x19.4pt
  as a plain label. Their design and exact spacing are that font's, not the math
  font's. The plugin paints them as
  outlines rather than leaving them as text, because text does not survive
  Veusz's painting: every widget is recorded onto a device and replayed, and Qt
  sizes SVG text against that device's resolution instead of in the SVG's own
  units, so the characters came out **dpi/72 times too large** — measured x1.3 on
  a 96dpi screen, x2.1 in a 150dpi export and x4.2 at 300dpi — and spilled out of
  the box the formula had reserved for them (a 20pt `\text{珠子}` was given a
  40x19pt box and drew 150x72px of ink into an 83x40px one). Measured in Veusz
  4.2.1 with Qt 6.10.2, the same glyph through the recording device came out
  478x243px where the path form stayed at 216x68px. Everything else in the
  formula was always exact: it is drawn as paths, and a path is only
  coordinates. Converting the text to outlines here — Qt draws the identical
  pixels (1 of 5393 differs, IoU 1.000) — makes the characters page-relative and
  filled like the rest of the formula: `\text{珠}` now measures 17.2pt of paper
  on a 96dpi screen, 16.8pt at 150dpi and 16.3pt at 300dpi, with nothing left as
  text. `test/smoke_test.py` fails if a character the font does not have differs
  by more than 8% between 96dpi and 300dpi, overflows its box, is still painted
  as `<text>`, ignores the Font setting, or changes size with the math font.
* Prebuilt binaries are **Windows x64 only** (see *Platforms*).

## Related

* Releases and issue tracker:
  <https://github.com/zhaiyusci/veusz-mathjax-plugin>
* Veusz itself: <https://veusz.github.io/>
* Feature request this plugin answers: [veusz/veusz#792](https://github.com/veusz/veusz/issues/792)
* The same feature as a pull request against Veusz, kept for reference: [veusz/veusz#819](https://github.com/veusz/veusz/pull/819)

## Acknowledgements

* **[QuickJax](https://github.com/Qalxry/QuickJax)** — Qalxry's zero-dependency
  MathJax v4 SVG renderer for Python, which runs MathJax inside QuickJS. This
  plugin exists because that one does: the first prototype here rendered through
  QuickJax's prebuilt bundle, and the bundle shipped today is built from the
  MathJax packages with the recipe learned there — inline every dynamic font
  range into the bundle, make `asyncLoad` a no-op, and call
  `loadDynamicFilesSync()`, because QuickJS is synchronous and MathJax's
  on-demand font loading has no `Promise` to retry with. QuickJax is MIT; no
  QuickJax code is redistributed here.
* **MathJax**, **QuickJS (quickjs-ng)** and **esbuild** do the actual work — see
  `THIRD_PARTY.md` and `NOTICE`.
* **Veusz**, by Jeremy Sanders — the host all of this plugs into.

## License

Apache-2.0 — see `LICENSE`. It is a lax, non-copyleft licence: it does not
require anything you build with this to be licensed the same way.

`data/` is one licence per file: `qjs.dll` is MIT (quickjs-ng, built
unmodified), `mathjax_bundle.js` is Apache-2.0 (MathJax 4), and
`mathjaxbridge.dll` is Apache-2.0 (this project). See `NOTICE` for the
attributions and `THIRD_PARTY.md` for the details and the licence texts in
`licenses/`.

Veusz itself is GPL-2.0-or-later and is **not** included here: this plugin
contains no Veusz source code and loads Veusz at run time through its plugin
interface.
