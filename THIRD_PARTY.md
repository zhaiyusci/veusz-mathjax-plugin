# Third-party components and licences

This plugin is distributed under the **Apache License 2.0** (see `LICENSE`).

The layout is deliberately **one licence per artefact**, so nothing here needs
a compatibility argument:

| Artefact | What it is | Licence |
|---|---|---|
| `veusz_mathjax.py` | the plugin itself | **Apache-2.0** (this project) |
| `data/mathjaxbridge.dll` / `.so` / `.dylib` | the JS host, built from `src/mathjax_bridge.cpp` | **Apache-2.0** (this project) |
| `data/qjs.dll` / `libqjs.so` / `libqjs.dylib` | QuickJS, built unmodified from quickjs-ng 0.16.2 | **MIT** — text in `licenses/quickjs-ng-LICENSE.txt` |
| `data/mathjax_bundle.js` | MathJax 4.1.3 + font packages, bundled by esbuild | **Apache-2.0** — same text as `LICENSE` |
| esbuild | build tool | MIT, used at build time only, not redistributed |

## Why the binaries are split

QuickJS used to be linked into `mathjaxbridge.dll`. It is now its own
`qjs.dll`, which the bridge **imports**, for three reasons:

1. **Licence boundaries you can see.** One file, one licence. QuickJS ships as
   upstream builds it (`cmake -DBUILD_SHARED_LIBS=ON`), so "unmodified, MIT" is
   a property of the file rather than a claim about a build recipe.
2. **The engine can be updated on its own.** A QuickJS security fix means
   rebuilding `qjs.dll`, not this project's C++ code.
3. **One engine, several renderers.** Another backend (KaTeX, say) can import
   the same `qjs.dll`.

Measured effect on size: `qjs.dll` 1,075,712 B + `mathjaxbridge.dll` 41,984 B
= 1,117,696 B, against 1,099,264 B for the single statically-linked binary —
18,432 B (18 KB) more. Splitting costs 1.7% and buys the above.

Keeping QuickJS statically linked would have been perfectly legal — MIT permits
it, and the only obligation is to ship the notice. The split is engineering,
not a compliance requirement.

## Are these licences compatible?

They do not even have to be compatible, because no two licences meet inside a
single file. For completeness:

* **MIT (QuickJS)** is lax and non-copyleft; it is compatible with everything
  here.
* **Apache-2.0** is lax and non-copyleft too. It is *not* a copyleft licence:
  it does not require derivative works to be licensed the same way. Its extra
  conditions compared with MIT are paperwork plus a patent grant — keep the
  notices, mark modified files, propagate a `NOTICE` file if the original had
  one (MathJax ships none), no trademark rights, and the patent licence ends
  if you sue the project over patents.
* **Veusz** is GPL-2.0-or-later, and is **not redistributed here**. This
  distribution contains no Veusz source code; the plugin loads Veusz at run
  time through its plugin interface, like any other Veusz plugin. Should
  someone redistribute this plugin together with Veusz, that combination is
  fine as well: the Apache-2.0 terms are GPL-compatible (GPLv3 directly, GPLv2
  via "or later"), so the combination can be passed on under the GPL.

## What a release must contain

`tools/build_all.py` packages all of it:

1. `LICENSE` — Apache-2.0 (this project).
2. `NOTICE` — attribution for MathJax, mhchemParser and QuickJS.
3. `THIRD_PARTY.md` — this file.
4. `licenses/quickjs-ng-LICENSE.txt` — the MIT notice for the engine.
5. `licenses/mathjax-Apache-2.0.txt` — a copy of the Apache-2.0 text next to
   the bundle, so `data/` is self-describing if it is separated from the rest.
6. `data/qjs.dll`, `data/mathjaxbridge.dll`, `data/mathjax_bundle.js`.

## Credited, not redistributed

**[QuickJax](https://github.com/Qalxry/QuickJax)** (MIT, by Qalxry) is a
zero-dependency MathJax v4 SVG renderer for Python that runs MathJax inside
QuickJS. It is **not** part of this distribution — nothing of its code is in
`veusz_mathjax.py`, in the bridge or in the bundle — but this project would not
exist without it: the first prototype rendered through QuickJax's prebuilt
bundle, and `tools/build_bundle.py` builds its entry with the recipe learned
there (inline every dynamic font range, no-op `asyncLoad`,
`loadDynamicFilesSync()`, `fontCache: "local"`), because QuickJS is synchronous
and MathJax's on-demand font loading has no `Promise` to retry with.

## Generated data

`data/mathjax_bundle.js` is **generated**: `tools/build_bundle.py` produces it
from the npm packages below. MathJax is minified and merged into one script,
and the glyph ranges are inlined because the embedded engine cannot fetch
anything at render time. The builder prepends a banner naming the packages and
the licence, so the file carries its own provenance. Reproducing it needs only
npm and these pinned versions:

```
@mathjax/src@4.1.3
@mathjax/mathjax-newcm-font@4.1.3        (or another official font: --font)
@mathjax/mathjax-mhchem-font-extension@4.1.3
@mathjax/mathjax-bboldx-font-extension@4.1.3
@mathjax/mathjax-dsfont-font-extension@4.1.3
@mathjax/mathjax-bbm-font-extension@4.1.3
esbuild                                  (build tool)
```

`data/qjs.dll` is generated too, but by upstream's own build:
`src/build-quickjs-windows.cmd` runs `cmake -DBUILD_SHARED_LIBS=ON` on an
unmodified quickjs-ng checkout. The revision used for published binaries is
recorded in the release notes.
