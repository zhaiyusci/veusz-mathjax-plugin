import { FontData } from '@mathjax/src/mjs/output/common/FontData.js';
export function CommonMathJaxPlexFontMixin(Base) {
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
        x_height: 0.516,
        axis_height: 0.262,
        rule_thickness: 0.066,
        sup1: 0.477,
        sup2: 0.358,
        sub1: 0.2,
        sup_drop: 0.221,
        sub_drop: 0.105,
        num1: 0.774,
        num2: 0.516,
        denom1: 0.698,
        denom2: 0.465,
        big_op_spacing1: 0.132,
        big_op_spacing2: 0.132,
        big_op_spacing3: 0.165,
        big_op_spacing4: 0.585,
        big_op_spacing5: 0.198,
        surd_height: 0.066,
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
