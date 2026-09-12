import { FontData } from '@mathjax/src/mjs/output/common/FontData.js';
export function CommonMathJaxLeteFontMixin(Base) {
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
        rule_thickness: 0.066,
        sup1: 0.42,
        sup2: 0.37,
        sub1: 0.25,
        sup_drop: 0.416,
        sub_drop: 0.2,
        num1: 0.58,
        num2: 0.45,
        denom1: 0.7,
        denom2: 0.48,
        big_op_spacing1: 0.15,
        big_op_spacing2: 0.15,
        big_op_spacing3: 0.15,
        big_op_spacing4: 0.6,
        big_op_spacing5: 0.2,
        surd_height: 0.076,
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
