// Veusz text runs inside MathJax: Qt measures and outlines, MathJax lays out.
// Copyright 2026 Yu Zhai. Licensed under Apache-2.0 (see LICENSE).
import { SvgTextNode } from '@mathjax/src/js/output/svg/Wrappers/TextNode.js';
import { SvgWrapperFactory } from '@mathjax/src/js/output/svg/WrapperFactory.js';
import { STATE } from '@mathjax/src/js/core/MathItem.js';
import { Styles } from '@mathjax/src/js/util/Styles.js';

function runSpec(node) {
    const text = node.getText();
    const variant = node.parent.attributes.get('mathvariant') || 'normal';
    // Resolve text emphasis before outlining: CSS on a parent SVG group cannot
    // make a path bold/italic later. Font family deliberately remains Veusz's.
    let weight = '', style = '';
    for (let parent = node.parent; parent; parent = parent.parent) {
        if (!parent.attributes) continue;
        const css = new Styles(parent.attributes.getExplicit('style') || '');
        weight ||= css.get('font-weight') || parent.attributes.getExplicit('fontweight') || '';
        style ||= css.get('font-style') || parent.attributes.getExplicit('fontstyle') || '';
    }
    const bold = weight ? /^(bold|bolder)$/.test(weight) || Number(weight) >= 600
                        : variant.includes('bold');
    const italic = style ? /^(italic|oblique)$/.test(style) : variant.includes('italic');
    return { key: JSON.stringify([text, variant, bold, italic]), text, variant, bold, italic };
}

class VeuszTextNode extends SvgTextNode {
    textRun() {
        const runs = this.jax.math.outputData.veuszText;
        if (!runs || !this.node.parent.isKind('mtext')) return null;
        const spec = runSpec(this.node);
        if (!Object.prototype.hasOwnProperty.call(runs, spec.key)) {
            throw new Error('Missing Veusz text metrics: ' + spec.text);
        }
        return runs[spec.key];
    }
    computeBBox(bbox, recompute = false) {
        const run = this.textRun();
        if (!run) return super.computeBBox(bbox, recompute);
        bbox.w = run.w;
        bbox.h = run.h;
        bbox.d = run.d;
    }
    toSVG(parents) {
        const run = this.textRun();
        if (!run) return super.toSVG(parents);
        // Qt paths use y-down; the surrounding MathJax group uses y-up.
        // Parent wrappers retain script scaling, colour, and placement.
        this.dom = [this.adaptor.append(parents[0], this.svg('path', {
            'data-veusz-text': this.node.getText(),
            transform: 'scale(1,-1)',
            d: run.path,
        }))];
    }
}

export class VeuszSvgWrapperFactory extends SvgWrapperFactory {}
VeuszSvgWrapperFactory.defaultNodes = {
    ...SvgWrapperFactory.defaultNodes,
    [SvgTextNode.kind]: VeuszTextNode,
};

export function installVeuszTextApi(docFor, currentFont, adaptor, extractSvg) {
    // Calls are serialized by the Python host lock. Only one compiled item is
    // retained, and the final call consumes it even if typesetting fails.
    let pending = null;
    globalThis.veuszTextVersion = () => '1';
    globalThis.prepareVeuszText = function (payload) {
        pending = null;
        const request = JSON.parse(payload);
        const doc = docFor(currentFont());
        const item = new doc.options.MathItem(request.tex, doc.inputJax[0], request.display);
        item.start.node = adaptor.body(doc.document);
        item.setMetrics(16, 8, 1e7, 1);
        doc.clearPromises();
        item.convert(doc, STATE.COMPILED);
        const runs = new Map();
        item.root.walkTree((node) => {
            if (node.isKind('mtext')) {
                for (const child of node.childNodes) {
                    if (child.kind !== 'text') continue;
                    const spec = runSpec(child);
                    runs.set(spec.key, spec);
                }
            }
        });
        pending = { doc, item };
        return JSON.stringify([...runs.values()]);
    };
    globalThis.discardVeuszText = function () {
        pending = null;
    };
    globalThis.renderVeuszText = function (payload) {
        if (!pending) throw new Error('No prepared Veusz text');
        const { doc, item } = pending;
        pending = null;
        item.outputData.veuszText = JSON.parse(payload);
        // Resume the compiled item, not a second convert(latex): TeX macros
        // and counters must be evaluated exactly once.
        item.convert(doc);
        return extractSvg(item.typesetRoot);
    };
}
