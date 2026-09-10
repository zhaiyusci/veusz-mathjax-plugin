# veusz-mathjax-plugin

MathJax-rendered math for [Veusz](https://veusz.github.io/), as a plugin.

Veusz's built-in math support is MathML plus some Unicode tricks. This plugin
adds a **"Use TeX" checkbox** to text objects and renders the formula with
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

## Install (from a release zip)

1. Unpack the zip anywhere, e.g. `C:\tools\veusz-mathjax-plugin`.
   Keep `veusz_mathjax.py` and `data/` together.
2. In Veusz: **Edit → Preferences → Plugins → Add…** and pick
   `veusz_mathjax.py`. Press OK, then restart Veusz.
3. Select any text object (label, axis label, tick labels, key, contour
   label, …) and open its **Text** properties: there is now a **Use TeX**
   checkbox. Tick it and type TeX, e.g.

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

* The checkbox lives with the text settings of each widget: labels and axis
  labels have their own, tick labels have theirs under the axis's *TickLabels*
  settings, the key has its own, and so on. It is an ordinary Veusz setting, so
  it is saved in `.vsz` documents like any other.
* The formula takes its colour from the current pen; changing the colour or the
  font size re-renders it, and results are cached per (text, size, colour).
* If anything goes wrong (a TeX error, a missing data file), the plugin falls
  back to the normal text renderer instead of breaking the plot — the label then
  shows the source text, and the reason appears at startup and in the log.

## What it renders

MathJax 4.1.3, TeX input, with these input packages enabled:

```
base  ams  newcommand  boldsymbol  braket  cancel  color  enclose  extpfeil
html  mhchem  noerrors  noundefined  physics  mathtools  amscd  action  bbox
unicode  verb  textmacros  textcomp  cases
```

That covers AMS math, matrices and `cases`, `\mathbb`/`\mathfrak`/`\mathcal`/
`\mathsf`/`\mathtt`, `\xrightarrow`, `\ce{2H2 + O2 -> 2H2O}`, `\dv{f}{x}`,
`\qty(…)` and the rest of those packages. The font is MathJax 4's default, New
Computer Modern, bundled with all 40 of its glyph ranges inlined, which also
covers Greek, Cyrillic, Hebrew, Devanagari and Cherokee inside a formula.

The formula is placed using the baseline MathJax reports, so TeX labels line up
with each other and with plain text, and it is drawn as paths, so vector exports
stay vector.

## Package layout

A release zip unpacks to exactly this:

```
veusz-mathjax-plugin/
  veusz_mathjax.py       the plugin — the file you add to Veusz
  data/
    mathjax_bundle.js    MathJax 4 + fonts, bundled with esbuild      (~12 MB)
    mathjaxbridge.dll    the JS host, built from src/mathjax_bridge.cpp
    qjs.dll              QuickJS (quickjs-ng), imported by the bridge (~1 MB)
  README.md
  LICENSE                Apache-2.0
  NOTICE                 attribution for MathJax, mhchemParser and QuickJS
  THIRD_PARTY.md         what is inside, and one licence per artefact
  licenses/              QuickJS (MIT) and MathJax (Apache-2.0) texts
```

The source repository additionally has `src/` (the bridge), `tools/` (the build
scripts), `test/` and `VERSION`.

The data files are found through `VEUSZ_JSENGINES_BRIDGE` /
`VEUSZ_JSENGINES_BUNDLE` / `VEUSZ_JSENGINES_QUICKJS` first, then in `data/` next
to the plugin file (and next to its parent, so several plugins can share one
`data/`). The folder can therefore be moved anywhere, as long as it is kept
together.

## Troubleshooting

| Symptom | What to do |
|---|---|
| No **Use TeX** checkbox | The plugin did not install. Read `veusz_mathjax.log` next to the plugin file, or start Veusz from a console for the message. |
| The checkbox appears, but the label shows the TeX source | Rendering failed and the plugin fell back to plain text on purpose. The log names the reason; a missing `data/qjs.dll` or `data/mathjax_bundle.js` shows up at startup. |
| The label shows `$x^2$`, dollars included | Drop the `$…$` — the whole label is the formula. |
| Nothing happens at all, no log | The plugin was probably not added in Preferences → Plugins, or Veusz was not restarted. |
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
python tools/build_all.py                 # add --font tex for a 3 MB bundle
                                          #         --trim to drop rare scripts
                                          #         --skip-quickjs / --skip-bridge
                                          #         to rebuild only part of it
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
access to Veusz's internals. It uses exactly five hooks:

1. **Settings** — wraps `veusz.setting.collections.Text.__init__` to add the
   `useTeX` boolean to every text-bearing widget. The properties panel is
   generated from the settings tree, so the checkbox appears by itself and is
   saved in `.vsz` files like any other setting.
2. **Which text is this?** — wraps `makeQFont` of the text settings class.
   Every widget builds the font of a text element from that element's own
   settings group just before drawing it (`s.get('TickLabels').makeQFont(
   painter)` for the tick numbers, `s.get('Label').makeQFont(painter)` for an
   axis label, `s.get('Text')` for a label or a key), so recording which group
   made the font tells the renderer which group the text belongs to. Without
   this, an axis's *Use TeX* on the label would drag its tick numbers along.
3. **Widget wiring** — wraps `draw` of every registered widget class to publish
   the widget's own settings as a fallback, for text painted without a
   `makeQFont` of its own. No widget internals are touched. The search also
   looks into nested groups (`settings.ticklabels`, `settings.label`, …),
   because an axis keeps its text settings there.
4. **Renderer** — wraps `veusz.utils.Renderer`, the single place every widget
   paints text through: for text whose settings group asked for TeX it returns
   its own renderer, otherwise the original one.
5. **Drawing** — the renderer asks the bridge for the SVG of the formula, draws
   it with Qt's SVG renderer, rotates it if the label is rotated, and positions
   it using the baseline MathJax reports (`vertical-align`). Colour comes from
   the current pen, and results are cached per (text, size, colour).

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
* The MathJax bundle is ~12 MB (3 MB with `--font tex`, ~7 MB with `--trim`),
  the QuickJS engine is ~1 MB and the bridge ~41 KB. Nothing of that is in
  upstream Veusz.
* `\text{中文}` (CJK inside a formula) needs a font that has those glyphs; the
  bundled math font does not, and Veusz's fallback for SVG text is limited.
  CJK in plain (non-TeX) labels is unaffected — that is Veusz's own text path.
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
