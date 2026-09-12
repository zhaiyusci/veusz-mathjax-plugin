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
| `mathjax-bonum.js` | Bonum (bookman-like) | 1.9 MB |
| `mathjax-termes.js` | Termes (Times-like) | 2.0 MB |
| `mathjax-pennstander.js` | Pennstander Math — a wide, friendly sans | 2.1 MB |
| `mathjax-schola.js` | Schola (Charter-like) | 2.1 MB |
| `mathjax-pagella.js` | Pagella (Palatino-like) | 2.4 MB |
| `mathjax-fira.js` | Fira | 2.7 MB |
| `mathjax-neohellenic.js` | GFS Neohellenic Math — Greek sans, made for slides | 2.8 MB |
| `mathjax-plex.js` | IBM Plex Math — sans, several weights upstream | 3.1 MB |
| `mathjax-stix2.js` | STIX Two | 3.2 MB |
| `mathjax-modern.js` | Modern | 3.8 MB |
| `mathjax-newcm.js` | New Computer Modern (MathJax's own default) | 10.9 MB |

The last seven are sans-serif *faces* — whole fonts whose digits, operators,
brackets and Greek are sans, not just the letters (`\mathsf` gives those in every
font above).  `lete`, `luciole`, `pennstander`, `plex` and `neohellenic` come from
OpenType math fonts converted for this project, as does `euler`; `fira`, `dejavu`
and the rest are MathJax's own packages.

The `allfonts` package in a release has twelve already, in one bundle, which is
still the smaller download if you want most of them (11 MB against 47 MB for all
of these). Take these when you want one or two more than your package has — and
note that `newcm` alone is most of that 47 MB.

## Licences

All of these are **OFL-1.1** except the MathJax ones (Apache-2.0, like MathJax).
The converted ones keep their own copyright lines, which travel in the font data:

| file | copyright |
|---|---|
| `mathjax-lete.js` | Lete Sans Math (c) 2024-2026 Chenjing Bu, Daniel Flipo |
| `mathjax-luciole.js` | Luciole Math (c) 2024-2026 Daniel Flipo, Laurent Bourcellier, Jonathan Fabreguettes |
| `mathjax-euler.js` | (c) 1997, 2009 American Mathematical Society; (c) 2009, 2021 Khaled Hosny |
| `mathjax-pennstander.js` | (c) 2020 The Grandstander Project Authors; design Ty Fink |
| `mathjax-neohellenic.js` | (c) 2016 George D. Matthiopoulos (GFS); MATH table by Antonis Tsolomitis, University of the Aegean |
| `mathjax-plex.js` | (c) 2020 IBM Corp.; design by Mike Abbink and others |

None of them declares a Reserved Font Name, so the converted files keep their
names. `licenses/lete-sans-math-OFL-1.1.txt` is the OFL text; the others ship the
same licence inside their own font data.

### Not here, and why

Three fonts were converted and are **not** published, because distributing a
modified version would need something we cannot simply decide:

* **KpMath Sans** and **ArsenalMath Sans** (the latter is built on the former)
  declare `Reserved Font Name <Kp>` and `<KpMath-Sans>`. The OFL forbids using a
  reserved name for a modified version, so these would have to be renamed — with
  the copyright holders' blessing, that is their call rather than ours.
* **New Computer Modern Sans Math** is under the GUST licence, which like the
  LPPL expects a modified version to be renamed, and its name is the point of it.

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
