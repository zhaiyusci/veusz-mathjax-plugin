import { FontData } from '@mathjax/src/mjs/output/common/FontData.js';
export function CommonMathJaxSans2FontMixin(Base) {
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
        x_height: 0.442,
        axis_height: 0.25,
        rule_thickness: 0.05,
        sup1: 0.45,
        sup2: 0.289,
        sub1: 0.21,
        sup_drop: 0.386,
        sub_drop: 0.05,
        num1: 0.677,
        num2: 0.444,
        denom1: 0.686,
        denom2: 0.345,
        big_op_spacing1: 0.111,
        big_op_spacing2: 0.167,
        big_op_spacing3: 0.2,
        big_op_spacing4: 0.6,
        big_op_spacing5: 0.15,
        surd_height: 0.05,
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
