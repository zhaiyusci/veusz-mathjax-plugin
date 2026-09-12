# One font each, to add to a plugin you already have

These are the math fonts the plugin can be given, one file per font. They are
kept here in the repository rather than attached to a release, so the link to
one never changes: `fonts/mathjax-termes.js` is that font, at every version.

**To add one:** download the file (right-click the *Raw* button and choose
"Save link as…" — a browser will otherwise show you three megabytes of
JavaScript) and put it in the `data/` folder of your plugin:

```
veusz-mathjax-plugin/
  veusz_mathjax.py
  data/
    mathjax_bundle.js     the fonts that came with your package
    mathjax-termes.js     <- the one you just added
```

Restart Veusz and it is in the chooser of the MathJax row, on every text
element. Nothing has to be configured and no list is updated: each bundle names
itself in its first line, and the plugin reads that. Take as many as you like —
they do not interfere with each other or with the fonts your package already
had — and delete the file again to remove the font. A font nobody selects is
never loaded, so one you do not use costs nothing but disk space.

| file | font | download |
|---|---|---|
| `mathjax-newcm.js` | New Computer Modern (MathJax's own default) | 11 MB |
| `mathjax-tex.js` | Computer Modern (TeX) — the one in the small package | 3.2 MB |
| `mathjax-modern.js` | Modern | 5.4 MB |
| `mathjax-stix2.js` | STIX Two | 4.8 MB |
| `mathjax-termes.js` | Termes (Times-like) | 3.6 MB |
| `mathjax-pagella.js` | Pagella (Palatino-like) | 4.1 MB |
| `mathjax-schola.js` | Schola (Charter-like) | 3.8 MB |
| `mathjax-bonum.js` | Bonum (bookman-like) | 3.6 MB |
| `mathjax-fira.js` | Fira | 4.3 MB |
| `mathjax-dejavu.js` | DejaVu | 2.8 MB |
| `mathjax-asana.js` | Asana | 2.8 MB |
| `mathjax-lete.js` | Lete Sans Math | 2.6 MB |

The `allfonts` package in a release has all twelve already, in one bundle, which
is a smaller download than the twelve files here (11 MB against 52 MB) because
they share one copy of MathJax. Take these when you want one or two fonts more
than your package has.

## Rebuilding them

    python tools/build_all.py --flavor fonts                  # all twelve
    python tools/build_all.py --flavor fonts --font termes    # just one

They are normally left alone: a bundle only changes when the MathJax version it
embeds changes or that font's data does, and the script keeps an existing file
when it was built with the current version. `--rebuild-fonts` forces it. When a
bundle is rebuilt, the release that follows is a good moment to refresh these
files too, since an older bundle keeps working but embeds an older MathJax.
