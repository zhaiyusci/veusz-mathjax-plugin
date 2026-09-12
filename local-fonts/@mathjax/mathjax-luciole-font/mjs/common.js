import { FontData } from '@mathjax/src/mjs/output/common/FontData.js';
export function CommonMathJaxLucioleFontMixin(Base) {
    var _a;
    return _a = class extends Base {
        },
        _a.defaultVariants = [
            ...FontData.defaultVariants,
            ['-size3', 'normal'],
            ['-size4', 'normal'],
            ['-size5', 'normal'],
            ['-size6', 'normal'],
        ],
        _a.defaultCssFonts = Object.assign(Object.assign({}, FontData.defaultCssFonts), {
            '-size3': ['sans-serif', false, false],
            '-size4': ['sans-serif', false, false],
            '-size5': ['sans-serif', false, false],
            '-size6': ['sans-serif', false, false],
        }),
        _a.defaultParams = Object.assign(Object.assign({}, FontData.defaultParams), {
        x_height: 0.45,
        axis_height: 0.28,
        rule_thickness: 0.1,
        sup1: 0.45,
        sup2: 0.34,
        sub1: 0.21,
        sup_drop: 0.3,
        sub_drop: 0.1,
        num1: 0.72,
        num2: 0.45,
        denom1: 0.73,
        denom2: 0.48,
        big_op_spacing1: 0.15,
        big_op_spacing2: 0.15,
        big_op_spacing3: 0.19,
        big_op_spacing4: 0.6,
        big_op_spacing5: 0.3,
        surd_height: 0.1,
        }),
        _a.defaultSizeVariants = [
            'normal',
            '-smallop',
            '-largeop',
            '-size3',
            '-size4',
            '-size5',
            '-size6',
        ],
        _a.defaultStretchVariants = [
            'normal',
            'normal',
            'normal',
            'normal',
        ],
        _a;
}
