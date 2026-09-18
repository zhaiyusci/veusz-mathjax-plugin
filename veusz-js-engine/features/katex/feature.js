/* KaTeX for Veusz: the whole feature, in JavaScript.
 *
 * KaTeX parses the LaTeX and hands back MathML.  Veusz draws MathML itself --
 * a text whose whole body is a `<math>...</math>` document goes to its own
 * MathML widget (`veusz/utils/textrender.py`), in the element's own font, on
 * the page and in an export alike.  So this feature draws nothing at all: it
 * asks KaTeX for the MathML, tidies the one thing that widget refuses, and
 * tells the platform to let Veusz draw *that* instead of the label's source.
 *
 * There is no MathJax in here, and no picture of ours.  What you see is
 * Veusz's own typesetting, which is also why a formula looks the way every
 * other label in the document does.
 */

(function () {
    'use strict';

    var VERSION = '0.1.0';

    veusz.feature({name: 'katex', title: 'KaTeX formulas', target: 'text',
                   version: VERSION});

    /* The two names a property has: the handle this feature reads the value
     * back with, and the name Veusz writes into the document.  They are this
     * feature's own words, so an old file keeps working if it is ever rewritten
     * in something else. */
    veusz.switch('on', {
        setting: 'katex', label: 'KaTeX', default: false, row: 'formula',
        descr: 'Typeset this text with KaTeX and let Veusz draw the MathML it '
               + 'makes'
    });
    veusz.switch('display', {
        setting: 'katexDisplay', label: 'Display style', default: false,
        row: 'formula',
        descr: 'Typeset as a displayed equation: larger fractions, limits '
               + 'above and below the operator.  Off typesets it inline.'
    });

    /* KaTeX wraps each formula in `<semantics>`, with the presentation in an
     * `<mrow>` and a copy of the LaTeX it came from in an `<annotation>`
     * beside it.  Qt's MathML widget does not know that element, so it draws
     * the annotation's *text*: the formula came out with its own source after
     * it, and the width was the source's.  Measured for `\frac{a}{b}`, with
     * and without the annotation: 212 px of ink box against 22 px, 2480 ink
     * against 533.  The presentation is what Veusz wants, so the annotation
     * goes -- it is KaTeX's note to itself, and the document already has the
     * LaTeX in the label. */
    var ANNOTATION = /<annotation\b[^>]*>[\s\S]*?<\/annotation>/g;

    /* Qt's MathML widget drops a space character inside `<mtext>` and then
     * refuses the element for having no content -- and KaTeX writes its thin
     * and medium spaces exactly that way, so `a\,b` came out as an error
     * before this.  MathML has `<mspace>` for a space, so that is what they
     * become.  Measured against the widget: of twenty constructs tried, this
     * is the only one it would not take as it stands. */
    var SPACE_WIDTH = {
        '\u2009': 0.167,      /* thin        */
        '\u200a': 0.083,      /* hair        */
        '\u2005': 0.222,      /* four-per-em */
        '\u2004': 0.278,      /* three-per-em */
        '\u00a0': 0.25,       /* no-break    */
        ' ': 0.25
    };
    var SPACE_TEXT = /<mtext>([\s\u00a0\u2000-\u200a]+)<\/mtext>/g;

    function forVeusz(mathml) {
        return mathml.replace(ANNOTATION, '')
                      .replace(SPACE_TEXT, function (whole, spaces) {
            var width = 0;
            for (var i = 0; i < spaces.length; i++) {
                width += SPACE_WIDTH[spaces.charAt(i)] || 0.25;
            }
            return '<mspace width="' + width.toFixed(3) + 'em"></mspace>';
        });
    }

    /* Parsed formulas, by everything that changes the answer.  KaTeX costs
     * about a millisecond, but a page of forty labels is a page of forty
     * parses on every repaint. */
    var rendered = {};
    var RENDERED_MAX = 256;

    function escapeXml(text) {
        return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                           .replace(/>/g, '&gt;');
    }

    /* A failure is MathML too: `<merror>` is part of the language, and Veusz's
     * widget draws it where the formula would have been -- in the label's own
     * font, with the label's box, so nothing jumps.  This is what the KaTeX
     * engine of the parent project did, and it is better than a message from
     * the platform: the label keeps working and the text says what happened. */
    function merror(message) {
        return '<math><merror><mtext>' + escapeXml(message)
            + '</mtext></merror></math>';
    }

    /* '$x$' and '$$x$$' come off: a Veusz label written for another engine
     * often carries them, and KaTeX would either typeset the dollars or refuse
     * the input.  Same rule as the parent project's KaTeX bundle. */
    function normalize(tex) {
        var t = String(tex).trim();
        if (t.length > 4 && t.slice(0, 2) === '$$' && t.slice(-2) === '$$') {
            return t.slice(2, -2);
        }
        if (t.length > 2 && t.charAt(0) === '$' && t.slice(-1) === '$') {
            return t.slice(1, -1);
        }
        return t;
    }

    veusz.renderText(function (req) {
        if (!req.on('on')) {
            return null;                     /* not ours: Veusz draws it */
        }
        var text = String(req.text == null ? '' : req.text);
        if (!text) {
            return null;
        }
        if (typeof katex === 'undefined'
                || typeof katex.renderToString !== 'function') {
            return veusz.error('the KaTeX library did not load');
        }
        var display = !!req.get('display');
        var source = normalize(text);
        var key = (display ? '1' : '0') + '\u0000' + source;
        if (rendered[key] !== undefined) {
            return veusz.delegate(rendered[key]);
        }

        var out;
        try {
            /* throwOnError, so that a typo is a message rather than an error
             * formula drawn in the document */
            out = katex.renderToString(source, {
                throwOnError: true,
                displayMode: display,
                output: 'mathml'
            });
        } catch (err) {
            var mathmlError = merror('KaTeX could not typeset this: '
                                     + (err && err.message ? err.message
                                                           : err));
            rendered[key] = mathmlError;
            return veusz.delegate(mathmlError);
        }
        /* KaTeX wraps the MathML in a <span class="katex">; Veusz wants the
         * document itself (its test is `^\s*<math.*</math\s*>\s*$`) */
        var start = out.indexOf('<math');
        var end = out.lastIndexOf('</math>');
        if (start < 0 || end < 0) {
            return veusz.delegate(merror('KaTeX produced no MathML'));
        }
        var mathml = forVeusz(out.substring(start, end + 7));
        if (Object.keys(rendered).length >= RENDERED_MAX) {
            rendered = {};
        }
        rendered[key] = mathml;
        return veusz.delegate(mathml);
    });
}());
