import { SvgFontData } from '@mathjax/src/mjs/output/svg/FontData.js';
import { CommonMathJaxEulerFontMixin } from './common.js';
import { normal } from './svg/normal.js';
import { bold } from './svg/bold.js';
import { italic } from './svg/italic.js';
import { boldItalic } from './svg/bold-italic.js';
import { fraktur } from './svg/fraktur.js';
import { sansSerif } from './svg/sans-serif.js';
import { sansSerifItalic } from './svg/sans-serif-italic.js';
import { sansSerifBoldItalic } from './svg/sans-serif-bold-italic.js';
import { monospace } from './svg/monospace.js';
import { smallop } from './svg/smallop.js';
import { largeop } from './svg/largeop.js';
import { size3 } from './svg/size3.js';
import { size4 } from './svg/size4.js';
import { size5 } from './svg/size5.js';
import { size6 } from './svg/size6.js';
import { delimiters } from './svg/delimiters.js';
const Base = CommonMathJaxEulerFontMixin(SvgFontData);
export class MathJaxEulerFont extends Base {
    constructor(options = {}) {
        super(options);
        for (const variant of Object.keys(this.variant)) {
            this.variant[variant].cacheID = 'MJF-' + variant;
        }
    }
}
MathJaxEulerFont.NAME = 'MathJaxEuler';
MathJaxEulerFont.OPTIONS = Object.assign(Object.assign({}, Base.OPTIONS), {});
MathJaxEulerFont.defaultDelimiters = delimiters;
MathJaxEulerFont.defaultChars = {
    'normal': normal,
    'bold': bold,
    'italic': italic,
    'bold-italic': boldItalic,
    'fraktur': fraktur,
    'sans-serif': sansSerif,
    'sans-serif-italic': sansSerifItalic,
    'sans-serif-bold-italic': sansSerifBoldItalic,
    'monospace': monospace,
    '-smallop': smallop,
    '-largeop': largeop,
    '-size3': size3,
    '-size4': size4,
    '-size5': size5,
    '-size6': size6,
};
MathJaxEulerFont.dynamicFiles = SvgFontData.defineDynamicFiles([]);
