# Fonts converted by us

MathJax ships eleven fonts, and its own font tools are not public (the
repository its npm packages point at returns 404, and the v4 documentation still
says the tools are "not yet ready for public release").  Fonts that MathJax does
not ship are converted here instead, with

    python tools/build_mathjax_font.py --regular <font.otf> [--bold <b.otf>] \
        --name <id> --title "<name>" --template <official package> ... \
        --out local-fonts

and `tools/build_bundle.py` finds them here without any extra flag (it appends
this directory to the package search path and tells esbuild where the package
is).  Only the SVG output is generated -- no web fonts, no HTML/CSS data -- since
that is all our plugin uses.

A converted font is not part of a full build until its id is added to `ALL_FONTS`
in `tools/build_bundle.py`; `lete` is deliberately left out of that list for now,
so it builds on request with `--font lete` and the published `allfonts` package is
still the eleven MathJax fonts its README describes.

## mathjax-lete-font

**Lete Sans Math**, a sans-serif OpenType math font based on Lato, by
Chenjing Bu and Daniel Flipo, version 0.61.  Converted from the copies in TeX
Live 2026:

    texmf-dist/fonts/opentype/public/lete-sans-math/LeteSansMath.otf       (regular)
    texmf-dist/fonts/opentype/public/lete-sans-math/LeteSansMath-Bold.otf  (bold)

Licence: **SIL Open Font License 1.1** (see `OFL.txt`, which is the font's own
licence statement).  The font declares no Reserved Font Name of its own; it was
renamed from "Lato Math" precisely because of the reserved name on Lato, so that
name is not used here either.  The conversion is a derived work: the glyph
outlines and metrics are the font's, reformatted as the data MathJax reads.

What the conversion had to work out, and why (all measured against the official
packages and against Latin Modern Math, TeX Gyre Termes/Pagella and STIX Two):

* metrics are in **em** while path coordinates are in **1000 units per em**, and
  the depth is negated (a glyph that sits above the baseline has a negative
  depth), matching MathJax's own data;
* a vertical assembly is listed **bottom-first** by every font checked, while
  MathJax reads its `stretch` array as `[beg, ext, end, mid]` -- so the pieces are
  reversed, and a brace's five pieces collapse onto those four slots;
* MathJax draws assembly piece *i* from the variant named
  `defaultStretchVariants[i]`, so this package puts every piece in the `normal`
  table and points all four slots at it (the official fonts spread them over the
  `-ex-md` / `-lf-tp` / `-rt-bt` pseudo-variants);
* assembly pieces whose codepoints the official key sets never mention (this font
  keeps its brace and radical pieces in the private use area) are added to that
  table by the generator;
* MathJax's TeX input asks for the *spacing* accents by codepoint -- `\hat` is
  U+02C6, `\dot` U+02D9, `\breve` U+02D8, `\check` U+02C7, `\tilde` U+02DC -- and
  only then reroutes the combining marks to those same codepoints through its
  accent map.  This font has none of the spacing accents; it draws `\hat` from
  the combining mark U+0302 (unicode-math's convention).  Left as they were,
  MathJax could not find the accent glyph at all, asked the system for it, and
  drew a fallback `^` in a guessed position: the accents looked like small
  carets floating off to one side.  So each missing spacing accent is filled in
  from the font's combining mark, re-origined first -- a combining mark is drawn
  centred on the origin, over the *preceding* character (U+0302 here spans
  x -367..-33 with zero advance), while MathJax draws an accent as a standalone
  glyph centred over its base and expects the ink to start at x=0.
* An accent's vertical position comes from the h/d the data reports (the same
  convention as any glyph: depth is negated), which is why they land on top of
  the base rather than needing an offset;
* the size-variant list is clamped to MathJax's seven slots (normal, `-smallop`,
  `-largeop`, `-size3` .. `-size6`); a font may offer many more variant glyphs
  than that, and anything larger is assembled from the stretch pieces.

Verified with `test/smoke_test.py`-style checks and by rendering fractions,
roots, big operators, matrices, accents, arrows, braces, Greek and the
double-struck/calligraphic/fraktur variants, then exporting from the installed
Veusz 4.2.1.

The accents were also checked against **XeLaTeX itself** (TeX Live 2026 is on
this machine): the same formula was typeset with `unicode-math` and
`\setmathfont{LeteSansMath.otf}`, and the two renders measured glyph by glyph
(connected components, so a tall base's own top is not mistaken for an accent).
That is how the accent handling above was found -- an accent shape that does not
come from this font is a giveaway that MathJax fell back to a system font.

What that comparison says, at 60pt and 300dpi (accent width / how far its centre
sits from the base's centre / gap above the base) -- the right-hand column is what
this conversion produces now:

| formula       | XeLaTeX + Lete     | this conversion    |
| ------------- | ------------------ | ------------------ |
| `\hat{x}`     | 84 px, +9.5, 19    | 84 px, +9.5, 29    |
| `\hat{H}`     | 84 px, +8.5, 13    | 84 px, +8.5, 30    |
| `\hat{I}`     | 84 px, +12.0, 13   | 85 px, +12.0, 30   |
| `\vec{v}`     | 125 px, +0.0, 15   | 126 px, +0.5, 29   |
| `\vec{H}`     | 125 px, +8.0, 9    | 126 px, +8.5, 30   |
| `\widehat{H}` | 137 px, +8.0, 16   | 138 px, +8.5, 30   |
| `\widehat{M}` | 200 px, +12.5, 16  | 201 px, +11.5, 30  |

The width and the horizontal placement agree with the TeX engine to about a pixel.
The one remaining difference is the gap above the base (29-30px against 9-19px):
that is MathJax's own spacing, the same with its stock fonts (26-27px measured for
NewCM), and it comes from MathJax's accent spacing rules rather than from the font
data.

Two things the comparison settled, both measured rather than assumed:

* `\hat` does **not** widen over a wide base in TeX either (`\hat{x}`, `\hat{H}`,
  `\hat{I}` are all 84px) -- only `\widehat` does.  MathJax reproduces that split
  once the data below is in place: `\hat` keeps the plain glyph, `\widehat` grows.
* The horizontal placement needs the MATH table's **`sk`** on the base glyphs --
  the top accent attachment measured from the glyph's centre, which is what TeX
  uses.  Without it every accent sat ~0.05em left of where XeLaTeX puts it; with
  it the positions above match.  Only `sk` is emitted, not the italic correction:
  MathJax shifts an accent by `sk + 0.75 * ic` (common/Wrappers/scriptbase.js),
  and measured, adding the `ic` part pushes the accents ~0.09em too far right.

### `\widehat` / `\widetilde`: they need the *accent character* to be stretchy

MathJax's TeX input defines the two accents apart only by a stretchy flag, over
the same character (BaseMappings.js):

```js
hat:     [BaseMethods.Accent, '005E'],
widehat: [BaseMethods.Accent, '005E', true],
```

A stretchy accent widens only if that character has an entry with `sizes` in the
font's `delimiters` table (common/Wrappers/mo.js `getStretchedVariant`), which is
what makes the font's wider glyphs for the mark reachable at all.  **None of the
eleven official MathJax font packages provides one** -- checked for U+005E, U+007E,
U+02C6, U+0302, U+02DC in every `delimiters.js` -- so `\widehat` is a no-op with
every one of them, which is what first made it look like a MathJax limitation.
Supplying the entry is what makes it work: the conversion now keys the mark's
`dir: H, sizes: [0.334, 0.334, 0.55, 0.8, 1.2, 1.6, 2]` under U+005E and U+007E as
well, and `\widehat{H}` comes out 138px wide (0.55em, the same variant XeLaTeX
picks for it) while `\hat{H}` stays at 84px.

### `\widehat` and `\widetilde` do not widen, in MathJax

Measured, and it is MathJax's behaviour rather than anything missing here:

(The first two paragraphs of this section were written before that entry was
found, when `\widehat` looked like a MathJax limitation; they are kept because the
measurements in them are what led to the fix, but the conclusion in them is
wrong -- see the section above.)

* `\hat{H}` and `\widehat{H}` render **pixel-identically** (0 differing pixels at
  300dpi) with this font **and with the stock NewCM package**; both resolve to the
  same accent codepoint, so no font data can tell them apart *that way*.
* XeLaTeX, with the same font, does widen: `\hat{H}` 84px vs `\widehat{H}` 137px
  at 60pt -- 137px is 0.55em, exactly the second size variant the font lists for
  the mark (`sizes: [0.334, 0.334, 0.55, ...]`).
* Pointing U+0302 (which is what a TeX engine reaches for) at that wider glyph was
  tried and changes nothing: the entry has to be under the character MathJax's
  mappings actually carry, which is U+005E / U+007E.
