/* The JavaScript API a feature is written against.
 *
 * This file is the platform's, and the platform runs it in a feature's runtime
 * *before* the feature's own JavaScript: `jsapi.js` is what the runtime is
 * created with, and the feature's files are added to it afterwards.  So `veusz`
 * exists by the time `feature.js` runs, and a feature needs nothing but this
 * and its own JavaScript -- no Python, and nothing about Veusz or Qt.
 *
 * Everything here is collected and then handed over as strings, because that is
 * the only channel there is: the host calls one global function with one string
 * and takes a string back.
 *
 * `render` is defined because the host looks for it when it loads a runtime:
 * `init_state()` in the bridge does `JS_GetPropertyStr(global, "render")` and
 * refuses a file that does not define it.  (Measured, one name at a time:
 * `render` alone loads, `renderInline` alone does not.)  It is a stub, and a
 * real bundle loaded into the same runtime overwrites it.  `renderInline` is
 * defined too because the bridge's render call falls back to that name when a
 * caller does not name a function and display style is off.
 */
(function () {
    'use strict';

    var spec = {};          // what veusz.feature() was told
    var props = [];         // what the property calls declared
    var painter = null;     // the render function, if the feature registered one

    globalThis.renderInline = function () { return ''; };
    globalThis.render = globalThis.renderInline;

    var veusz = {};

    /*
     * What this feature is.
     *
     *   veusz.feature({name: 'mathjax', title: 'MathJax formulas',
     *                  target: 'text'});
     *
     * `target` is what kind of thing it applies to: 'text' means every text
     * element in the document has its own copy of the properties below, and
     * that the renderer is asked once per text element.
     */
    veusz.feature = function (declared) {
        Object.keys(declared || {}).forEach(function (key) {
            spec[key] = declared[key];
        });
        return veusz;
    };

    function declare(kind, name, options) {
        var property = {kind: kind, name: name};
        Object.keys(options || {}).forEach(function (key) {
            property[key] = options[key];
        });
        props.push(property);
        return veusz;
    }

    /*
     * The properties this feature adds: one row in Veusz's properties panel
     * each, unless several of them share a `row` (see below).  The kind decides
     * which control it gets.  `label` is what the user reads, `default` is the
     * starting value, `descr` is the tooltip.
     *
     *   veusz.switch('on',      {label: 'MathJax', default: false});
     *   veusz.choice('font',    {label: 'Font', default: 'tex',
     *                            choices: [{value: 'tex', label: 'TeX'}]});
     *   veusz.text('prefix',    {label: 'Prefix', default: ''});
     *   veusz.number('scale',   {label: 'Scale', default: 1.0});
     *
     * A property has two names, because it has two audiences, and the feature
     * owns both:
     *
     *   name     the feature's own handle for the value.  It is what the
     *            request reports back -- req.get('font') -- and it never
     *            reaches Veusz.  It may be as short as you like.
     *   setting  what Veusz stores in the document, and therefore what a saved
     *            file says.  Optional.  On its own the platform qualifies the
     *            name with the feature's (`mathjax_on`), which is what keeps
     *            two features from colliding over an `on`; say it when the
     *            document's own words matter -- for instance when the feature
     *            has been writing them since before it was JavaScript, and
     *            renaming them would untick every formula in an old file.
     *
     *   veusz.switch('on', {setting: 'mathjax', label: 'MathJax'});
     *
     * Properties that name the same `row` share one line of the panel, in the
     * order they were declared -- the first of them owns the line and its label
     * is the row's.  This is presentation only: each is still a setting of the
     * document, saved and scripted under its own name.
     *
     *   veusz.switch('on',      {label: 'MathJax', row: 'formula'});
     *   veusz.choice('font',    {label: 'Font', row: 'formula', choices: [...]});
     *   veusz.switch('display', {label: 'Display style', row: 'formula'});
     *
     * draws as:  MathJax  [x]  [Computer Modern (TeX) v]  [ ] Display style
     *
     * A property on its own may also be declared `hidden: true`, which keeps
     * it out of the panel entirely while leaving it in the document.
     */
    veusz.switch = function (name, options) { return declare('switch', name, options); };
    veusz.choice = function (name, options) { return declare('choice', name, options); };
    veusz.text = function (name, options) { return declare('text', name, options); };
    veusz.number = function (name, options) { return declare('number', name, options); };

    /*
     * How it draws.  The function is handed one request and returns either
     * null -- meaning "not mine, let Veusz draw it" -- or an SVG.
     *
     *   veusz.renderText(function (req) {
     *       if (!req.on('on')) { return null; }
     *       return veusz.svg(someDrawing(req.text), {width: 12, height: 9,
     *                                                depth: 2});
     *   });
     */
    veusz.renderText = function (fn) { painter = fn; return veusz; };
    veusz.renderWidget = function (fn) { painter = fn; return veusz; };

    /*
     * An SVG and the box it occupies, in points: `width` and `height` are the
     * whole box, `depth` is how far it reaches below the baseline.  That is the
     * entire drawing contract -- the platform puts the box where the text would
     * have gone, aligns it by the baseline and paints the SVG into it.
     *
     * Working out the box is the feature's business, because only the feature
     * knows what its own geometry means.  A feature drawing in `ex` units must
     * convert them itself; `veusz.ex()` below is the one line that takes.
     */
    veusz.svg = function (markup, box) {
        box = box || {};
        return JSON.stringify({
            svg: String(markup),
            width: box.width === undefined ? null : box.width,
            height: box.height === undefined ? null : box.height,
            depth: box.depth === undefined ? null : box.depth
        });
    };

    /*
     * "Veusz, draw this text yourself."
     *
     * The platform does not know what the text means; it hands it to the same
     * renderer Veusz would have used for the label.  That is what makes a
     * markup a feature can produce but not draw -- MathML, which Veusz renders
     * with its own widget -- reachable from JavaScript, with no drawing code
     * and no library on this side at all.
     *
     *   return veusz.delegate('<math><mi>x</mi></math>');
     */
    veusz.delegate = function (text) {
        return JSON.stringify({delegate: String(text)});
    };

    /* An ex measurement in points: one ex is `exPerEm` of an em, and one em is
     * `sizePt` points.  There is deliberately no default -- the spread between
     * real fonts is wide enough (0.387 to 0.531) that a guess is visibly
     * wrong. */
    veusz.ex = function (value, exPerEm, sizePt) {
        return value * exPerEm * sizePt;
    };

    /* A feature that cannot draw says so, and the platform shows the message
     * where the drawing would have been instead of failing silently. */
    veusz.error = function (message) {
        return JSON.stringify({error: String(message)});
    };

    /* Something to say once -- "this bundle is too old for X", "that font has
     * no outlines" -- which goes to the platform's log.  Repeats of the same
     * message are dropped, because a note is about a fact and not about how
     * many times a formula happened to be painted. */
    var notes = [];
    veusz.note = function (message) {
        var text = String(message);
        if (notes.indexOf(text) < 0) { notes.push(text); }
        return veusz;
    };

    /* "Not mine" from inside a branch, when plain `null` reads badly. */
    veusz.decline = function () { return ''; };

    /* -- what the host asks -------------------------------------------------- */

    globalThis.veuszDescribe = function () {
        var out = {};
        Object.keys(spec).forEach(function (key) { out[key] = spec[key]; });
        out.properties = props;
        return JSON.stringify(out);
    };

    /*
     * One render request, one answer.
     *
     * The request is `{text, size, color, props, feature, version}`, and three
     * more fields appear when the host has something to say:
     *
     *   face      what font the text element is set in, as a string that
     *             changes whenever that font would be painted differently.
     *             A drawing that contains text of its own depends on it.
     *   measured  the answer to a reply that asked for text to be shaped --
     *             see below.
     *   discard   the host could not do what the feature asked (the font has
     *             no outlines): forget the half-built drawing and draw without
     *             your own text rather than draw nothing.
     *
     * The answer is an SVG, `veusz.svg(...)`, `veusz.error(...)`, `null` to
     * decline, `{load: 'fonts/x.js'}` to have one of your own files read (the
     * only thing the host will ever read for you -- see below), or
     * `{measure: [...]}`, which means "my drawing contains text of its own;
     * shape these runs in the element's font and ask me again".  Only the host
     * has Qt, and only the feature knows what its drawing says, so it takes
     * both to draw text in the reader's font.
     *
     * A `{load: ...}` file is one of the feature's own, under its `fonts/`
     * directory, and the host asks the feature to draw again once it is in.
     * That is how a feature lists fonts it has not paid for: the host hands
     * over the head of every file the feature carries as
     * `globalThis.veuszFileHeads = [{file, head}, ...]`, so the feature can
     * read a name and a description out of it and defer the megabytes.
     */
    globalThis.veuszRender = function (request) {
        if (!painter) { return ''; }
        var req;
        try {
            req = JSON.parse(request || '{}');
        } catch (err) {
            return veusz.error('the platform sent something that is not JSON');
        }
        var values = req.props || {};
        req.on = function (name) { return !!values[name]; };
        req.get = function (name) { return values[name]; };

        notes = [];
        var out = painter(req);
        if (out === null || out === undefined || out === false) { return ''; }
        out = (typeof out === 'string') ? out : JSON.stringify(out);
        if (notes.length) {
            /* a note has to ride on the answer, and a note can be made while
             * producing a plain SVG, so wrap that in the envelope */
            var text = notes.join('; ');
            notes = [];
            if (out.charAt(0) === '{') {
                try {
                    var merged = JSON.parse(out);
                    if (merged && typeof merged === 'object') {
                        merged.note = text;
                        return JSON.stringify(merged);
                    }
                } catch (err) { /* fall through to wrapping */ }
            }
            return JSON.stringify({svg: out, note: text});
        }
        return out;
    };

    globalThis.veusz = veusz;
}());
