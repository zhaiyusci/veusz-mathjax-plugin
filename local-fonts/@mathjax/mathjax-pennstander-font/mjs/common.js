import { FontData } from '@mathjax/src/mjs/output/common/FontData.js';
export function CommonMathJaxPennstanderFontMixin(Base) {
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
        x_height: 0.531,
        axis_height: 0.3,
        rule_thickness: 0.088,
        sup1: 0.4,
        sup2: 0.1,
        sub1: 0.169,
        sup_drop: 0.212,
        sub_drop: 0.181,
        num1: 0.7,
        num2: 0.692,
        denom1: 0.661,
        denom2: 0.435,
        big_op_spacing1: 0.103,
        big_op_spacing2: 0.119,
        big_op_spacing3: 0.181,
        big_op_spacing4: 0.54,
        big_op_spacing5: 0.198,
        surd_height: 0.088,
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
