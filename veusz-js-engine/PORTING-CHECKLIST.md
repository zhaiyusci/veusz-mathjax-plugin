# Feature inventory: the original `veusz_mathjax.py` (MathJax for Veusz)

Scope: everything the released plugin does, so the port to
`veusz-js-engine/features/mathjax/feature.js` forgets nothing.

Evidence base (all read in full):

| file | lines |
|---|---|
| `veusz_mathjax.py` | 1441 |
| `README.md` | 563 |
| `src/mathjax_text.js` | 102 |
| `tools/build_bundle.py` | 1104 |
| `test/test_text_font.py` | 270 |
| `test/smoke_test.py` | 601 |
| `veusz-js-engine/features/mathjax/feature.js` | **194** (the task says 190; the file is 194 lines) |

Supporting files read for the gap checklist only: `veusz-js-engine/features/mathjax/fonts.js` (20 —
since deleted, because the font list now comes out of the bundle's own declaration),
`.../mathjax/README.md` (119), `veusz-js-engine/jsapi.js` (169), `veusz-js-engine/veusz_js_engine.py`
(lines 1225-1464), `data/fonts.json` (126), `veusz-js-engine/features/mathjax/mathjax.js` (header +
API regions).

Citations are `file:line`. Nothing here is a design proposal; where the code is ambiguous it is
marked **ambiguous**.

---

## 1. Settings it adds to Veusz

All four are added by wrapping `veusz.setting.collections.Text.__init__`
(`veusz_mathjax.py:1198`, `_text_init` at `1283-1310`, patch applied at `1310`), so they exist on
**every text-bearing widget** (label, axis label, tick labels, key, contour label, …) and are saved in
`.vsz` files like any other setting (`README.md:409-410`). Each is guarded by `if <name> not in self`
(`1285`, `1291`, `1298`, `1307`), i.e. idempotent if another copy already added it.

| setting | Python class / type | default | `usertext` (label) | hidden | `descr` (tooltip) | declaration |
|---|---|---|---|---|---|---|
| `mathjax` | `_MathJaxSetting(setting.Bool)` — `1274-1281` | `False` | `'MathJax'` | no | `'Render this text with MathJax (the font and the style are next to this box)'` | `1286-1290` |
| `mathjaxDisplay` | `setting.Bool` | `False` | `'Display style'` | **yes** (`hidden=True`) | `'Typeset as a displayed equation: larger fractions, limits above and below the operator.  Off typesets it inline, the way text in a paragraph looks.'` | `1292-1297` |
| `mathjaxFont` | `setting.Str` | `''` | `'Font'` | **yes** (`hidden=True`) | `'Which MathJax font to use for this text (see the font list in the MathJax row)'` | `1299-1303` |
| `useTeX` | `setting.SettingBackwardCompat('useTeX', 'mathjax', False)` | `False` (forwards to `mathjax`) | none passed | no `hidden=` argument is passed — **ambiguous**: `README.md:408` calls it "A hidden `useTeX`" | none | `1307-1308` |

The composite control is **not** a setting but the `makeControl` of the `mathjax` setting:

* `_MathJaxSetting.makeControl` returns `_MathJaxRow` (`1277-1279`), and on **any** exception returns
  `controls.Bool(self, *args)` — comment `# never lose the checkbox` (`1280-1281`).
* `_MathJaxRow` (`1200-1272`) is one `QHBoxLayout` (`1217-1220`) holding:
  * a `QCheckBox` with **no text** (the row label comes from `usertext`), tooltip
    `'Render this text with MathJax'` (`1222-1226`), initial state `bool(setting.val)` (`1224`);
  * a `QComboBox` filled from the discovered font list with `f.get('title') or f['id']` as the label and
    `f['id']` as the data (`1228-1230`), initial index `findData(self.font_setting.val or default_font)`
    clamped with `max(idx, 0)` (`1231-1232`); **when the package carries fewer than two fonts the combo
    is disabled and given the tooltip `'this package ships one font'`** (`1233-1235`); stretch 1 (`1237`);
  * a `QCheckBox('Display style')` whose tooltip is `'Typeset as a displayed equation: larger
    fractions, limits above and below.  Off typesets it inline.'` (`1239-1245`).
* The row drives **three** settings through one signal: `sigSettingChanged = pyqtSignal(QObject,
  object, object)` (`1207`), emitted by whichever of the three controls moved (`1250-1262`) — so each
  change goes through Veusz's normal command/undo path (`1202-1205`).
* It follows external changes: each of the three settings gets `setOnModified(self._sync)`
  (`1247-1248`), and `_sync` (`1263-1272`) writes all three widgets back under an `ignore` flag that
  suppresses re-emission (`1250-1252`, `1266`, `1272`) to avoid a signal loop.

README's account of the same: three settings, the row, and the hidden forwarder (`README.md:404-417`);
the visible row's shape is `MathJax: [x] [font v] [ ] Display style` (`README.md:103-105`).

---

## 2. Rendering behaviours

### 2.1 Which text is MathJax (per text element, not per widget)

* `_wrap_draw` wraps `draw` of every registered widget class (`1329-1362`);
  `thefactory.listWidgetClasses()` supplies them (`1360-1362`); classes are marked
  `draw._mathjax_plugin = True` and skipped if already wrapped (`1330-1331`); names are collected for
  the startup report (`1358`, `1410`).
* Inside a draw, the widget's own settings are published in two `threading.local()` slots
  (`1059-1060`): `_current_text_settings` and `_last_font_owner`. `draw` prefers `settings.Text` when it
  wants MathJax, else falls back to `_find_mathjax_settings(settings)` (`1334-1345`); it saves and
  restores the previous values (`1346-1354`), and clears `_last_font_owner` so fonts seen belong to this
  draw only (`1349`, comment).
* `_makeQFont` wraps `collections.Text.makeQFont` (`1317-1324`): after the original builds the font it
  records `(self, font)` in `_last_font_owner` (`1321`). This is the **precise** answer to "which text
  element is this" — an axis's label switch must not drag its tick numbers along (`1312-1316`,
  `1063-1074`).
* `_font_owner(font)` returns the recorded group only if the recorded QFont compares equal to the font
  the renderer was handed (`1076-1085`).
* `_settings_for_text(font)` (`1123-1134`): the font's owner if known, otherwise the widget-level
  `_current_text_settings` record.
* `_find_mathjax_settings` (`1141-1169`) is the fallback for text painted without a preceding
  `makeQFont`: it walks the nested settings groups (`setdict`) looking for one that wants MathJax, with
  guard `depth > 4` (`1151`) and `_is_settings` = `hasattr(obj, '__dict__') and 'setdict' in
  obj.__dict__` (`1137-1138`). Comment: axes keep theirs under `settings.ticklabels` /
  `settings.label`, keys under `settings.Text` (`1144-1149`).
* `_wants_mathjax(settings)` (`1088-1104`) returns True if **either** `mathjax` **or** `useTeX` is truthy
  (`1098-1103`), each read under `try/except` — this is both the 0.2.x compatibility path and the name a
  TeX-enabled Veusz uses (`1091-1094`).
* `_display_style(settings)` = `bool(settings.mathjaxDisplay)`, exception ⇒ `False` (`1107-1112`).
* `_font_for_text(settings)` = `settings.mathjaxFont or None` (`1115-1120`).

### 2.2 The renderer hook, and what it declines

`_renderer(painter, font, x, y, text, alignhorz=-1, alignvert=-1, angle=0, usefullheight=False,
doc=None, **kwargs)` (`1367-1385`): if `_wants_mathjax(settings)` **and** `text` is non-empty **and**
`not text.lstrip().startswith('<')` (`1371-1372`) it builds `renderer_class(...)` with
`display=_display_style(settings)` and `mathjax_font=_font_for_text(settings)` (`1374-1379`). Any
exception from that construction is swallowed (`except Exception as e: pass`, `1380-1381`, comment
`# fall through to normal text rendering`) and the original renderer runs. The `<`-prefix exemption is
how HTML/markup text is left alone.

Both names widgets can resolve are patched: `veusz.utils.textrender.Renderer` (`1387`) and
`veusz.utils.Renderer` (`1390-1391`, comment `# widgets call utils.Renderer, so patch the name they
resolve`).

### 2.3 Renderer class and its geometry

`build_renderer_class(textrender, qt, hosts)` (`597`) returns `_MathJaxRenderer(textrender._Renderer)`
(`945`).

* `__init__(*args, display=False, mathjax_font=None, **kwargs)` sets `self.display` / `self.font_id`
  **before** `super().__init__` because that calls `_initText()` (`948-958`). Long comment `951-955`
  records why the parameter is `mathjax_font`, not `font`: `_Renderer.__init__`'s second positional
  parameter is already `font` (the QFont), so a colliding keyword made every call raise — and the
  failure was invisible because `_renderer` catches exceptions and falls back to plain text.
* `_initText` resets `error=''`, `svgbytes=None`, `renderer=None`, `color=None`,
  `w=h=ascent=1.0`, `painter_size=1.0`, then calls `measure()` (`960-968`).
* `_pixperpt()` (`971-982`): `painter.pixperpt` if truthy, else `painter.dpi/72`, else
  `device().logicalDpiY()/72`, else `96.0/72.0`.
* `_text_size_pt()` (`984-988`): `pointSizeF()`; if None or ≤0 use `pixelSize()/pixperpt`;
  `max(size, 1.0)`.
* `measure()` (`990-1022`):
  * size from `_text_size_pt()`;
  * colour = `painter.pen().color().name()` **unless the pen style is `NoPen`**, in which case
    `color = None` (`993-999`);
  * under `_HOST_LOCK` (`1001`): `hosts.host_for(self.font_id)` (`1002`) and
    `host.render(self.text, size, color, self.display, self.font_id,
    text_font=text_font_key(self.font), measure_text=lambda runs: measure_text_runs(runs, self.font))`
    (`1003-1006`);
  * `if not svg: raise RuntimeError('empty SVG')` (`1007-1008`);
  * `svg, left_as_text = svg_text_as_paths(svg, self.font.family(), em_in_user_units(svg, w_pt, size))`
    (`1009-1010`);
  * if anything was left as text, `svg = cancel_qt_text_factor(svg, self.painter)` (`1011-1012`);
  * `scale = self._pixperpt()`; stores `svgbytes`, `color`, `painter_size = size`
    (`1013-1016`);
  * `self.w = max(w_pt*scale, 1.0)`, `self.h = max(h_pt*scale, 1.0)`
    (`1017-1018`);
  * `descent = max(-base_pt*scale, 0.0)`, `self.ascent = max(self.h - descent, 1.0)` (`1019-1020`);
  * builds `QSvgRenderer(qt.QByteArray(svg))` with a **direct** `from PyQt6.QtSvg import QSvgRenderer`
    (`1021-1022`).
* `_getWidthHeight()` returns `(self.w, self.ascent, 0.0)` — comment: ascent as the total height makes
  the framework's `(xi, yi)` the **baseline**, which is what MathJax reports through
  `vertical-align` (`1025-1028`).
* `render()` (`1030-1050`): `getBounds()` if `calcbounds is None`; `painter.save()`;
  **if the renderer is missing or invalid** it sets a default `QFont`, a red pen, and draws
  `'TeX error: %s' % (self.error or 'cannot render')` into a `QRectF(self.xi, self.yi, 300, 200)`
  top-left aligned, restores and returns (`1035-1044`); otherwise
  `translate(xi, yi)`, `rotate(self.angle)`, `translate(0.0, -self.ascent)`,
  `renderer.render(painter, QRectF(0,0,self.w,self.h))`, `restore()` (`1045-1050`).

### 2.4 Display style

`_display_style` (`1107-1112`) → renderer keyword `display` (`1378`) → `JsHost._render(..., display)`
(`534`) → `self._call('render' if display else 'renderInline', tex, size=text_size,
display=int(display), color=color)` (`573-575` and `581-583`). In the bundle, `render` is
`display: true` and `renderInline` is `display: false`
(`tools/build_bundle.py:688-704`). Default is inline (`README.md:135-142`; measured 21.6 pt inline vs
36.5 pt display for `\frac{a}{b}` at 20 pt).

### 2.5 Font choice and x-height switching

* `JsHost.set_font(font)` (`511-526`): `font = font if font in self._x_height else self.default_font`
  (`518`); return early if it equals the current font (`519-520`); call the bundle's `setFont` only
  `if self.font is None or font:` (`521-522`) — so the **initial** call with an empty default id is
  skipped and the bundle keeps its own default; then
  `if xh: self.lib.mathjax_set_ex_height(float(xh))` (`523-525`); record `self.font = font` (`526`).
  Docstring: `1ex = size * x_height` decides how MathJax's geometry becomes points, so leaving the
  previous font's value would render the new one up to 19% off (measured tex 0.442, dejavu 0.519,
  fira 0.527) (`513-517`).
* `JsHost._render` resolves the target before anything else:
  `target = font if font in self._x_height else self.default_font`; if it differs from `self.font`,
  `self.set_font(target)` (`543-549`). Comment `543-546`: an empty setting means "the default" and must
  switch **back** to the default rather than keep the last font used — otherwise a freshly ticked label
  showed one font while the chooser showed another.
* The ex-height is re-applied on **every** render, not only on a font change, because ex-height belongs
  to the DLL and not to an individual host (`555-559`).
* The host is chosen by font id through `_HostSet.host_for` (`354-376`); a host whose declared list does
  not contain a run-time registered font is taught it by `remember_font` (`499-509`, called at `369`
  and `375`). Docstring `500-506`: without it the plugin took a registered id for unknown and silently
  drew the core's glyphs.

### 2.6 Colour

Colour comes from the painter's pen (`993-999`), is passed to the bridge as a
`c_char_p` (`469-483`), and the bridge's `postprocess=1` rewrites the SVG's ex units to points **and
applies the colour** (`476-477`, comment). It is part of the render cache key (`550-551`), and the
renderer stores it (`1015`). `README.md:149-150`: the formula takes its colour from the current pen;
changing colour, size or either font re-renders it.

### 2.7 `\text{...}` in the element's own Font — the two-pass protocol, end to end

Capability probe, once per host, at construction:

1. `JsHost.__init__` (`391-443`) loads the engine first (`_load_quickjs`, `398`, `201-210`), loads the
   bridge with `ctypes.CDLL` (`399-407`), binds `js_host_init/shutdown/render`, binds
   `js_host_eval` only if the symbol exists (`422-425`), binds `mathjax_set_ex_height` and
   `mathjax_free` (`426-429`), calls `js_host_init` and raises if the handle is `<= 0` (`430-433`),
   then `self.set_font(self.default_font)` (`435`).
2. It calls `veuszTextVersion` with `postprocess=False` and sets
   `self.text_font_api = (version == b'1')` (`436-440`); a `RuntimeError` means `False` (`439-440`).
3. If not available: `sys.stderr.write('veusz-mathjax: old bundle; rebuild mathjax_bundle.js to use the
   Veusz Font for formula text.\n')` (`441-443`), and every render then takes the single-pass path.

Per render (`JsHost._render`, `534-590`) when `self.text_font_api and measure_text is not None`
(`560`):

1. **Pass 1 — compile.** `request = json.dumps({'tex': tex, 'display': bool(display)})` (`561`) and
   `runs, _ = self._call('prepareVeuszText', request, postprocess=False)` (`562`).
   In the bundle (`src/mathjax_text.js:67-88`): `pending = null`; parse the request; get the document for
   the current font; build `new doc.options.MathItem(request.tex, doc.inputJax[0], request.display)`
   with `item.start.node = adaptor.body(doc.document)`; `item.setMetrics(16, 8, 1e7, 1)`;
   `doc.clearPromises()`; `item.convert(doc, STATE.COMPILED)`; then `item.root.walkTree(...)` collects
   every `mtext` node's direct `text` children through `runSpec` (`src/mathjax_text.js:8-24`), whose key
   is `JSON.stringify([text, variant, bold, italic])` — `variant` from `node.parent.attributes
   .get('mathvariant') || 'normal'`, and bold/italic resolved from accumulated CSS
   `font-weight`/`font-style` on ancestors **before** outlining (comment `src/mathjax_text.js:11-19`),
   falling back to `variant.includes('bold'/'italic')` when no CSS is present. It stores
   `pending = {doc, item}` and returns `JSON.stringify([...runs.values()])` — one entry per distinct run.
2. **Python measures.** `measured = measure_text(json.loads(runs))` (`564`), i.e.
   `measure_text_runs(requests, label_font)` (`680-768`):
   * per request: `text`, `variant`; `bold = request.get('bold', 'bold' in variant)`,
     `italic = request.get('italic', 'italic' in variant)` (`690-692`);
   * cache key `(text_font_key(label_font), text, variant, bold, italic)` (`693`);
   * `text_font_key(font)` (`672-678`) is `(font.toString(), styleName(), kerning(), stretch(),
     letterSpacingType().value, letterSpacing(), wordSpacing(), capitalization().value, underline(),
     strikeOut(), overline())` — "paint-relevant QFont properties omitted by `toString()`";
   * a copy of the label font is measured at `_OUTLINE_PX = 1000.0` px (`640`, `697-698`): the outline
     geometry therefore does not depend on the requested size;
   * **only** `AbsoluteSpacing` letter spacing and word spacing are scaled by
     `1000/original_px` (`699-704`);
   * `font.setStyleName('')` when bold or italic, because "a named Regular face can otherwise override
     setBold/Italic" (`707-709`), then `setBold`/`setItalic` (`710-713`);
   * `setStyleStrategy(PreferOutline)` (`714`) and `setHintingPreference(PreferNoHinting)` (`715`);
   * a `QTextLayout` with one line whose width is set to `1e9` (`716-721`);
   * `advance = line.horizontalAdvance()`, `baseline = line.ascent()` (`725-726`);
   * **per glyph run** (so Qt's system font fallback, kerning, ligatures, RTL and CJK all survive):
     `raw = glyph_run.rawFont()`, and for every glyph `raw.pathForGlyph(glyph)` transformed by
     `QTransform.fromTranslate(pos.x(), pos.y() - baseline)` and added to one `QPainterPath`
     (`729-736`);
   * decorations are not in the glyph outlines, so underline / strike-out / overline rectangles are
     added explicitly from `QFontMetricsF`, `thickness = max(metrics.lineWidth(), 1.0)`
     (`737-745`);
   * the run is `{'w': advance/1000, 'h': max(0, -box.top())/1000, 'd': max(0, box.bottom())/1000,
     'path': _svg_path_data(path)}` (`758-763`) — advance (not ink width) is the width, so spaces and
     italic bearings are kept without shifting the origin (`756-757`);
   * returns a dict keyed by `request['key']` (`767-768`).
3. **Pass 2 — typeset.** `svg, (w, h, b) = self._call('renderVeuszText', json.dumps(measured),
   size=text_size, display=int(display), color=color)` (`577-579`).
   In the bundle (`src/mathjax_text.js:92-101`): throw `'No prepared Veusz text'` if nothing is pending;
   consume `pending`; `item.outputData.veuszText = JSON.parse(payload)`; `item.convert(doc)` —
   **resuming the compiled item, not a second `convert(latex)`**, with the stated reason "TeX macros and
   counters must be evaluated exactly once" (`src/mathjax_text.js:97-99`); then
   `extractSvg(item.typesetRoot)`.
   In the wrapper (`src/mathjax_text.js:26-53`): `VeuszTextNode.textRun()` looks the run up by
   `runSpec` key in `outputData.veuszText`, returns `null` when the node's parent is not `mtext` (or no
   runs at all) so ordinary `<text>` output is unchanged (`28-35`), and **throws**
   `'Missing Veusz text metrics: ' + spec.text` when the key is absent (`31-33`).
   `computeBBox` takes `w/h/d` from the run (`36-42`); `toSVG` emits a `<path>` with
   `data-veusz-text="<text>"`, `transform="scale(1,-1)"` (Qt paths are y-down, the MathJax group is
   y-up) and `d = run.path` (`43-53`), so parent wrappers keep script scaling, colour and placement.
4. `build_renderer_class`'s closure supplies both halves on every call:
   `text_font=text_font_key(self.font)` and
   `measure_text=lambda runs: measure_text_runs(runs, self.font)` (`1005-1006`).

The single-pass path (used when `text_font_api` is false or no `measure_text` was given) is
`self._call('render' if display else 'renderInline', tex, size=text_size, display=int(display),
color=color)` (`580-583`).

README's description of the same protocol, including that it needs **both** the updated Python and a
rebuilt `mathjax_bundle.js` (bridge unchanged) and that an older bundle keeps the old behaviour with a
warning: `README.md:156-183`, `438-454`.

Tests asserting this protocol: `test/test_text_font.py:95-266`
(Latin run uses the Veusz font and the cache is isolated `95-103`; width equals
`QFontMetricsF.horizontalAdvance/1000*20` `105-112`; maths unchanged by the text font `114-121`;
the run is identical under every math font `123-135`; bold/italic/element style `137-147`; following
maths uses the measured width `149-158`; spaces kept `160-167`; script scaling applied once
(`0.707`, `0.5`) `169-176`; nested `\text` + maths `178-185`; a `\newcommand` expanding to `\text`
`187-189`; Unicode escaping, fallback and dpi invariance `191-200`; CSS emphasis measured before
outlining `202-209`; emphasis overrides a named Regular face `211-215`; underline and size in the cache
`217-225`; italic advance does not shift the origin `227-235`; maths matches the legacy render API
`237-245`; raster-font fallback `247-259`; text inside fractions/subscripts `261-266`).

### 2.8 The text-outline-unavailable fallback (raster fonts)

* Trigger inside `measure_text_runs`: `if path.isEmpty() and text.strip():` (`751`) — comment
  `# A run that is all whitespace legitimately has no outline` (`750`). It calls
  `_warn_unoutlinable(label_font.family(), text)` and raises
  `TextOutlineUnavailable('no glyph outlines for %r in %r')` (`752-755`).
* `_warn_unoutlinable` (`80-99`): key is `font_family or '(default)'`; returns immediately if already
  warned (`_warned_fonts`, `77`, `82-85`); otherwise writes to `sys.stderr` and appends a timestamped
  line to `veusz_mathjax.log` next to the plugin file, with the whole log write inside `try/except`
  (`91-99`). The message names the Font, the first 40 characters of the text, that it "could not be
  drawn in it", that the formula text "falls back to the MathJax font", and advises choosing an outline
  font such as Arial or Times New Roman (`86-90`).
* `_render` catches `TextOutlineUnavailable` around `measure_text` (`565`): it best-effort calls
  `discardVeuszText` (its own failure is swallowed, `569-572`), then renders the old way —
  `render`/`renderInline` with no text metrics (`573-575`) — so the formula's text is drawn with the
  math font instead of coming out blank (comment `566-568`).
* `TextOutlineUnavailable`'s docstring names the fonts this is for (Windows raster-only: MS Sans Serif,
  System, Fixedsys) and says `QPainterPath.addText` / `QRawFont.pathForGlyph` return nothing for them
  (`66-74`). `README.md:518-524` documents the same, adding that the reason is written **once** to
  `veusz_mathjax.log`. Asserted by `test/test_text_font.py:247-259`.

### 2.9 `<text>` → `<path>` conversion (characters the math font lacks)

Motivation, measured, in the comment block `600-627`: MathJax emits `<text>` for characters its math
font lacks (CJK, a rare symbol, an emoji); a `<text>` does not survive Veusz's record-and-replay
painting — Qt sizes SVG text against the paint device's resolution, not the SVG's own units
(measured ×1.33 at 96 dpi, ×4.17 in a 300 dpi export, against ×1.00 for paths), and it replays as a
hairline outline instead of a filled glyph. The measured table (`615-621`):
`<text>` straight 216×68 FILLED; `<text>` via recording 478×243 FILLED; `<path>` straight 216×68;
`<path>` via recording 216×68. The conversion is faithful to 1 pixel in 5393 (IoU 1.000) (`622-623`).

Implementation:

* Regexes: `_TEXT_ELEM_RE = rb'<text\b([^>]*)>(.*?)</text>'` with `re.S` (`628`),
  `_ATTR_RE = rb'([A-Za-z-]+)\s*=\s*"([^"]*)"'` (`629`),
  `_TEXT_SIZE_RE = rb'font-size="([0-9.]+)px"'` (`630`).
* `_GENERIC_FAMILIES` (`634-637`): `'' , serif, sans-serif, monospace, cursive, fantasy, system-ui,
  ui-serif, ui-sans-serif, ui-monospace, ui-rounded, math, emoji, fangsong` — "families that name a
  *kind* of font rather than one".
* `_OUTLINE_PX = 1000.0` (`640`), and a module-level `_converted = {}` cache (`641`).
* `_svg_path_data(path)` (`643-668`): walks `elementCount()`; `MoveToElement` closes the previous
  contour with `'Z'` then `M%.2f %.2f`; `LineToElement` → `L`; `CurveToElement` → reads the next two
  elements into one `C%.2f %.2f %.2f %.2f %.2f %.2f` and advances `i` by 2; appends a final `'Z'`
  (`667`).
* `_text_to_path_data(text, attrs, label_family, em_units=None)` (`770-842`):
  * family = first entry of `font-family`, stripped of whitespace and quotes (`780-781`);
  * if that family is in `_GENERIC_FAMILIES` **and** a `label_family` exists, it is replaced by the
    label font (`788-789`) — comment: "so a formula's CJK matches the text around it (and so the Font
    setting in the formatting panel means something for it). An explicit family from the engine still
    wins"; the Font-row selection is therefore what a fallback character is drawn in;
  * `font-size` stripped of `px`, `ValueError` ⇒ `None` (`790-795`);
  * if `em_units` is None or ≤0 it falls back to the engine's own `size` (`796-799`); `size <= 0`,
    `em_units <= 0` or empty text ⇒ `None` (`800-801`);
  * builds `qt.QFont(family)` (or a default `QFont()` when the family is empty) at 1000 px
    (`803-804`);
  * `font-weight` `bold`/`bolder` or a numeric ≥600 ⇒ bold; `font-style` `italic`/`oblique` ⇒ italic
    (`805-809`);
  * `PreferOutline | ForceOutline` (`810-813`) and `PreferNoHinting` when available (`814-816`) —
    comment: "outlines, not hinted bitmaps: this is geometry";
  * **gives up on any character the chosen family lacks**: `if any(not
    metrics.inFontUcs4(ord(char)) for char in text): return None` (`822-827`) — comment: `addText` does
    not fall back and "would draw a box", so those are left to Qt's own SVG text route "and the size
    correction below still applies to them"; the whole check is in `try/except` that ignores failures;
  * `path.addText(QPointF(0.0, 0.0), font, text)` (`829-830`), then
    `scale = em_units / _OUTLINE_PX` applied by `QTransform.fromScale(...).map(path)` (`831-832`) —
    i.e. those characters are drawn at **one em**, not MathJax's `2*ex`, which measured 17% larger
    under Fira (ex/em 0.527) than Termes (0.441) and 12% smaller than a plain label (`770-779`);
  * `x`/`y` attributes are parsed (`ValueError` ⇒ 0) and applied as a translation only if non-zero
    (`833-839`);
  * empty path ⇒ `None` (`840-841`); otherwise `_svg_path_data(path).encode('utf-8')` (`842`).
* `svg_text_as_paths(svg, label_family='', em_units=None)` (`844-884`):
  * cache key `(svg, label_family, em_units)` (`853`); if there is no `b'<text'` in the SVG it stores
    `(svg, False)` and returns immediately (`877-879`);
  * otherwise substitutes every `<text>` element, decoding attributes with `_ATTR_RE` and
    `html.unescape`-ing the body (`859-863`); any exception inside one element's conversion is caught
    and treated as "not converted" (`864-867`); an unconverted element is returned **unchanged** and
    sets `leftover = True` (`868-870`);
  * the element's own `transform` attribute is preserved on the replacement
    (`<path transform="..." d="..."/>`, `871-875`) — the transform must be kept "or the glyphs come out
    flipped": MathJax wraps the formula in `scale(1,-1)` and each text element in `scale(1,-1)` again,
    so text-local coordinates (y down, baseline at origin) land upright only through the second flip
    (`624-627`);
  * eviction >128 clears the cache (`881-882`).
* `em_in_user_units(svg, w_pt, size_pt)` (`886-904`): finds `viewBox`, parses its **third** field as the
  viewBox width, returns None if the SVG does not say, or `w_pt <= 0`, or `size_pt <= 0`, or the width
  is unparsable/≤0 (`895-903`); result `size_pt * (viewbox_width / w_pt)` (`904`). Comment: MathJax lays
  maths out with 1 em = the requested size, and the plugin paints into a rect of the reported box, so
  box and viewBox give the scale (`890-893`).

### 2.10 Cancelling Qt's dpi scaling on any `<text>` that was left behind

* `qt_text_factor(painter)` (`906-922`): returns `1.0` unless `type(painter.device()).__name__ ==
  'RecordPaintDevice'` (only Veusz's recording device reports the page dpi in that metric) — a `QImage`
  reports 72 "so there is nothing to cancel", and the `QPicture` fallback is not scaled this way at all;
  then `dpi = device.metric(PdmDpiY)`, returning `dpi/72.0` if `dpi > 0`, `1.0` on any exception
  (`914-922`).
* `cancel_qt_text_factor(svg, painter)` (`924-943`): no-op when the factor is within `1e-6` of 1.0 or
  there is no `<text` (`930-931`); otherwise rewrites the `font-size="Npx"` of every opening `<text`
  tag to `value * (1/factor)`, formatting with `%g` (`932-943`).

### 2.11 Anything else that affects the picture

* `_HOST_LOCK = threading.RLock()` (`63`), with the comment: the bridge shares its state table and
  ex-height across hosts, so `compile -> Qt measurement -> typeset` must not interleave with another
  paint (`61-62`). Held around `JsHost.render` (`530-532`) and around the `hosts.host_for` +
  `host.render` in `measure()` (`1001-1006`).
* `JsHost._call` passes `postprocess=int(postprocess)`; for the render path it is `1` with the comment
  "the bridge rewrites the SVG's ex units to points and applies the colour, which is what the renderer
  expects" (`476-483`). The text-protocol calls (`veuszTextVersion`, `prepareVeuszText`,
  `discardVeuszText`) pass `postprocess=False` (`437`, `562`, `570`).
* If the bridge returns nothing, `if not svg: raise RuntimeError('empty SVG')` in both `_render`
  (`584-585`) and `measure` (`1007-1008`).
* README: the formula is placed by the baseline and drawn as paths so vector exports stay vector
  (`README.md:251-257`); the plugin is synchronous, the first paint costs a few ms, then it is cached
  (`README.md:491-493`).

---

## 3. Caching

Four caches, one warning set, plus two per-draw records.

1. **Rendered SVG — `JsHost.svg_cache`** (per host, `394`).
   * Key: `key = (tex, float(text_size), color or '', bool(display), self.font, text_font)` (`550-551`).
     `text_font` is `text_font_key(self.font)` (`1005`), so the whole paint-relevant QFont state is in
     the key — the README states the reason explicitly: "The cache also distinguishes the Veusz text
     font and its style, so switching Font cannot reuse another face's text" (`README.md:150-151`).
   * A hit returns **before** the ex-height restore (`552-554`).
   * Eviction: `if len(self.svg_cache) > 256: self.svg_cache.clear()` **before** inserting the new
     entry (`587-589`) — i.e. the whole cache is dropped when it would exceed 256 entries.
   * Correctness reason recorded in a comment: the font is resolved to a concrete id *before* the cache
     lookup, because an empty setting must switch back to the default rather than keep the last font
     used (`543-549`).
2. **Shaped text runs — `_text_runs`** (closure of `build_renderer_class`, `670`).
   * Key: `(text_font_key(label_font), text, variant, bold, italic)` (`693`).
   * Eviction: `if len(_text_runs) >= 512: _text_runs.clear()` before storing (`764-766`).
   * `text_font_key` exists because `QFont.toString()` omits paint-relevant properties
     (`672-674`), and the outline geometry is size-independent, which is why the key has no size —
     asserted by `test/test_text_font.py:217-225` (a 40 pt run reuses the same `d`).
3. **Converted SVG — `_converted`** (`641`).
   * Key: `(svg, label_family, em_units)` (`853`).
   * Stores `(svg, leftover)`; the no-`<text>` case is cached as `(svg, False)` (`877-879`).
   * Eviction: `if len(_converted) > 128: _converted.clear()` (`881-882`).
4. **JS hosts / font registration — `_HostSet._hosts` and `_HostSet._registered`** (`338-339`).
   * One host per bundle path, created on first use (`348-352`, `371-376`) — "a font nobody selects
     costs nothing: no host, no memory, no parse" (`324-330`).
   * A font **data** file is evaluated into the core host only once per path (`363-370`), and then
     `remember_font` teaches the host the id/x-height (`369`, `499-509`). `README.md:84-87` and
     `230-236` document the consequence: an unused added font costs nothing; deleting the file makes
     documents fall back.
5. `_warned_fonts = set()` (`77`) — one warning per font family key (`82-85`); never bounded/evicted.
6. `_current_text_settings` / `_last_font_owner` (`1059-1060`) are per-thread state, saved and restored
   around every wrapped `draw` (`1346-1354`) and reset at the start of each draw (`1349`).

---

## 4. Font handling

### 4.1 Where the font list comes from

* `_find_bundle(here)` (`150-175`): `VEUSZ_JSENGINES_BUNDLE` if it exists (`158-160`); else
  `data/mathjax_bundle.js`, `mathjax_bundle.js`, `../data/mathjax_bundle.js` (`163-165`); else the first
  `*.js` in `data/`, `here`, `../data` (in that order) whose header declares fonts
  (`168-174`, comment `150-157`).
* `_discover_fonts(here, bundle)` (`272-320`): candidates are the bundle first, then every other `*.js`
  in the bundle's own directory in sorted order (`284-291`, inside `try/except`). For each candidate
  `_declared_fonts(path, sidecar=is_default)` (`297`); a non-default file that declares nothing is
  skipped (`298-299`); the configured default that declares nothing becomes the single placeholder
  `[{'id': '', 'title': 'default', 'x_height': None}]` (`300-302`).
* Entries are de-duplicated **by id** (`seen`, `283`, `306-308`) and each surviving entry is copied with
  two plugin-added fields: `entry['bundle'] = str(path)` and `entry['kind'] = bundle_kind`
  (`309-311`).
* The default id is the bundle's declared default (or its first font) **and only from the configured
  bundle** (`313-314`); if no font matched, the first discovered font becomes the default
  (`318-319`); if nothing was discovered at all, a single placeholder entry with `bundle`/`kind` is
  returned (`315-317`).
* The docstring states the policy: any other `*.js` beside it that declares itself is taken as well,
  each carrying the file it came from, so a font bundle dropped into `data/` shows up after a restart
  with nothing to keep in step; a bundle that declares nothing is only ever loaded as the configured
  default (`272-280`). Same promise in `README.md:215-236`.

### 4.2 What a font entry has

Read from the declared JSON: `id`, `title`, `x_height` (`237`, `247`, `260`, `305`); only entries with a
truthy `id` are kept (`237`, `247`, `260`). Top level: `default` (`239`, `249`, `261`) and `kind`
(`239`, read as `data.get('kind') or 'bundle'`). The plugin adds `bundle` (absolute path) and `kind`
(`310-311`). The builder writes, per font, `id`, `title`, `x_height`, `ranges` in the header and in
`fonts.json` (`tools/build_bundle.py:507-508`, `790-797`, `809-815`); a font-data file's header adds
`'kind': 'data'` (`build_bundle.py:503-509`). `ranges` is the number of dynamic glyph-range files kept
(and `ranges_total` the number before `--trim`, which is only in the builder's local `meta`,
`build_bundle.py:542-552`).

**`ranges` is never read by the plugin** (verified: `grep ranges veusz_mathjax.py` → no matches). It is
self-description for the build's own reporting (`build_bundle.py:1073-1080`) and `--list-fonts`
(`1049-1059`). The top-level `mathjax` version field is likewise not read by the plugin.

### 4.3 How the declarations are read (without executing the file)

`_declared_fonts(bundle, sidecar=True)` (`213-250`): read the first 16384 bytes
(`errors='replace'`, failure ⇒ empty string, `225-229`); `re.search(r'^// MATHJAX-FONT (\{.*\})\s*$',
head, re.M)` (`230`); parse the JSON (`232-235`, failure ⇒ `data=None`); keep only fonts with an id and
return `(fonts, data.get('default'), data.get('kind') or 'bundle')` (`236-239`). If the header is absent
and `sidecar` is true, fall back to `fonts.json` in the same directory, which always means kind
`'bundle'` (`240-249`) — kept "for bundles built before this header existed" (`222-223`).

`JsHost.__init__` re-reads the list **per host** with `_load_fonts(bundle)` (`395`): if
`VEUSZ_JSENGINES_FONTS` exists it overrides everything (`255-263`); otherwise the header/sidecar is
used; if the declared default is not among the ids it becomes the first id (`266-267`); if nothing is
declared the fallback is `([{'id': '', 'title': 'default', 'x_height': None}], '')` (`269`). The host
keeps `self._x_height = {f['id']: f.get('x_height') for f in self.fonts}` (`396`).

### 4.4 Extra fonts: two kinds, loaded lazily

* `kind == 'bundle'`: one `JsHost` per bundle path, created on first use, then `remember_font(id,
  x_height)` (`371-376`).
* `kind == 'data'`: the file carries no MathJax; it is evaluated once into the **core host** (the host
  of the configured bundle, `core_bundle`, `332-352`) through `js_host_eval` (`445-467`), and every font
  that arrives this way shares that one host (`360-370`, comment). Then `remember_font(id, x_height)`
  (`369`). The font file itself reads `globalThis.__veuszMathjax` and calls `registerFont({id, title,
  xHeight, FontClass})` (`tools/build_bundle.py:481-493`, `621-625`), and the build guarantees the
  needed MathJax classes are already in the bundle (`build_bundle.py:571-575`).
* `load_font` raises `RuntimeError('this build of mathjaxbridge cannot add fonts at run time; replace
  it with one that has js_host_eval')` when the symbol is missing (`452-455`), and
  `RuntimeError('cannot load the font %s: %s')` when the bridge reports an error, freeing the error
  string with `mathjax_free` (`456-466`).

### 4.5 How x-height is applied

* `fonts.json` / the header carries `x_height` per font (`build_bundle.py:372-397` extracts it with
  `x_height:\s*([0-9.]+)` from the package's `mjs/common.js`, prefixing a leading `.` with `0`;
  `write_fonts_json` docstring names newcm 0.442, stix2 0.479, dejavu 0.519 and "up to 17% off",
  `801-818`).
* `set_font` hands the current font's value to the bridge with
  `mathjax_set_ex_height(float(xh))`, but **only when it is truthy** (`523-525`) — a `None` x-height
  leaves the bridge's previous value in place; **ambiguous** whether that is intended.
* It is re-applied on every render (`557-559`), reason in the comment: ex-height belongs to the DLL, not
  an individual host, "Restore it even when this host's font did not change since another host was
  drawn" (`555-556`).
* `remember_font` is a no-op for an empty id (`507-508`) and otherwise (re)stores the value
  (`509`).
* Effect of getting it wrong is documented twice: `README.md:142-148` ("0.441 to 0.527 … up to 19% off —
  that is the bug class that made formulas 33% too large in 0.2.0") and `test/smoke_test.py:26-31,
  307-336`, which measures a 20 pt `\mathrm{H}` in points of paper and demands ≈0.683 em
  (New Computer Modern cap height, `smoke_test.py:310-336`).

### 4.6 Unknown / empty font id

* `_HostSet.spec(font_id)` returns `self._by_id.get(font_id) or self._by_id.get(self.default_font)`
  (`341-342`), i.e. unknown ids fall back to the default entry, not merely to nothing.
* `host_for` returns `None` if that too is missing (`356-357`), and for `kind == 'data'` returns `None`
  when there is no core host (`363-365`). `measure()` would then raise `AttributeError` on `None.render`
  — caught by `_renderer`'s `except` (`1380-1381`) so the label is drawn as plain text. (This path is
  not mentioned in any comment; inferred from the code. **Ambiguous**, but it is the only refusal.)
* `JsHost.set_font`: unknown id ⇒ the default font (`518`); an empty id with a normal host is *not*
  passed to `setFont` because of the `self.font is None or font` guard (`521-522`).
* `JsHost._render`: `target = font if font in self._x_height else self.default_font` (`547`); docstring:
  "``font`` is the id of the font in fonts.json to render with; empty or unknown means the bundle's
  default" (`540-541`).
* `_font_for_text` maps `''` to `None` (`1118`).
* The bundle side also guards: `docFor()` falls back to the current font for an unknown name
  (`build_bundle.py:638-639`) and `setFont` ignores a name that is not in `FONT_CLASSES`
  (`build_bundle.py:678-684`).
* README: a document that names a font the package does not carry falls back to that package's font
  (`README.md:90-92`, `234-236`).

---

## 5. Safeguards and error paths

Every refusal, warning, fallback and log message, with its trigger.

### Startup / install

| condition | behaviour | line |
|---|---|---|
| `_plugin_path()` cannot find the plugin path (frame walk fails, no `__file__`) | returns `None`; callers treat that as "no directory" | `111-123` |
| bridge or bundle not found | `install()` raises `RuntimeError('missing %s. Put mathjaxbridge.dll/.so and mathjax_bundle.js in a data/ directory next to the plugin file, or set VEUSZ_JSENGINES_BRIDGE / VEUSZ_JSENGINES_BUNDLE.')` — the `%s` is `'bridge library'` or `'mathjax_bundle.js'` | `1181-1186` |
| `_declared_fonts` cannot open a file / bad JSON / no fonts | returns `None` (silently) | `225-235`, `240-247` |
| `_discover_fonts` glob fails | ignored (`try/except`) | `287-291` |
| `_discover_fonts` path comparison fails | treated as not-default | `293-296` |
| `install()` raises anything, or the module is imported outside Veusz | `_auto_install()` catches, writes `'veusz-mathjax: not installed: %s'` to stderr and **overwrites** `veusz_mathjax.log` with the message plus `traceback.format_exc()`; the log directory falls back to `_plugin_dir()` or `%TEMP%`, and the whole write is best-effort | `1425-1437` |
| `VEUSZ_MATHJAX_DEFER == '1'` | the module does not auto-install at all (the hook the tests use) | `1440-1441`; `test/test_text_font.py:16`, `test/smoke_test.py:96` |
| on success (verbose) | prints `veusz-mathjax: TeX rendering enabled (MathJax bundle)`, then `  plugin: <path> (<n> bytes, modified <time>)`, `  bridge:`, `  bundle:`, `  widgets wired: <n>` | `1405-1410` |
| on success, always | **overwrites** `veusz_mathjax.log` with the install time, plugin copy (path/size/mtime), bridge, bundle and widget count (comment `1393-1395`: Veusz loads plugins once at startup, so editing the file does nothing and that is easy to mistake for a fix that did not work) | `1411-1418` |
| install returns | `{'bridge', 'bundle', 'widgets', 'fonts', 'hosts'}` | `1421-1422` |

### Engine / bridge

| condition | behaviour | line |
|---|---|---|
| QuickJS not found or not loadable | `_load_quickjs` returns `None` (no message of its own; the bridge then fails) | `201-210` |
| bridge `CDLL` fails | `RuntimeError('cannot load %s: %s' + ' -- and no QuickJS engine (%s) was found next to it' when quickjs is None)` | `399-407` |
| `js_host_init` returns `<= 0` | `RuntimeError('cannot load the JavaScript bundle %s')` | `430-433` |
| bundle lacks `veuszTextVersion` (`RuntimeError`) or returns ≠ `b'1'` | `text_font_api = False` and one stderr line: `veusz-mathjax: old bundle; rebuild mathjax_bundle.js to use the Veusz Font for formula text.` | `436-443` |
| bridge has no `js_host_eval` and a font must be added | `RuntimeError('this build of mathjaxbridge cannot add fonts at run time; replace it with one that has js_host_eval')` | `452-455` |
| `js_host_eval` returns non-zero | `RuntimeError('cannot load the font %s: %s')` with the bridge's message, or `rc=<n>`; the error string is freed | `456-466` |
| `js_host_render` returns non-zero | `RuntimeError` with the bridge's message (or `mathjaxbridge failed (rc=%d)`), error string freed | `484-490` |
| bridge produced no bytes | `RuntimeError('empty SVG')` in `_render` (and again in `measure`) | `584-585`, `1007-1008` |
| `set_font` called for the default `''` | `setFont` is **not** called (guard), so the bundle keeps its own default | `521-522` |

### Rendering

| condition | behaviour | line |
|---|---|---|
| text is empty, or `text.lstrip()` starts with `<` | the wrapped renderer is not used at all; the original renderer draws it (HTML/markup is never sent to MathJax) | `1371-1372` |
| the MathJax renderer cannot be constructed (any exception, incl. a missing host, a bad SVG, a bridge error) | exception swallowed and the original `_Renderer` used, so the label shows its source text — comment `# fall through to normal text rendering`. **No log line.** | `1380-1381` |
| `makeControl` cannot build the composite row | falls back to `controls.Bool` — `# never lose the checkbox` | `1277-1281` |
| the package carries fewer than two fonts | combo disabled with tooltip `'this package ships one font'`; the chooser still exists with one entry | `1233-1235`; `README.md:90-93` |
| a settings change arrives from elsewhere | `_sync` follows it under `ignore` so no signal loop | `1247-1248`, `1263-1272` |
| a text run has glyphs but no outlines and is not all whitespace | `_warn_unoutlinable` (once per family: stderr + **append** to `veusz_mathjax.log`, timestamped) then `TextOutlineUnavailable` | `77-99`, `746-755` |
| `TextOutlineUnavailable` during measurement | `discardVeuszText` (best effort, failure swallowed) then the formula is rendered with `render`/`renderInline` so its text uses the math font instead of coming out blank | `565-575` |
| `discardVeuszText` itself fails | swallowed | `569-572` |
| a single `<text>` element cannot be outlined | that element is left as `<text>`, `leftover = True`, and its `font-size` is then corrected for the device dpi | `859-870`, `1011-1012` |
| a character is not present in the chosen family (`inFontUcs4` false) | `_text_to_path_data` returns `None` → left to Qt's SVG text route (which does fall back), size correction still applied | `818-827`, `850-852` |
| `font-size` / `viewBox` / `x` / `y` unparsable | `None` (element left as text) for the first; `em_units` falls back to the engine's size; `x=y=0.0` | `790-799`, `833-837`, `895-903` |
| the painter device is not Veusz's `RecordPaintDevice` | `qt_text_factor` returns 1.0, i.e. no correction (a QImage reports 72, the QPicture fallback is not scaled) | `908-922` |
| `QSvgRenderer` invalid at paint time | a red, top-left `'TeX error: %s'` (or `'cannot render'`) is drawn in a 300×200 rect instead of the formula | `1035-1044` |
| painter has no pen colour (`NoPen`) | no colour is sent, so the bridge does not apply one | `993-999` |

### Defensive/quiet paths (all `except: pass`/`return None`)

`_plugin_path` frame walk and `__file__` (`111-123`); `_warn_unoutlinable`'s log append (`98-99`);
`_font_owner`'s QFont comparison (`1082-1084`); `_wants_mathjax`'s per-name attribute read
(`1099-1103`); `_display_style` (`1108-1112`); `_font_for_text` (`1117-1120`);
`_find_mathjax_settings`'s `setdict` access and per-name `getattr` (`1153-1161`);
`_pixperpt` (`979-982`); `_text_to_path_data`'s `inFontUcs4` check (`822-827`);
`svg_text_as_paths`'s per-element conversion (`864-867`); `install`'s log write (`1419-1420`);
`_auto_install`'s traceback write (`1436-1437`); the plugin **never** writes into the Veusz
installation (`README.md:34-35`, `489-490`).

### Documented troubleshooting behaviours (`README.md:290-300`)

* No **MathJax** checkbox ⇒ the plugin did not install; read `veusz_mathjax.log` next to the plugin or
  start Veusz from a console.
* Checkbox appears but the label shows the TeX source ⇒ rendering failed and the plugin deliberately
  fell back to plain text; the log names the reason, and a missing `data/qjs.dll` or
  `data/mathjax_bundle.js` shows up at startup.
* Label shows `$x^2$` with dollars ⇒ the user wrapped the formula; drop `$…$`.
* Nothing happens, no log ⇒ the plugin was probably never added in Preferences → Plugins, or Veusz was
  not restarted.
* A fix does not seem to be in effect ⇒ Veusz loads plugins once at startup; restart and check the first
  lines of `veusz_mathjax.log`, which record which copy was loaded with its size and mtime.
* `"qjs.dll was not found"` ⇒ `data/` was separated from `veusz_mathjax.py`, or a copy step skipped
  `qjs.dll`.
* Missing MSVC runtime DLLs ⇒ install the VC++ 2015-2022 x64 redistributable.

---

## 6. Documented-but-not-implemented

Search of `README.md` for `not` / `todo` / `future` / `planned`: **there is no TODO, "planned" or
"future" text anywhere in the README** (verified by grep). The "not implemented" statements are all in
*Limitations* and in a few inline sentences:

1. **MathJax is not LaTeX** — no `\usepackage`, `\includegraphics`, TikZ/pgfplots, counters or
   references, no shell escape; only the listed packages (`README.md:484-486`, list at `196-206`).
2. **It reaches into Veusz internals** (`Text` settings class, `Widget.draw`, `Renderer`, `_Renderer`);
   a major Veusz refactor can break it and an update would then be needed. It does **not** patch
   anything on disk (`README.md:487-490`).
3. **Rendering is synchronous**: the first paint costs a few ms, then it is cached; very large documents
   with hundreds of distinct labels feel that on the first draw (`README.md:491-493`).
4. **Automatic text wrapping is not supported by the outline-based text path**, nor is bitmap-only /
   colour emoji font rendering (`README.md:182-183`); the same limitation is restated for
   `hsize`/`maxwidth` inside `\text` (`README.md:523-524`). No code implements either.
5. **Raster-only fonts cannot be outlined** — the documented workaround is fallback to the MathJax font
   plus one log line (`README.md:518-524`).
6. **Not a `$…$` mixed-label mode**: the whole label is TeX; the "two-stage call" is internal
   (`README.md:180-181`).
7. **An older bundle keeps the old text-font behaviour and emits a warning** — the new behaviour needs
   both the updated Python *and* a rebuilt `mathjax_bundle.js`; the bridge DLL is unchanged
   (`README.md:177-181`).
8. **Prebuilt binaries are Windows x64 only**; other platforms need the bridge and QuickJS built
   (`README.md:19-27`, `368-384`, `525`). "Other versions have not been tried, so they may work, or they
   may not" (`README.md:31-32`).
9. **`--flavor fonts` is not part of every release build**; an existing file is kept while the MathJax
   version it embeds is current, `--rebuild-fonts` forces it (`README.md:339-342`).
10. **Fixed behaviour that is asserted, not merely documented**: `test/smoke_test.py` checks (1) the
    engine renders, (2) the plugin replaces `utils.Renderer` *by identity*, (3) TeX label, TeX axis label
    and an ordinary label all draw ink, (4) 20 pt is 20 pt of paper at any dpi (cap height 0.683 em,
    ±6%), (5) `useTeX` still renders identically to `mathjax`, (6) Display style is taller, (7) an unset
    font means the package default, (8) a missing glyph keeps its size and stays inside its box at
    96 vs 300 dpi (8% tolerance), (9) those characters are painted as `<path>`, (10) they follow the
    element's Font setting, (11) their size does not depend on the math font and matches a plain label
    (`test/smoke_test.py:79-598`).

Code comments that record what is *not* covered, inside the implementation:

* `_text_to_path_data` deliberately leaves a character the chosen family lacks to Qt's SVG text route,
  because `addText` would draw a box (`veusz_mathjax.py:818-827`).
* `em_in_user_units` returns `None` when the SVG does not state a viewBox, and then the engine's own
  font size is used (`886-893`).
* `qt_text_factor` corrects only Veusz's `RecordPaintDevice`; a `QImage` and the `QPicture` fallback are
  left alone (`906-913`).
* `measure_text_runs` preserves only underline / strike-out / overline as decorations — QFont's other
  line decorations are not handled, and text wrapping is not attempted (`737-745`).
* `_GENERIC_FAMILIES` is a fixed list; any other family MathJax writes is treated as explicit
  (`634-637`, `783-789`).

---

## 7. Gap checklist against `veusz-js-engine/features/mathjax/feature.js`

What the 194-line `feature.js` **already has** (so these are *not* gaps):

* `[HAS]` feature declaration `veusz.feature({name:'mathjax', title:'MathJax formulas', target:'text',
  version:'0.3.0'})` (`feature.js:17`, `36-37`).
* `[HAS]` the three properties with the **document names preserved**: `on`→`mathjax` (switch, default
  false, descr), `font`→`mathjaxFont` (choice, default `FONTS.default`, choices from `FONTS.fonts`),
  `display`→`mathjaxDisplay` (switch, default false, descr) (`feature.js:46-60`).
* `[HAS]` decline when the switch is off, and when the text is empty (`feature.js:162-169`).
* `[HAS]` display vs inline: `globalThis.render` when display, `renderInline` otherwise
  (`feature.js:174-180`).
* `[HAS]` the ex→pt conversion of the root `<svg>` `width`, `height` and `vertical-align`, at three
  decimals with a `pt` suffix, using the font's own `x_height` from the font list
  (`feature.js:19-26`, `103-158`), and the resulting box `{width, height, depth = -baseline}`
  (`feature.js:134-157`, `189-192`).
* `[HAS]` the per-font x-height list, read from `globalThis.veuszFonts`
  (`feature.js:16`, `19-26`; `fonts.js:13-20`).
* `[HAS]` declining (via null/veusz.error) rather than throwing (`feature.js:164`, `167`, `178`,
  `182-186`).
* `[HAS]` a size fallback of 20 pt when the request has none (`feature.js:171`).
* `[COVERED BY PLATFORM]` colour: `feature.js` ignores it, but the platform rewrites
  `fill`/`stroke` of `currentColor` and `#000000` to the painter's pen colour and drops zero-width
  strokes (`veusz_js_engine.py:1317-1320`, `1417-1442`) — the original did this in the bridge
  (`veusz_mathjax.py:476-477`).
* `[COVERED BY PLATFORM]` rotation, baseline placement, w/h clamps ≥1.0, the red "cannot draw" text and
  the exception fall-through to ordinary text (`veusz_js_engine.py:1242`, `1251-1255`, `1331-1332`,
  `1344-1357`); the original had these in `_MathJaxRenderer` (`veusz_mathjax.py:1017-1020`,
  `1035-1044`, `1046-1050`) — with the difference that the platform draws its message as
  `'cannot draw: …'` where the original drew `'TeX error: …'`.
* `[COVERED BY PLATFORM]` which text element a font belongs to (`makeQFont` owner tracking /
  nested-group fallback) — `veusz_js_engine.py` owns font-owner tracking per the feature README
  (`features/mathjax/README.md:8-10`); the original's `_makeQFont` / `_font_owner` /
  `_find_mathjax_settings` (`veusz_mathjax.py:1063-1169`, `1317-1362`) are the reference behaviour.
* `[COVERED BY PLATFORM]` install failure / "not installed" reporting and the log next to the plugin
  (`features/mathjax/README.md:57-61`); the original's `_auto_install` (`veusz_mathjax.py:1425-1441`).

Flat work items that were **missing or partial** in `feature.js` when this
audit was written — kept as written, because they are the evidence for the
port; the state of each one *now* is the table at the end of this section:

1. `[MISSING]` Add the hidden `useTeX` backward-compat setting that forwards to `mathjax`, so documents
   written before 0.3.0 keep rendering (`veusz_mathjax.py:1304-1308`; asserted by
   `test/smoke_test.py:338-348`). `feature.js:46-60` declares only the three settings.
2. `[MISSING]` Switch the bundle's math font when the chooser changes (and back to the default for an
   empty/unknown id), instead of using the id only for `x_height`: `feature.js:19-26`, `170`, `189`
   never call `setFont`, while the original resolves the target and calls it
   (`veusz_mathjax.py:543-549`, `511-526`); the engine bundle beside the feature does expose
   `globalThis.setFont` / `currentFont` (`veusz-js-engine/features/mathjax/mathjax.js`, API region) and
   the original's test for it is `test/smoke_test.py:371-412`.
3. `[PARTIAL]` Fall back to the **default font's** `x_height` for an unknown/empty font id;
   `feature.js:19-26` returns the hard-coded `0.442`, and `feature.js:170` uses `FONTS.default` without
   checking that it is a member of `FONTS.fonts` (the original validates: `veusz_mathjax.py:266-267`,
   `318-319`).
4. `[PARTIAL]` Provide the `descr`/tooltip for the font chooser and the equivalent of the checkbox
   tooltip; `feature.js:51-54` declares `font` with a label and choices but no `descr`
   (the original: `veusz_mathjax.py:1223`, `1233-1235`, `1240-1242`).
5. `[MISSING]` Disable the chooser and explain `'this package ships one font'` when the package carries
   fewer than two fonts (`veusz_mathjax.py:1233-1235`); `feature.js:28-32` always builds a chooser of
   whatever length.
6. `[MISSING]` Follow changes made to the three settings from elsewhere in Veusz, with a re-entrancy
   guard (`veusz_mathjax.py:1247-1248`, `1250-1252`, `1263-1272`); no equivalent in `feature.js`.
7. `[MISSING]` Draw `\text{...}` (and every `mtext`) in the element's own **Font** via the two-pass
   protocol: call `prepareVeuszText`, hand the runs to Qt for shaping/outlining, then call
   `renderVeuszText` on the same compiled item (`veusz_mathjax.py:560-579`, `680-768`;
   `src/mathjax_text.js:67-101`) — `feature.js` calls only `render`/`renderInline`
   (`feature.js:174-180`), so formula text comes out in the math font. (Confirmed by the feature's own
   README: `features/mathjax/README.md:114-117`.)
8. `[MISSING]` Probe `veuszTextVersion` once and warn (stderr, `'old bundle; rebuild
   mathjax_bundle.js to use the Veusz Font for formula text.'`) when the bundle lacks the text API
   (`veusz_mathjax.py:436-443`); `feature.js:176-179` only checks `typeof render === 'function'`.
9. `[MISSING]` Qt measurement of each run: `QTextLayout` at 1000 px, per-glyph-run fallback face,
   kerning/ligatures/RTL/CJK via `glyphRuns()`, `pathForGlyph` with `pos.y - baseline`, advance from
   `line.horizontalAdvance()` rather than ink width, and underline/strike-out/overline rectangles
   (`veusz_mathjax.py:680-768`).
10. `[MISSING]` Resolve `\textbf`/`\textit`/element Bold-Italic/CSS `font-weight`/`font-style` **before**
    outlining, and clear a named Regular `styleName` so emphasis is not overridden
    (`veusz_mathjax.py:691-692`, `705-713`; `src/mathjax_text.js:11-22`).
11. `[MISSING]` Scale `AbsoluteSpacing` letter spacing and word spacing with the outline pixel size so a
    run's advance is not distorted (`veusz_mathjax.py:699-704`).
12. `[MISSING]` The raster-font fallback: detect `path.isEmpty() && text.strip()`, warn once per family
    (stderr + append to the plugin log, with the "choose an outline font (Arial, Times New Roman, …)"
    advice), and re-render the formula with the math font instead of leaving it blank
    (`veusz_mathjax.py:66-99`, `746-755`, `565-575`; `test/test_text_font.py:247-259`).
13. `[MISSING]` Convert every `<text>` element in the SVG to `<path>` outlines, preserving the element's
    own `transform` (`veusz_mathjax.py:600-668`, `844-884`), and emit `data-veusz-text` for the run
    paths (`src/mathjax_text.js:48-52`).
14. `[MISSING]` Substitute the text element's own font family for MathJax's *generic* families
    (`_GENERIC_FAMILIES`: `''`, serif, sans-serif, monospace, cursive, fantasy, system-ui, ui-*,
    math, emoji, fangsong) so a fallback character follows the Font row
    (`veusz_mathjax.py:634-637`, `780-789`; `test/smoke_test.py:492-552`).
15. `[MISSING]` Draw those fallback characters at **one em**, computed from the SVG `viewBox` width and
    the reported box (`em_in_user_units`, `veusz_mathjax.py:886-904`), not at MathJax's `2*ex`
    (`veusz_mathjax.py:770-779`, `831-832`; `test/smoke_test.py:554-597`).
16. `[MISSING]` Refuse to outline a character the chosen family does not contain (`inFontUcs4`), leaving
    it to Qt's own SVG text route so no box is drawn (`veusz_mathjax.py:818-827`).
17. `[MISSING]` Cancel Qt's device-dpi scaling of any `<text>` left in the SVG, only on Veusz's
    `RecordPaintDevice` (`veusz_mathjax.py:906-943`; `test/smoke_test.py:414-451`).
18. `[MISSING]` Cache the rendered SVG, keyed by
    `(tex, size, colour, display, currentFont, QFont-properties-of-the-text-font)`, cleared wholesale
    above 256 entries (`veusz_mathjax.py:550-551`, `587-589`); `feature.js` has no cache at all.
19. `[MISSING]` Cache shaped text runs, keyed by
    `(QFont properties incl. kerning/stretch/letterSpacing/wordSpacing/capitalization/underline/
    strikeOut/overline, text, variant, bold, italic)`, cleared above 512 (`veusz_mathjax.py:672-678`,
    `693`, `764-766`).
20. `[MISSING]` Cache the converted SVG, keyed by `(svg, label family, em_units)`, with the
    no-`<text>`-present fast path, cleared above 128 (`veusz_mathjax.py:641`, `853-856`, `877-883`).
21. `[MISSING]` Scan the feature's data directory for `*.js` that declare themselves in a
    `// MATHJAX-FONT {…}` header (first 16 KiB, never executed), plus the older sidecar `fonts.json`,
    de-duplicate by font id, and record each entry's source file and kind
    (`veusz_mathjax.py:150-175`, `213-250`, `272-320`); `feature.js:16` reads only the static
    `globalThis.veuszFonts` written by hand (`fonts.js:13-20`).
22. `[MISSING]` Support `kind: 'data'` font files: read a font data file with one extra script
    evaluation into the core bundle, register it once per path, and teach the host its id/x-height so a
    run-time registered font is not treated as unknown (`veusz_mathjax.py:354-375`, `445-467`,
    `499-509`); the engine bundle already exposes `registerFont`/`__veuszMathjax`
    (`tools/build_bundle.py:606-625`) but nothing in the port calls it.
23. `[MISSING]` Create JS hosts lazily so a font nobody selects costs no parse and no memory
    (`veusz_mathjax.py:323-330`, `371-376`; `README.md:84-87`); the platform loads its one bundle up
    front.
24. `[MISSING]` Honour `VEUSZ_JSENGINES_FONTS` (an explicit font list / x-height file) as the list
    source (`veusz_mathjax.py:255-263`).
25. `[PARTIAL]` Report the plugin copy that is actually loaded (path, byte size, mtime) and the widget
    count in the log/at startup (`veusz_mathjax.py:1393-1418`); `feature.js:17` only carries a version
    string.
26. `[MISSING]` Write the render/measure failures a user can act on: the original logs the outdated
    bundle once, the raster font once per family, and the install failure with a traceback
    (`veusz_mathjax.py:91-99`, `441-443`, `1428-1437`); `feature.js` returns `veusz.error(...)` strings
    only (`feature.js:178`, `182-186`).
27. `[MISSING]` Skip text that starts with `<` (HTML/markup) — covered by the platform
    (`veusz_js_engine.py:1242`), but the feature-level decision in the original is explicit
    (`veusz_mathjax.py:1371-1372`); `feature.js:167` declines only on empty text.
28. `[MISSING/UNVERIFIED]` Serialise `compile → Qt measurement → typeset` so two paints cannot
    interleave through the shared engine state (`veusz_mathjax.py:61-63`, `530-532`, `1001-1006`);
    `feature.js` has no locking concept and this inventory did not establish whether the platform
    serialises calls.
29. `[MISSING]` Port the regression assertions, not just the wiring: measurements in points of paper at
    96 vs 300 dpi, `\text` width against `QFontMetricsF(...).horizontalAdvance`, run identity under
    every math font, spaces kept, script/scriptscript scaling (`0.707`/`0.5`), nested `\text` + maths,
    `\newcommand` expanding to `\text`, CSS emphasis, underline in the cache, italic origin, and the
    legacy-API comparison (`test/test_text_font.py:95-266`); the smoke checks 1-11
    (`test/smoke_test.py:79-598`). The port's `test_two_layer.py` covers load order, the decline path
    and "switching font changes the picture" (`features/mathjax/README.md:95-108`).
30. `[NO-OP]` `fonts.json`'s `ranges` field has no runtime consumer in the original
    (`tools/build_bundle.py:507-508`, `812-814`; no reference in `veusz_mathjax.py`) — nothing to port,
    but keep it if the font list stays generated.

### Where these thirty stand now

Checked against the port as it is, not as it was: each line names the code that
does it and the test that pins it. Three of the thirty were dropped on purpose
(items 1, 5 and 6: the legacy `useTeX` forwarder, the disabled chooser when a
package ships one font, and the row resynchronising widgets changed from
elsewhere) — the user's decision, and the reason this list is not all `[done]`.

| # | state |
|---|---|
| 1 | **not ported, by request.** The feature does write `mathjax` as the setting name, so `.vsz` files keep working; the legacy `useTeX` name is not forwarded. |
| 2 | **done.** `entryFor`/`fontOf` resolve the chosen id, call `setFont`, and come back to the declared default for an empty or unknown id; asserted by `test_the_javascript_mathjax_feature_reproduces_the_geometry` (`id="…"` stripped, glyph paths compared). |
| 3 | **done.** The default comes from the declarations, and the same test asserts `outlines('') == outlines('nonsense') == outlines('tex')`. |
| 4 | **done.** Every property is declared with a `descr` — the font chooser included — and the platform passes it to Veusz as the tooltip (`_property_setting`). |
| 5 | **not ported, by request.** A one-font feature still shows a chooser with one entry. |
| 6 | **not ported, by request.** The platform builds the settings; the row does not push values back into widgets written from elsewhere. |
| 7 | **done.** `prepareVeuszText`/`renderVeuszText` over the platform's seam (`veusz_js_engine.py:2336-2350`), asserted by `features/mathjax/test/test_text_in_formulas.py` (12 cases). |
| 8 | **done, differently.** `canShapeText()` asks whether the bundle has the text API and simply draws the words in the math font when it does not; the "old bundle" warning has nothing to warn about now that the bundle is built here. |
| 9 | **done.** `measure_text_runs`: `QTextLayout` at 1000 px, per-glyph-run fallback, kerning/ligatures/RTL/CJK, advance rather than ink, decorations as rectangles. |
| 10 | **done.** The bundle resolves `\textbf`/`\textit`/element and CSS emphasis into per-run `bold`/`italic`, which the run cache and Qt measurement both use; asserted by `test_css_emphasis_is_measured_before_outlining`. |
| 11 | **done.** `measure_text_runs` scales `AbsoluteSpacing` letter and word spacing with the outline pixel size. |
| 12 | **done.** `TextOutlineUnavailable` → the seam re-renders with `discard=True`, `_warn_unoutlinable` writes one line per family and `state.note` records it; asserted end to end by `test_06_a_font_without_outlines_still_draws_the_formula`. |
| 13 | **done.** `svg_text_as_paths` keeps each element's own `transform` and emits `data-veusz-text` for the run paths. |
| 14 | **done.** `_GENERIC_FAMILIES` substitutes the element's family for the generic ones. |
| 15 | **done.** `em_in_user_units` gives fallback characters one em, not MathJax's `2ex`. |
| 16 | **done.** The `inFontUcs4` check in `_text_to_path_data` leaves a character the family lacks to Qt's own SVG route. |
| 17 | **done.** `qt_text_factor`/`cancel_qt_text_factor`, for Veusz's `RecordPaintDevice` and nothing else; asserted by `test_a_leftover_text_element_is_rescued_from_device_dpi` (15px at 96 dpi, 4.8px at 300 dpi). |
| 18 | **done.** `feature.js`'s `rendered` cache, keyed by `[text, size, colour, display, fontId, req.face]`, cleared above 256. |
| 19 | **done.** `_TEXT_RUNS` in the platform, keyed by font key, text, variant, bold, italic, cleared above 512. |
| 20 | **done.** `_CONVERTED`, keyed by the markup and the family, with the no-`<text>` fast path, cleared above 128. |
| 21 | **done, and moved.** The platform hands over the head of *every* `*.js` the feature carries as `globalThis.veuszFileHeads`, and the feature parses its own `// MATHJAX-FONT` convention; nothing is executed to list a font. The older sidecar `fonts.json` is now a build-time artifact, and `test_05_the_font_chooser_lists_what_the_bundle_declares` keeps the two copies in step. |
| 22 | **done.** A font file is read by replying `{load: "fonts/x.js"}` → `load_feature_file`, once per path, and the feature is asked to draw again. |
| 23 | **done.** `fonts/` is not run when the feature loads; only the font that is actually used is read. This is the platform's rule, not the feature's. |
| 24 | **not ported, deliberately.** The new platform does not share the original's `VEUSZ_JSENGINES_*` namespace (`README.md`); the list source is the declarations in the files a feature carries. |
| 25 | **partly.** The install banner prints the version, the engine path, the features and the widget count, and `report()` lists every runtime by path; the original's byte size and mtime are not printed. |
| 26 | **done.** `_warn_unoutlinable`, `state.note` (deduplicated, so fifty repaints are one line) and the log beside the plugin. |
| 27 | **done.** The platform declines markup for a text starting with `<` (`veusz_js_engine.py:1261`); the feature declines empty text. |
| 28 | **done, differently.** A runtime belongs to the thread that made it (`_anchor_to_this_thread`, `stack_budget_for_this_thread`), which is what the original's lock was for; the two-pass handshake is two synchronous calls, so nothing can interleave inside it. |
| 29 | **done.** Ported into `features/mathjax/test/test_text_in_formulas.py` (the original's `test/test_text_font.py:95-266`) and `test/test_platform.py` (outlines, the element's font, the dpi correction, the raster fallback). The legacy-API comparison is the byte-exact golden in `test_the_javascript_mathjax_feature_reproduces_the_geometry`. |
| 30 | **nothing to port.** `ranges` still has no consumer; `fonts.json` stays a build-time artifact. |

Two things this audit described but did not number as gaps, and where they went:

* the composite row (`_MathJaxRow`, `1200-1272`) — the switch, the font menu and
  the Display style box on one line — is the platform's **`row`** declaration
  now: properties that name the same `row` share a line, and the first of them
  owns it (`_merge_into_one_row` in `veusz_js_engine.py`). It is a platform
  capability, not a MathJax special case: any feature can ask for it, and the
  members keep the ordinary controls Veusz would have made, so they follow
  changes made elsewhere without the original's `_sync`/`ignore` machinery.
* the font files under `fonts/` are the ones built for the original: all twenty
  are in the feature now, one file per font, read on demand (item 21-23), and
  the chooser offers every font the files and the bundle declare.

---

## Ambiguities worth flagging

* `useTeX`'s hidden-ness: `hidden=True` is passed for `mathjaxDisplay` and `mathjaxFont` but **not**
  for `useTeX` (`veusz_mathjax.py:1292-1308`), while `README.md:408` calls it hidden. Whether
  `SettingBackwardCompat` hides itself is not visible in this plugin.
* `set_font` applies the ex-height only when the value is truthy (`veusz_mathjax.py:523-525`): a font
  whose declared `x_height` is `None`/`0` leaves the bridge's previous value in place.
* `_HostSet.host_for` can return `None` (unknown default, or a `data` font with no core host),
  `measure()` then raises `AttributeError`, and `_renderer` swallows it into plain-text fallback
  (`veusz_mathjax.py:356-365`, `1002-1006`, `1380-1381`). This is not documented anywhere.
* The log file is **overwritten** at install (`1414-1418`) but **appended** to by
  `_warn_unoutlinable` (`95-97`), so a raster-font warning survives only until the next Veusz start.
* `_plugin_path()` depends on a frame-local `plugin` string in Veusz's `loadPlugins`
  (`105-123`); under a different loader it silently falls back to `__file__` or `None`.
* `feature.js` is 194 lines, not the 190 given in the task brief.
