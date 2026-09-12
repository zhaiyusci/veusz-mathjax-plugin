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
| `mathjax-termes.js` | Termes (Times-like) | 2.0 MB |
| `mathjax-bonum.js` | Bonum (bookman-like) | 1.9 MB |
| `mathjax-schola.js` | Schola (Charter-like) | 2.1 MB |
| `mathjax-pagella.js` | Pagella (Palatino-like) | 2.4 MB |
| `mathjax-fira.js` | Fira | 2.7 MB |
| `mathjax-stix2.js` | STIX Two | 3.2 MB |
| `mathjax-modern.js` | Modern | 3.8 MB |
| `mathjax-newcm.js` | New Computer Modern (MathJax's own default) | 10.9 MB |

The `allfonts` package in a release has all twelve already, in one bundle, which
is still the smaller download if you want most of them (11 MB against 34 MB for
all of these). Take these when you want one or two more than your package has —
and note that `newcm` alone is most of that 34 MB.

## Rebuilding them

    python tools/build_all.py --flavor fonts                  # all twelve
    python tools/build_all.py --flavor fonts --font termes    # just one

They are normally left alone: a file only changes when the font's own data
changes or the way it is written does, and the script keeps an existing one when
its header says it was built for the current MathJax version.
`--rebuild-fonts` forces it.
