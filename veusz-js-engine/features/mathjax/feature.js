/* MathJax formulas for Veusz: the whole feature, in JavaScript.
 *
 * The platform puts the `veusz` API in this runtime, hands over the head of
 * every file this feature carries (JavaScript cannot read a file), and then
 * loads this directory's files in name order, ending with this one -- so the
 * MathJax bundle (mathjax.js) is already here by the time the code below runs.
 * The files in `fonts/` are *not*: one font's data is megabytes, so only the
 * font that is actually used is read (see the `load` reply below).
 *
 * Nothing in here knows about Veusz or Qt.  It declares one switch, one font
 * chooser and one style box, and it answers the platform's request to draw with
 * an SVG plus the box that SVG occupies.
 */

(function () {
    'use strict';

    var VERSION = '0.3.0';

    /* A font declares itself in the head of the JavaScript file that carries
     * it -- the built-in ones in this feature's bundle, the rest in `fonts/`,
     * one file each.  Reading the declarations is what lets the chooser list a
     * font without loading it, and it is why nothing has to be listed twice. */
    var FONT_MARKER = '// MATHJAX-FONT ';

    function readDeclarations() {
        var heads = globalThis.veuszFileHeads || [];
        var fonts = [];
        var declared = null;
        heads.forEach(function (file) {
            String(file.head || '').split('\n').forEach(function (line) {
                if (line.indexOf(FONT_MARKER) !== 0) { return; }
                var json;
                try {
                    json = JSON.parse(line.slice(FONT_MARKER.length));
                } catch (err) {
                    return;                  /* not ours to complain about */
                }
                if (json.default && !declared) { declared = json.default; }
                (json.fonts || []).forEach(function (font) {
                    if (!font || !font.id) { return; }
                    var known = fonts.some(function (seen) {
                        return seen.id === font.id;
                    });
                    if (known) { return; }
                    fonts.push({id: font.id, title: font.title || font.id,
                                x_height: font.x_height, file: file.file});
                });
            });
        });
        /* nothing declared itself: still drawable, with one anonymous font */
        if (!fonts.length) {
            fonts.push({id: 'default', title: 'default', x_height: 0.442,
                        file: null});
        }
        return {default: declared || fonts[0].id, fonts: fonts};
    }

    var FONTS = readDeclarations();

    function entryFor(fontId) {
        var found = null;
        FONTS.fonts.forEach(function (font) {
            if (font.id === fontId) { found = font; }
        });
        if (found) { return found; }
        FONTS.fonts.forEach(function (font) {
            if (font.id === FONTS.default) { found = font; }
        });
        return found;
    }

    function fontOf(fontId) {
        /* An empty or unknown choice means the default -- and it must switch
         * *back* to the default rather than keep whatever the last text used
         * (the engine ignores a name it does not know, so passing it on would
         * draw one font's glyphs under another's name in the chooser). */
        var entry = entryFor(fontId);
        return entry ? entry.id : FONTS.default;
    }

    function xHeightOf(fontId) {
        var entry = entryFor(fontId);
        return entry && entry.x_height ? entry.x_height : 0.442;
    }

    function loadedFonts() {
        /* What the *engine* has registered, not what we have declared.  A font
         * file is read only when it is asked for, so this is the fact that
         * decides whether to ask: `__veuszMathjax.fonts()` is the engine's own
         * list, kept up to date as fonts register themselves, where
         * `fontNames` is only what the bundle was built with -- a font added
         * afterwards is in the first and never in the second. */
        try {
            var api = globalThis.__veuszMathjax;
            if (api && typeof api.fonts === 'function') {
                return api.fonts() || [];
            }
        } catch (err) { /* not that kind of bundle: use the static list */ }
        return globalThis.fontNames || [];
    }

    /* Only a feature's own `fonts/` may be read on its behalf, so only a font
     * declared there can be asked for.  A font the bundle was built with is
     * registered when the bundle loads, and one it merely *declares* is not
     * a file we could ask for. */
    function dataFileOf(entry) {
        var file = entry && entry.file ? String(entry.file) : '';
        return file.indexOf('fonts/') === 0 ? file : '';
    }

    function choices() {
        return FONTS.fonts.map(function (font) {
            return {value: font.id, label: font.title};
        });
    }

    /* -- what this feature is ------------------------------------------ */

    veusz.feature({name: 'mathjax', title: 'MathJax formulas',
                   target: 'text', version: VERSION});

    /* Each property is declared with the two names it has: the short handle
     * this feature uses (`on`, `font`, `display`, as the request reports them
     * back) and the name Veusz writes into the document.  The second is named
     * rather than left to the platform's default `mathjax_on`, because these
     * are the words this feature has been writing into people's documents from
     * the beginning -- a rename would silently untick every formula in an
     * existing file.
     *
     * All three share one `row`, so the panel shows them on one line --
     * the switch, the font, and the style, which is what the original plugin
     * did and what makes the row readable at a glance.  The first of them owns
     * the row (its name is the row's label); the other two are its members.
     */
    veusz.switch('on', {
        setting: 'mathjax', label: 'MathJax', default: false, row: 'formula',
        descr: 'Render this text with MathJax (the font and the style are next '
               + 'to this box)'
    });
    veusz.choice('font', {
        setting: 'mathjaxFont', label: 'Font', default: FONTS.default,
        choices: choices(), row: 'formula'
    });
    veusz.switch('display', {
        setting: 'mathjaxDisplay', label: 'Display style', default: false,
        row: 'formula',
        descr: 'Typeset as a displayed equation: larger fractions, limits '
               + 'above and below the operator.  Off typesets it inline, the '
               + 'way text in a paragraph looks.'
    });

    /* -- the geometry, in MathJax's own units ---------------------------- */

    /* MathJax writes lengths in ex: width="7.238ex" height="2.807ex", and the
     * baseline as style="vertical-align: -0.797ex".  One ex is this font's
     * x_height of an em and an em is the size in points -- knowledge only this
     * feature has, which is why the conversion is here and the platform is only
     * ever handed points.
     *
     * The formatting mirrors what the platform's predecessor did (three
     * decimals, "pt"), because the numbers in the tests were measured from it.
     */
    function rootTag(markup) {
        var start = markup.indexOf('<svg');
        if (start < 0) { return null; }
        var end = markup.indexOf('>', start);
        if (end < 0) { return null; }
        return {start: start, end: end, text: markup.substring(start, end + 1)};
    }

    function quoted(tag, name) {
        var at = tag.indexOf(name + '="');
        if (at < 0) { return ''; }
        var begin = at + name.length + 2;
        var stop = tag.indexOf('"', begin);
        return stop < 0 ? '' : tag.substring(begin, stop);
    }

    function valign(tag) {
        var key = 'vertical-align: ';
        var at = tag.indexOf(key);
        if (at < 0) { return ''; }
        var begin = at + key.length;
        var stop = begin;
        while (stop < tag.length && ';" '.indexOf(tag.charAt(stop)) < 0) {
            stop += 1;
        }
        return tag.substring(begin, stop);
    }

    /* An ex length as points.  Anything without the `ex` suffix -- and anything
     * unparsable -- is left alone rather than guessed at. */
    function exPoints(text, xHeight, sizePt) {
        if (!text || text.slice(-2) !== 'ex') { return null; }
        var value = parseFloat(text.slice(0, -2));
        if (isNaN(value)) { return null; }
        return value * xHeight * sizePt;
    }

    /* The root's ex lengths rewritten as points, and the box they amount to. */
    function measure(markup, xHeight, sizePt) {
        var found = rootTag(markup);
        if (found === null) {
            return {svg: markup, box: null};
        }
        var tag = found.text;
        var widthEx = quoted(tag, 'width');
        var heightEx = quoted(tag, 'height');
        var depthEx = valign(tag);
        var names = ['width', 'height'];
        var i;
        for (i = 0; i < names.length; i++) {
            var name = names[i];
            var at = tag.indexOf(name + '="');
            if (at < 0) { continue; }
            var begin = at + name.length + 2;
            var stop = tag.indexOf('"', begin);
            if (stop < 0) { continue; }
            var points = exPoints(tag.substring(begin, stop), xHeight, sizePt);
            if (points === null) { continue; }
            var written = points.toFixed(3) + 'pt';
            tag = tag.substring(0, begin) + written + tag.substring(stop);
        }
        var baseline = exPoints(depthEx, xHeight, sizePt);
        if (baseline !== null) {
            var key = 'vertical-align: ';
            var start = tag.indexOf(key) + key.length;
            var end = start;
            while (end < tag.length && ';" '.indexOf(tag.charAt(end)) < 0) {
                end += 1;
            }
            var text = baseline.toFixed(3) + 'pt';
            tag = tag.substring(0, start) + text + tag.substring(end);
        }
        markup = markup.substring(0, found.start) + tag + markup.substring(found.end + 1);
        var width = exPoints(widthEx, xHeight, sizePt);
        var height = exPoints(heightEx, xHeight, sizePt);
        return {
            svg: markup,
            box: (width === null || height === null) ? null : {
                width: width,
                height: height,
                /* MathJax's vertical-align is negative below the baseline, and
                 * the platform calls that the depth. */
                depth: baseline === null ? 0 : -baseline
            }
        };
    }

    /* -- how it draws ---------------------------------------------------- */

    /* MathJax draws the characters its math font has as outlines, and the
     * words of \text{...} as text -- which has to be set in the *figure's*
     * font, not in the math font.  Only Qt can shape that text, and only this
     * feature knows the words, so it takes two passes:
     *
     *   pass 1   ask the bundle which runs it needs, hand them to the platform
     *   pass 2   the platform answers with the shaped outlines, and the bundle
     *            finishes
     *
     * The bundle compiles once and resumes, so TeX macros and counters are
     * evaluated exactly once.  A formula with no such text finishes in pass 1
     * (`renderVeuszText` with nothing measured), so the common case costs one
     * call and not two.
     */
    var warnedAboutBundle = false;

    function canShapeText() {
        return typeof globalThis.prepareVeuszText === 'function'
            && typeof globalThis.renderVeuszText === 'function';
    }

    function discardCompiled() {
        /* never leave a half-compiled formula behind: the next render would
         * resume it and produce something that belongs to nothing */
        try {
            if (typeof globalThis.discardVeuszText === 'function') {
                globalThis.discardVeuszText('');
            }
        } catch (err) { /* nothing useful to do about it */ }
    }

    function drawFormula(req, text, fontId, sizePt, display) {
        /* A font whose data has not been read is not registered with the
         * engine yet, and the engine draws in whatever font it has.  Rather
         * than draw the wrong one, ask for the data: the platform reads that
         * one file and asks again.  Only the font that is used is ever read. */
        var names = loadedFonts();
        if (names.indexOf(fontId) < 0) {
            var wanted = dataFileOf(entryFor(fontId));
            if (wanted) {
                return JSON.stringify({load: wanted});
            }
        }
        /* The chosen font belongs to the *engine*, not to one call: it has to
         * be set before every render, or the formula comes out in whatever
         * font was used last while the box is scaled for the one the chooser
         * says -- which looks like a working font switch and is not one. */
        if (typeof globalThis.setFont === 'function') {
            globalThis.setFont(fontId);
        }
        var shaping = canShapeText();
        if (!shaping && !warnedAboutBundle) {
            warnedAboutBundle = true;
            if (typeof globalThis.prepareVeuszText !== 'function') {
                veusz.note('this bundle cannot draw formula text in the '
                           + 'element\'s font; rebuild mathjax.js for that');
            }
        }
        if (req.discard) {
            /* the font the words are set in has no outlines: drop the
             * half-built formula and let the bundle draw it without them,
             * rather than reserve space for words that cannot be drawn */
            discardCompiled();
            shaping = false;
        }

        var markup = null;
        try {
            if (shaping) {
                if (req.measured) {
                    markup = globalThis.renderVeuszText(
                        JSON.stringify(req.measured));
                } else {
                    var runs = globalThis.prepareVeuszText(
                        JSON.stringify({tex: text, display: !!display}));
                    runs = JSON.parse(runs || '[]');
                    if (runs && runs.length) {
                        /* the platform shapes these and asks again */
                        return JSON.stringify({measure: runs});
                    }
                    markup = globalThis.renderVeuszText('{}');
                }
            }
            if (!markup) {
                var draw = display ? globalThis.render
                                   : globalThis.renderInline;
                if (typeof draw !== 'function') {
                    return veusz.error('the MathJax bundle did not load');
                }
                markup = draw(text);
            }
        } catch (err) {
            if (shaping) {
                discardCompiled();
            }
            return veusz.error('MathJax could not typeset this: '
                               + (err && err.message ? err.message : err));
        }
        if (!markup) {
            return veusz.error('MathJax produced nothing');
        }
        var result = measure(String(markup), xHeightOf(fontId), sizePt);
        return result.box === null
            ? veusz.svg(result.svg)
            : veusz.svg(result.svg, result.box);
    }

    /* Rendered formulas, by everything that changes the picture.  The element's
     * font is part of the key because formula text is set in it, and one
     * formula can appear on a page in two different fonts. */
    var rendered = {};
    var RENDERED_MAX = 256;

    veusz.renderText(function (req) {
        if (!req.on('on')) {
            return null;                     /* not ours: Veusz draws it */
        }
        var text = String(req.text == null ? '' : req.text);
        if (!text) {
            return null;
        }
        var fontId = fontOf(req.get('font'));
        var sizePt = req.size > 0 ? req.size : 20;
        var display = !!req.get('display');

        /* A request that carries measurements is the second pass of a formula
         * already in flight, so it must not be answered from the cache and
         * must not be stored under the same key as the finished one. */
        if (req.measured || req.discard) {
            return drawFormula(req, text, fontId, sizePt, display);
        }
        var key = [text, sizePt, req.color || '', display ? 1 : 0, fontId,
                   req.face || ''].join('\u0000');
        var hit = rendered[key];
        if (hit !== undefined) {
            return hit;
        }
        var reply = drawFormula(req, text, fontId, sizePt, display);
        /* A reply that asks for something is half an answer: a measurement, or
         * a file of ours to be read -- the finished one comes back on the next
         * pass, and that is the one worth remembering.  Caching the ask would
         * answer the platform's second call with the same request to load the
         * file, for ever. */
        if (typeof reply === 'string' && reply.charAt(0) === '{'
                && (reply.indexOf('"measure"') > 0
                    || reply.indexOf('"load"') > 0)) {
            return reply;
        }
        if (Object.keys(rendered).length >= RENDERED_MAX) {
            rendered = {};
        }
        rendered[key] = reply;
        return reply;
    });
}());
