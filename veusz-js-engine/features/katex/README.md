# katex

Formulas for Veusz, parsed by [KaTeX](https://katex.org/) and **drawn by
Veusz itself** — a second feature on [the platform](../../README.md), and the
opposite of the `mathjax` one: it draws nothing.

```
veusz-js-engine/features/katex/
  feature.js                  the whole feature: settings, and one delegation
  katex.min.js                KaTeX 0.18.7 (MIT), the parser
  LICENSE-KATEX.txt           its licence, as it ships
  README.md                   this file
  test/test_katex_feature.py  its own tests, beside it
```

## How it works, and why there is no MathJax here

Veusz renders MathML by itself: a text whose whole body is a `<math>…</math>`
document is handed to `veusz/utils/textrender.py`'s `_MmlRenderer`, which is
Qt's MML widget, drawing in the element's own font (its family and its point
size). Nothing has to be installed for that; it is a Veusz feature.

So the whole feature is three steps:

1. KaTeX parses the label's text and hands back MathML
   (`renderToString(tex, {throwOnError: true, output: 'mathml'})`);
2. two things KaTeX puts in that MathML are dealt with (see below);
3. the platform is told `veusz.delegate(mathml)` — "you draw this" — and Veusz
   draws it instead of the label's LaTeX, which stays in the document.

There is no MathJax in this feature and no drawing code either: 266 KB of
parser, and a `feature.js` of about a hundred lines. A picture of ours would
have had to reproduce a layout, a font and a baseline; this way the formula is
typeset by the same engine as the rest of the label, and a change to Veusz's
MathML rendering is a change you get for free.

**What you get is therefore Veusz's MathML renderer, not KaTeX's appearance.**
KaTeX's own look lives in its CSS and its fonts and its HTML; that cannot be
drawn here. What KaTeX contributes is the *parser*: its LaTeX coverage, its
error messages, and its speed.

## What KaTeX's output needs before Veusz sees it

Two things, both measured against Qt's widget, both a line of JavaScript.

**KaTeX's copy of the LaTeX is taken out.** Each formula comes wrapped in
`<semantics>`, with the presentation in an `<mrow>` and the LaTeX it was parsed
from in an `<annotation encoding="application/x-tex">` beside it. Qt's widget
does not know that element, so it draws the text inside it — the formula came
out with its own source after it, and the label was as wide as the source:

| formula | with the annotation | without |
|---|---|---|
| `\frac{a}{b}` | 212 px wide, 2480 ink | **22 px**, 533 ink |
| the quadratic | 591 px, 5859 ink | **272 px**, 3152 ink |

The annotation is KaTeX's note to itself; the LaTeX is already in the label.
Dropping it is the whole fix, and the width is what the feature's test pins.

**A space is no longer an empty `<mtext>`.** KaTeX writes a thin space (`a\,b`)
as `<mtext>` holding a single space character; Qt drops the character and then
refuses the element for having no content, so the formula came out as Veusz's
red error text. MathML has `<mspace>` for a space, so those become
`<mspace width="… em">`.

**`$…$` comes off, and a parse error becomes `<merror>`.** A label written for
another engine often carries the dollars, and KaTeX would either typeset them or
refuse the input, so a `$…$` or `$$…$$` wrapper is stripped when that is what it
is. A formula KaTeX cannot parse becomes
`<math><merror><mtext>…</mtext></merror></math>`, which Veusz's widget draws
where the formula would have been: the label keeps its box, and the text says
what happened.

The last two are not my invention — they are what the parent project's KaTeX
engine did (`tools/build_katex_bundle.py`, and `docs/veusz-mathjax-engine.md`
§9), where KaTeX was wired into Veusz's own `_MmlRenderer` the same way this
feature does it. That round also measured the engine as a whole: a 263 KiB
bundle, 20 ms to initialise, 0.4 ms per formula, against MathJax's 11.3 MiB and
7.8 ms -- and judged the quality "mathematically correct and readable, spacing
tight, `\mathbb` degrades to upright letters". The same
`<semantics>`/`<annotation>` unwrapping is there too.

Everything else of the twenty constructs tried passes through untouched:
fractions, roots, scripts, sums and integrals with limits, `\text` (including
CJK), matrices, `\left(…\right)`, accents, blackboard bold, `\binom`, `\lim`,
`\overline`, relations and bold italic. What Qt still refuses is its own subset
talking — `\stackrel` was one of them — and then Veusz draws its own
`Error interpreting MathML: …` in red, which is honest about it. The pictures
that measurement produced are in `build/native-mml/` of the checkout, and
`build/probe_mml_native.py` makes them again.

## Install

Drop `katex/` into the platform's `features/` directory (or point
`VEUSZ_JS_ENGINE_FEATURES` at the directory that contains it) and restart
Veusz. A text element then has a **KaTeX** row:

```
KaTeX:  [x]   [ ] Display style
```

Write the formula on its own — no `$…$`. The two settings are per text
element, and both are written into the document under this feature's own
names (`katex`, `katexDisplay`).

## The fraction rule is thin, and it was thin before

Veusz's MathML renderer lays a formula out at **five times** the point size,
records it at screen dpi, and replays it scaled down by
`dpi / screen / upscale` (`veusz/utils/textrender.py`, `_MmlRenderer`,
`upscale = 5.`). Anything the widget draws as a one-pixel line in that recording
comes out at a fifth of a pixel — and a fraction rule is a line. Measured on
`\frac{a}{b}` at 20 pt as the ink in the rule's own column, in a column where no
glyph is in the way:

| | 150 dpi | 300 dpi |
|---|---|---|
| Veusz MathML (this feature) | 0.20 px | 0.10 px |
| the `mathjax` feature's SVG | 0.35 px | **1.17 px** |

So the rule does not merely look thin: at print resolution it is a tenth of a
pixel of ink, and it gets *worse* as the resolution rises, where MathJax's gets
better.

This is Veusz's MathML widget — not KaTeX, and not this feature. The parent
project's KaTeX engine had exactly the same thing and left it alone: its
`_MmlRenderer` keeps `upscale = 5.` verbatim, and its notes record "spacing
tight" without ever mentioning the rule. Changing it means taking over that
method, because the factor is not a setting (a pen on the recording painter does
nothing: the widget sets its own per element, measured). Zooms of the rule, at
both resolutions, are in `build/rules/` of the checkout if that judgement is
worth making; `build/probe_rule.py` makes them again.

## Limits worth knowing

* **Display style is KaTeX's, and Qt ignores the attribute that says so.**
  KaTeX emits `<math display="block">` *and* switches big-operator limits from
  `msubsup` to `munderover`; measured, Qt's widget takes no notice of the
  attribute and only the structure changes the picture (a sum's ink box goes
  from 51 px to 84 px at 150 dpi). Display style works; it is the structure
  that does it.
* **A space the widget drops stays dropped.** Qt accepts `<mspace>` but draws
  it as nothing, so `a\,b` is laid out as `ab` — the same as before the fix,
  only without the error. Nothing else is lost.
* **Qt's widget is a partial MathML implementation, and it draws what it does
  not know.** The twenty constructs above work; something outside them (the
  markup `\stackrel` makes is one) shows Veusz's own
  `Error interpreting MathML: …` in red — and an element Qt does not recognise
  has its *text content* drawn, which is what KaTeX's annotation did until it
  was taken out.
* **KaTeX's error is a message.** A formula KaTeX cannot parse draws the
  platform's `cannot draw: KaTeX could not typeset this: …` where the formula
  would have gone, rather than a partly-drawn formula.
* This feature is not a port of anything in the parent project; it exists to
  answer whether a second typesetter fits the platform. It does: a feature
  directory, no platform change beyond the `delegate` reply, and none of the
  drawing machinery.

## Tests

```bash
PYTHONPATH=<your Veusz> python veusz-js-engine/features/katex/test/test_katex_feature.py
```

Run it with the Python of the Veusz you use. It checks that the feature loaded
and its two settings are one row of the panel; that off is off; that the answer
is a delegation carrying a `<math>` document rather than an SVG; that an export
goes through Veusz's own MathML renderer **without it reporting an error** (the
difference between a formula and Veusz's explanation of why not — both put ink
on the page); the space fix; that display style reaches the delegated MathML;
and that a formula KaTeX cannot parse is a message rather than a broken label.
