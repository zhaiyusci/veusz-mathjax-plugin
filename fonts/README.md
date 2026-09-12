# One font each, to add to a plugin you already have

These are the math fonts the plugin can be given, one file per font. **They carry
no MathJax**: each holds one font's data and registers it into the MathJax that
the plugin already loaded, so every font here shares that one copy. That is why
they are a megabyte or two rather than three to eleven: the core is not repeated.

They are kept in the repository rather than attached to a release, so the link to
one never changes: `fonts/mathjax-termes.js` is that font, at every version.

**To add one:** download the file (right-click the *Raw* button and choose
"Save link as…" — a browser will otherwise show you a megabyte of JavaScript) and
put it in the `data/` folder of your plugin:

```
veusz-mathjax-plugin/
  veusz_mathjax.py
  data/
    mathjax_bundle.js     MathJax itself, and the fonts your package came with
    mathjax-termes.js     <- the one you just added
```

Restart Veusz and it is in the chooser of the MathJax row, on every text
element. Nothing has to be configured and no list is updated: each file names
itself in its first line, and the plugin reads that. Take as many as you like —
they do not interfere with each other or with the fonts your package already had
— and delete the file again to remove the font. A font nobody selects is never
loaded, so one you do not use costs nothing but disk space.

This needs a plugin whose `mathjax_bundle.js` offers the registration (anything
built from this version of the source or later). A file from here does not work
with an older plugin — it will say so rather than draw the wrong font.

| file | font | download |
|---|---|---|
| `mathjax-tex.js` | Computer Modern (TeX) — the one in the small package | 1.4 MB |
| `mathjax-dejavu.js` | DejaVu | 1.1 MB |
| `mathjax-asana.js` | Asana | 1.1 MB |
| `mathjax-lete.js` | Lete Sans Math | 1.4 MB |
| `mathjax-luciole.js` | Luciole Math — thick strokes, made for low vision | 0.9 MB |
| `mathjax-euler.js` | Euler Math — Zapf's upright, calligraphic-flavoured | 1.5 MB |
| `mathjax-sans1.js` | **Sans 1** — see below; it is KpMath Sans | 1.1 MB |
| `mathjax-sans2.js` | **Sans 2** — see below; it is ArsenalMath Sans | 1.1 MB |
| `mathjax-bonum.js` | Bonum (bookman-like) | 1.9 MB |
| `mathjax-termes.js` | Termes (Times-like) | 2.0 MB |
| `mathjax-pennstander.js` | Pennstander Math — a wide, friendly sans | 2.1 MB |
| `mathjax-schola.js` | Schola (Charter-like) | 2.1 MB |
| `mathjax-pagella.js` | Pagella (Palatino-like) | 2.4 MB |
| `mathjax-fira.js` | Fira | 2.7 MB |
| `mathjax-neohellenic.js` | GFS Neohellenic Math — Greek sans, made for slides | 2.8 MB |
| `mathjax-sans3.js` | **Sans 3** — see below; it is New Computer Modern Sans Math | 3.0 MB |
| `mathjax-plex.js` | IBM Plex Math — sans, several weights upstream | 3.1 MB |
| `mathjax-stix2.js` | STIX Two | 3.2 MB |
| `mathjax-modern.js` | Modern | 3.8 MB |
| `mathjax-newcm.js` | New Computer Modern (MathJax's own default) | 10.9 MB |

Ten of these are sans-serif *faces* — whole fonts whose digits, operators,
brackets and Greek are sans, not just the letters (`\mathsf` gives those in every
font above).  `lete`, `luciole`, `pennstander`, `plex`, `neohellenic`, `euler` and
the three Sans fonts come from OpenType math fonts converted for this project;
`fira`, `dejavu` and the rest are MathJax's own packages.

The `allfonts` package in a release has every one of them already, in one bundle,
and is a much smaller download than the files here added up (16 MB against 52 MB
for all of them), because they share one copy of MathJax and the zip compresses
the glyph data. Take these when you want one or two more than your package has —
and note that `newcm` alone is a fifth of that 52 MB.

## Licences

The MathJax fonts are **Apache-2.0** like MathJax; every converted font is
**OFL-1.1** except Sans 3, which is under the **GUST Font License**.  The
converted files keep their own copyright lines, which travel in the font data:

| file | copyright |
|---|---|
| `mathjax-lete.js` | Lete Sans Math (c) 2024-2026 Chenjing Bu, Daniel Flipo |
| `mathjax-luciole.js` | Luciole Math (c) 2024-2026 Daniel Flipo, Laurent Bourcellier, Jonathan Fabreguettes |
| `mathjax-euler.js` | (c) 1997, 2009 American Mathematical Society; (c) 2009, 2021 Khaled Hosny |
| `mathjax-pennstander.js` | (c) 2020 The Grandstander Project Authors; design Ty Fink |
| `mathjax-neohellenic.js` | (c) 2016 George D. Matthiopoulos (GFS); MATH table by Antonis Tsolomitis, University of the Aegean |
| `mathjax-plex.js` | (c) 2020 IBM Corp.; design by Mike Abbink and others |
| `mathjax-sans1.js` | (c) 2007-2018 Christophe Caignaert; (c) 2019-2026 Daniel Flipo |
| `mathjax-sans2.js` | as Sans 1 — this font is built on it |
| `mathjax-sans3.js` | (Copyleft) 2019-2026 Antonis Tsolomitis |

`licenses/OFL-1.1.txt` is the OFL text, and `licenses/newcm-sans-math-GUST.txt`
the GUST one for Sans 3.

### Why three of them are called Sans 1, Sans 2 and Sans 3

**Not laziness, or not only.**  KpMath Sans declares `Reserved Font Name <Kp>` and
`<KpMath-Sans>`, and ArsenalMath Sans is built on it; New Computer Modern Sans
Math is under the GUST licence, which like the LPPL expects a modified version to
be renamed.  A conversion is a modified version, so those names may not be used
for it — OFL §3 restricts exactly that, the primary name presented to users.
Their copyright holders are credited above and their licences travel with the
files, which is what the licence actually asks for; only the name is different.
Take them up with the font authors if you want the original names back.

### Not here, and why

**Arev** is not a font that can be converted at all: TeX Live ships it as Type 1
(`ArevSans-Roman.pfb` and friends) and its "math" is assembled by LaTeX from
several fonts — Arev for letters, MathDesign for symbols, Fourier for
blackboard-bold. There is no single OpenType file with a MATH table to convert;
the licence is the permissive Bitstream Vera one, so an OTF would be welcome
should one appear.

## Rebuilding them

    python tools/build_all.py --flavor fonts                  # every font above
    python tools/build_all.py --flavor fonts --font termes    # just one

They are normally left alone: a file only changes when the font's own data
changes or the way it is written does, and the script keeps an existing one when
its header says it was built for the current MathJax version.
`--rebuild-fonts` forces it.

A font that is not one of these — one you converted yourself — becomes a file here
the same way: `python tools/build_bundle.py --font-data --font <id> --out
fonts/mathjax-<id>.js`. See `local-fonts/README.md` for the conversion itself.
