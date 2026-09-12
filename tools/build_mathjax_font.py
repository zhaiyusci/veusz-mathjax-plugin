"""Generate a MathJax v4 SVG font package from an OpenType math font.

MathJax's own font tools are not public (the repository its npm packages point
at returns 404, and the docs still say the tools are "not yet ready for public
release"), so this builds the data we need for a font MathJax does not ship.
It only produces what our plugin uses -- the SVG output -- and skips the web
fonts, the HTML/CSS data and the image tables entirely.

The layout of the generated package mirrors the official ones, e.g.
@mathjax/mathjax-termes-font:

    mjs/common.js          the font's parameters and which variants it has
    mjs/svg.js             the font class: variant table + delimiters
    mjs/svg/<variant>.js   codepoint -> [height, depth, width, {p: path}]
    package.json

The glyph entry format is MathJax's: metrics in em units (1000 units per em, so
for a 1000-unit font the numbers are just font units), and the outline as SVG
path data in the font's own coordinate system (y up) with the leading moveto
written as a bare coordinate pair and no closing Z, which is what MathJax's
path reader expects.

The set of codepoints MathJax will ask for per variant is taken from an official
package (--template): those are Unicode facts, not font data.  Glyphs the target
font does not have are simply left out, and MathJax falls back as it does for a
font with missing variants.

Usage:

    python tools/build_mathjax_font.py \
        --regular <LeteSansMath.otf> [--bold <LeteSansMath-Bold.otf>] \
        --name lete --title "Lete Sans Math" \
        --template <node_modules>/@mathjax/mathjax-termes-font \
        --out <plugin>/fonts
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont
except ImportError:
    sys.exit('this needs fontTools: python -m pip install fonttools')

UNITS = 1000            # MathJax works in 1000 units per em
# MathJax parameter -> MATH table constant (only the ones a font really
# disagrees about; the rest keep MathJax's TeX defaults)
PARAM_MAP = {
    'axis_height': 'AxisHeight',
    'rule_thickness': 'FractionRuleThickness',
    'sup1': 'SuperscriptShiftUp',
    'sup2': 'SuperscriptShiftUpCramped',
    'sub1': 'SubscriptShiftDown',
    'sub2': 'SubscriptBottomShiftDown',
    'sup_drop': 'SuperscriptBaselineDropMax',
    'sub_drop': 'SubscriptBaselineDropMin',
    'num1': 'StackTopDisplayStyleShiftUp',
    'num2': 'StackTopShiftUp',
    'denom1': 'StackBottomDisplayStyleShiftDown',
    'denom2': 'StackBottomShiftDown',
    'big_op_spacing1': 'UpperLimitGapMin',
    'big_op_spacing2': 'LowerLimitGapMin',
    'big_op_spacing3': 'UpperLimitBaselineRiseMin',
    'big_op_spacing4': 'LowerLimitBaselineDropMin',
    'big_op_spacing5': 'StackGapMin',
    'surd_height': 'RadicalRuleThickness',
}
# the spacing accent MathJax asks for -> the combining mark a font may have
# instead (this is what a TeX engine does with the ssty/flac features)
ACCENT_SUBSTITUTES = {
    0x02C6: 0x0302,   # hat
    0x02DC: 0x0303,   # tilde
    0x02D8: 0x0306,   # breve
    0x02D9: 0x0307,   # dot
    0x02DA: 0x030A,   # ring
    0x02C7: 0x030C,   # check
    0x02C9: 0x0304,   # macron
    0x02CA: 0x0301,   # acute
    0x02CB: 0x0300,   # grave
    0x02DD: 0x030B,   # double acute
}
# variant -> which file to read glyphs from when the font has a bold face
BOLD_VARIANTS = {
    'bold', 'bold-italic', 'bold-script', 'bold-fraktur', 'bold-sans-serif',
    'sans-serif-bold-italic',
}
# MathJax size variants, in the order its delimiters table lists sizes
SIZE_VARIANTS = ['-smallop', '-largeop', '-size3', '-size4', '-size5', '-size6']


class PathPen(BasePen):
    """Collect a glyph outline as MathJax path data (font units, y up)."""

    def __init__(self, glyphSet, scale=1.0, dx=0.0):
        super().__init__(glyphSet)
        self.parts = []
        # coordinates stay in the font's units scaled to 1000 per em: MathJax's
        # paths are written that way (a .676 em tall exclamation mark has
        # coordinates up to 676), while the metrics beside them are in em
        self.scale = scale
        self.dx = dx

    def _pair(self, pt):
        return '%d %d' % (round((pt[0] + self.dx) * self.scale),
                          round(pt[1] * self.scale))

    def _moveTo(self, pt):
        # MathJax writes the first moveto as a bare coordinate pair
        self.parts.append(('' if not self.parts else 'M') + self._pair(pt))

    def _lineTo(self, pt):
        self.parts.append('L' + self._pair(pt))

    def _curveToOne(self, p1, p2, p3):
        self.parts.append('C' + self._pair(p1) + ' ' + self._pair(p2) + ' '
                          + self._pair(p3))

    def _closePath(self):
        # contours are closed implicitly, as in MathJax's own data
        pass

    def _endPath(self):
        pass

    def data(self):
        return ''.join(self.parts)


def fmt(value):
    """A metric as a short decimal, the way MathJax writes them."""
    text = '%.3f' % value
    text = text.rstrip('0').rstrip('.')
    return text if text not in ('', '-0') else '0'


def entry_js(entry):
    """One glyph table row: MathJax writes ``0xNN: [h, d, w, {options}]``.

    Every option has to survive here -- the path, but also the italic
    correction ``ic`` and the accent skew ``sk`` that MathJax reads from the
    glyph's data -- so this is the only place a row is formatted.
    """
    options = []
    for key, value in entry[3].items():
        options.append('p: %s' % json.dumps(value) if key == 'p'
                       else '%s: %s' % (key, value))
    return '[%s, %s, %s, { %s }]' % (entry[0], entry[1], entry[2],
                                     ', '.join(options))


def variant_keys(templates, variant):
    """The codepoints MathJax asks for in this variant.

    Taken as the union over several official packages: any single one has holes
    (Termes ships empty stubs for script, fraktur and friends, which its font
    does not have), and we want every codepoint MathJax might ask for, so that
    a target font which *does* have those glyphs can supply them.
    """
    keys = []
    seen = set()
    for template in templates:
        path = Path(template) / 'mjs' / 'svg' / (variant + '.js')
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8')
        match = re.search(r'=\s*\{(.*)\}\s*;?\s*$', text, re.S)
        body = match.group(1) if match else text
        for found in re.finditer(r'(?:^|[\s{,])(0x[0-9A-Fa-f]+)\s*:', body):
            cp = int(found.group(1), 16)
            if cp not in seen:
                seen.add(cp)
                keys.append(cp)
    return keys


class FontSource:
    """One face: glyph outlines, metrics and the MATH table."""

    def __init__(self, path):
        self.path = Path(path)
        self.font = TTFont(str(self.path))
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.upem = self.font['head'].unitsPerEm
        self.math = self.font['MATH'].table if 'MATH' in self.font else None

    def glyph_name(self, cp):
        return self.cmap.get(cp)

    def entry(self, cp):
        """[height, depth, width, {p: path}] for a codepoint, or None."""
        name = self.cmap.get(cp)
        if name is None:
            return None
        return glyph_by_name(self, name)

    def italic_correction(self, name):
        """Italics correction of a glyph, in em (MathJax's ``ic``)."""
        info = self.math.MathGlyphInfo if self.math else None
        record = getattr(info, 'MathItalicsCorrectionInfo', None) if info else None
        if record is None:
            return None
        coverage = getattr(record, 'Coverage', None)
        if coverage is None or name not in coverage.glyphs:
            return None
        value = record.ItalicsCorrection[coverage.glyphs.index(name)]
        return getattr(value, 'Value', None)

    def top_accent_skew(self, name, advance):
        """Where a top accent is centred, as a skew from the glyph's centre.

        MathJax shifts an accent over a base by the base's ``sk`` (see
        common/Wrappers/scriptbase.js: ``let {sk, ic} = base.getOuterBBox()``),
        and ``sk`` is the MATH table's top accent attachment measured from the
        glyph's own centre -- which is what TeX's \\skewchar does.
        """
        info = self.math.MathGlyphInfo if self.math else None
        record = getattr(info, 'MathTopAccentAttachment', None) if info else None
        if record is None:
            return None
        coverage = record.TopAccentCoverage
        if coverage is None or name not in coverage.glyphs:
            return None
        value = record.TopAccentAttachment[coverage.glyphs.index(name)]
        attachment = getattr(value, 'Value', None)
        if attachment is None:
            return None
        return attachment - advance / 2.0

    def height_depth(self, cp):
        """(height, depth) of a codepoint's glyph, in em."""
        name = self.cmap.get(cp)
        if name is None:
            return None
        return glyph_extents(self, name)

    def height_width(self, cp):
        """(height, ink width) of a codepoint's glyph, in em."""
        name = self.cmap.get(cp)
        if name is None:
            return None
        box = _bounds(self, name)
        if box is None:
            return None
        em = 1.0 / float(self.upem)
        return (box[3] * em, (box[2] - box[0]) * em)

    def variant_glyphs(self, cp, direction='H'):
        """[(glyph name, ink width in units)] for a codepoint's size variants."""
        if self.math is None or not self.math.MathVariants:
            return []
        variants = self.math.MathVariants
        coverage = (variants.HorizGlyphCoverage if direction == 'H'
                    else variants.VertGlyphCoverage)
        constructions = (variants.HorizGlyphConstruction if direction == 'H'
                         else variants.VertGlyphConstruction)
        if coverage is None:
            return []
        name = self.cmap.get(cp)
        if name is None or name not in coverage.glyphs:
            return []
        construction = constructions[coverage.glyphs.index(name)]
        out = []
        for record in (construction.MathGlyphVariantRecord or []):
            box = _bounds(self, record.VariantGlyph)
            if box:
                out.append((record.VariantGlyph, box[2] - box[0]))
        return out

    def params(self):
        """MathJax parameters taken from the font's own MATH table."""
        out = {}
        os2 = self.font['OS/2']
        if getattr(os2, 'sxHeight', 0):
            out['x_height'] = os2.sxHeight / float(self.upem)
        constants = self.math.MathConstants if self.math else None
        if constants is None:
            return out
        for name, field in PARAM_MAP.items():
            record = getattr(constants, field, None)
            if record is None:
                continue
            value = getattr(record, 'Value', None)
            if value is None:
                continue
            out[name] = value / float(self.upem)
        return out

    def delimiters(self, code_of_name):
        """MathJax's delimiters table from the font's MathVariants."""
        if self.math is None or not self.math.MathVariants:
            return {}
        variants = self.math.MathVariants
        out = {}
        for coverage, direction in ((variants.VertGlyphCoverage, 'V'),
                                    (variants.HorizGlyphCoverage, 'H')):
            if coverage is None:
                continue
            for index, name in enumerate(coverage.glyphs):
                cp = code_of_name.get(name)
                if cp is None:
                    continue
                construction = (variants.VertGlyphConstruction
                                if direction == 'V'
                                else variants.HorizGlyphConstruction)[index]
                base = self.height_depth(cp)
                if base is None:
                    continue
                entry = {'dir': direction,
                         'HDW': [round(base[0], 3), round(base[1], 3),
                                 round(self.font['hmtx'][name][0]
                                       / float(self.upem), 3)]}
                sizes = [round(base[0] + base[1] if direction == 'V'
                               else self.height_width(cp)[1], 3)]
                # MathJax has seven size slots (normal, -smallop, -largeop,
                # -size3 .. -size6); a font may offer many more variant glyphs
                # than that, and indexes past the end would break the lookup.
                # Keep the smallest ones: anything bigger is assembled from the
                # stretch pieces instead.
                for record in (construction.MathGlyphVariantRecord or [])[:len(SIZE_VARIANTS)]:
                    variant_name = record.VariantGlyph
                    # the variant glyphs are not in the cmap: they are only
                    # reachable through the MATH table, so MathJax keys them by
                    # the base codepoint and looks them up per size variant
                    extents = glyph_extents(self, variant_name)
                    if extents is None:
                        continue
                    if direction == 'V':
                        sizes.append(round(extents[0] + extents[1], 3))
                    else:
                        # a horizontal delimiter grows in width, and MathJax
                        # compares these against the width it needs
                        box = _bounds(self, variant_name)
                        sizes.append(round((box[2] - box[0]) / float(self.upem),
                                           3))
                    entry.setdefault('_variants', []).append(variant_name)
                if len(sizes) > 1:
                    entry['sizes'] = sizes
                assembly = getattr(construction, 'GlyphAssembly', None)
                # MathJax reads this as [beg, ext, end, mid] (svg/Wrappers/mo.js
                # destructures exactly those four names).  Vertical assemblies
                # are listed bottom-first in every font checked (Latin Modern
                # Math, TeX Gyre Termes/Pagella, STIX Two, Lete) while MathJax's
                # data for the same fonts is [top, extender, bottom], so they are
                # reversed; horizontal ones are already left-to-right.  Extra
                # pieces (a brace has five) collapse onto those four slots.
                if assembly and assembly.PartRecords:
                    pieces = []
                    for record in assembly.PartRecords:
                        part_cp = code_of_name.get(record.glyph)
                        pieces.append(part_cp if part_cp is not None else 0)
                    if direction == 'V':
                        pieces.reverse()
                    if len(pieces) >= 2:
                        slots = [pieces[0], pieces[1], pieces[-1]]
                        slots.append(pieces[2] if len(pieces) > 3 else 0)
                        entry['stretch'] = slots
                out[cp] = entry
        return out


def code_of_glyph(font):
    """glyph name -> codepoint, built from every cmap subtable."""
    out = {}
    for table in font['cmap'].tables:
        for cp, name in table.cmap.items():
            out.setdefault(name, cp)
    return out


def write_package(args):
    templates = args.template
    regular = FontSource(args.regular)
    bold = FontSource(args.bold) if args.bold else None
    out = Path(args.out) / ('mathjax-%s-font' % args.name)
    (out / 'mjs' / 'svg').mkdir(parents=True, exist_ok=True)

    code_by_glyph = code_of_glyph(regular.font)
    if bold:
        for name, cp in code_of_glyph(bold.font).items():
            code_by_glyph.setdefault(name, cp)

    # ---- the real variants ------------------------------------------------
    variant_const = {
        'normal': 'normal', 'bold': 'bold', 'italic': 'italic',
        'bold-italic': 'boldItalic', 'double-struck': 'doubleStruck',
        'fraktur': 'fraktur', 'bold-fraktur': 'frakturBold',
        'script': 'script', 'bold-script': 'scriptBold',
        'sans-serif': 'sansSerif', 'bold-sans-serif': 'sansSerifBold',
        'sans-serif-italic': 'sansSerifItalic',
        'sans-serif-bold-italic': 'sansSerifBoldItalic',
        'monospace': 'monospace',
    }
    imports = []
    char_entries = []
    covered = {}
    for variant, export in variant_const.items():
        keys = variant_keys(templates, variant)
        if not keys:
            continue
        source = bold if (bold and variant in BOLD_VARIANTS) else regular
        entries = {}
        for cp in keys:
            entry = source.entry(cp)
            if entry is None and source is not regular:
                entry = regular.entry(cp)
            if entry is not None:
                entries[cp] = entry
        covered[variant] = len(entries)
        if not entries:
            continue
        module = variant              # the file is named like the variant
        imports.append((export, module, variant))
        lines = ['export const %s = {' % export]
        for cp in sorted(entries):
            e = entries[cp]
            lines.append('    0x%X: %s,' % (cp, entry_js(e)))
        lines.append('};')
        (out / 'mjs' / 'svg' / (variant + '.js')).write_text(
            '\n'.join(lines) + '\n', encoding='utf-8')

    # ---- delimiters, and the accents built on them -------------------------
    delims = regular.delimiters(code_by_glyph)
    # MathJax's TeX input asks for the *spacing* accents by codepoint: \hat
    # draws U+02C6, \dot U+02D9, \breve U+02D8, \check U+02C7, \tilde U+02DC and
    # so on (its defaultAccentMap then reroutes the combining marks to those
    # same codepoints).  A font may have only the combining marks -- Lete draws
    # \hat as U+0302 -- and without these codepoints MathJax gives up on the
    # font and asks the system for the glyph, which lands wherever it guesses.
    #
    # So each missing spacing accent is filled in from the font's combining
    # mark, re-origined into the private use area: a combining mark is drawn
    # centred on the origin, over the *preceding* character (Lete's U+0302 spans
    # x -367..-33 with zero advance), while MathJax draws an accent as a
    # standalone glyph centred over its base, expecting the ink to start at x=0.
    # The mark's stretch data comes with it, which is what makes \widehat and
    # \widetilde widen to cover a wide base.
    accented = {}
    for want, have in sorted(ACCENT_SUBSTITUTES.items()):
        if want in regular.cmap:
            # the font has this accent: it still needs re-origining if it is
            # drawn as a combining mark.  Lete's U+20D7 -- the arrow \vec uses --
            # is exactly that (zero advance, ink entirely left of the origin),
            # and taken as-is it lands about a quarter em left of its base.
            entry = rebased_accent(regular, want)
            if entry is not None:
                accented[want] = entry
            continue
        if have not in regular.cmap:
            continue
        piece = glyph_by_name(regular, regular.cmap[have], rebase=True)
        if piece is None:
            continue
        accented[want] = piece
        # the mark's stretch data comes with it: that is what lets \widehat and
        # \widetilde widen over a wide base, the way a TeX engine does it
        if have in delims:
            delims[want] = delims[have]
    # the arrow and the other accents MathJax's map names that this font does
    # have: same re-origining rule
    for cp in (0x20D7, 0x00A8, 0x00AF, 0x005E, 0x007E):
        entry = rebased_accent(regular, cp)
        if entry is not None:
            accented[cp] = entry

    # \widehat and \widetilde are the *stretchy* accents: MathJax's TeX input
    # defines them as ``Accent`` with a stretchy flag, and both they and \hat
    # carry the character U+005E / U+007E (BaseMappings.js: ``hat: [Accent,
    # '005E']`` against ``widehat: [Accent, '005E', true]``).  A stretchy accent
    # grows only if that character has an entry with ``sizes`` in the font's
    # delimiters table (common/Wrappers/mo.js getStretchedVariant), which is what
    # makes the font's wider glyphs for the mark reachable at all -- and which
    # none of the eleven official MathJax fonts provides, so \widehat is a no-op
    # in MathJax with every one of them.  Supplying it here is what makes
    # \widehat{H} come out as wide as XeLaTeX draws it, while \hat stays narrow.
    for char, mark in ((0x005E, 0x02C6), (0x007E, 0x02DC)):
        if char not in delims and mark in delims:
            delims[char] = delims[mark]

    # \widehat / \widetilde: a TeX engine draws a *wider* accent for these (with
    # unicode-math and this font, XeLaTeX renders \hat{H} 84px wide at 60pt and
    # \widehat{H} 137px, the wider glyph coming from the MATH table's variants).
    # MathJax 4.1.3 has no such behaviour: \hat and \widehat come out
    # pixel-identical (0 differing pixels) with this font *and* with the stock
    # NewCM package, both resolving to the same accent codepoint U+02C6, so there
    # is nothing in the font data that could tell them apart -- putting the wide
    # variant at U+0302 (which is what TeX reaches for) changes nothing, and was
    # tried and measured.  The wide glyphs stay where the font puts them, in the
    # size variants of the mark, which MathJax does use for the stretchy
    # constructs it supports (\overrightarrow, \overbrace).

    # ---- the size variants (big operators and stretchy sizes) -------------
    # the glyphs live only in the MATH table's variant records, keyed the same
    # way MathJax looks them up: by the base codepoint, per size variant
    size_entries = {name: {} for name in SIZE_VARIANTS}
    for cp, entry in delims.items():
        for slot, glyph_name in zip(SIZE_VARIANTS,
                                    entry.get('_variants', [])):
            e = glyph_by_name(regular, glyph_name)
            if e is not None:
                size_entries[slot][cp] = e
    for slot, entries in size_entries.items():
        if not entries:
            continue
        export = ('smallop' if slot == '-smallop' else
                  'largeop' if slot == '-largeop' else
                  'size' + slot[-1])
        module = slot.lstrip('-')     # the file is named without the leading dash
        imports.append((export, module, slot))
        lines = ['export const %s = {' % export]
        for cp in sorted(entries):
            e = entries[cp]
            lines.append('    0x%X: %s,' % (cp, entry_js(e)))
        lines.append('};')
        (out / 'mjs' / 'svg' / (slot.lstrip('-') + '.js')).write_text(
            '\n'.join(lines) + '\n', encoding='utf-8')

    # MathJax draws assembly part i from the variant named by the font's
    # defaultStretchVariants[i], keyed by the codepoint in the stretch entry.
    # The official fonts spread those over four pseudo-variants (-ex-md,
    # -lf-tp, -rt-bt); this generator puts every piece in 'normal' instead and
    # points all four slots at it, so pieces only need to be in the one table.
    # Pieces whose codepoints the official key sets do not mention (a font may
    # keep them in the private use area) are added to that table here.
    normal_entries = {}
    for variant, export in variant_const.items():
        if variant != 'normal':
            continue
        keys = variant_keys(templates, variant)
        for cp in keys:
            entry = regular.entry(cp)
            if entry is not None:
                normal_entries[cp] = entry
    extra_pieces = {}
    for entry in delims.values():
        for cp in entry.get('stretch', ()):
            if cp and cp not in normal_entries:
                piece = regular.entry(cp)
                if piece is not None:
                    extra_pieces[cp] = piece

    # ---- assemble the normal table ----------------------------------------
    # it also carries the accents synthesised above and any assembly piece the
    # official key sets never mention, so both are merged in here
    if extra_pieces or accented:
        normal_entries.update(extra_pieces)
        normal_entries.update(accented)
        lines = ['export const normal = {']
        for cp in sorted(normal_entries):
            e = normal_entries[cp]
            lines.append('    0x%X: %s,' % (cp, entry_js(e)))
        lines.append('};')
        (out / 'mjs' / 'svg' / 'normal.js').write_text(
            '\n'.join(lines) + '\n', encoding='utf-8')

    clean = {}
    for cp, entry in delims.items():
        entry = dict(entry)
        variants = entry.pop('_variants', [])
        if 'sizes' in entry:
            entry['sizes'] = entry['sizes'][:1 + len(variants)][:len(
                entry['sizes'])]
        clean[cp] = entry
    lines = ["import { V, H } from '@mathjax/src/mjs/output/common/Direction.js';",
             'export const delimiters = {']
    for cp in sorted(clean):
        d = clean[cp]
        parts = ['dir: %s' % ('V' if d['dir'] == 'V' else 'H')]
        if 'sizes' in d:
            parts.append('sizes: [%s]' % ', '.join(fmt(v) for v in d['sizes']))
        if 'stretch' in d:
            parts.append('stretch: [%s]' % ', '.join('0x%X' % v
                                                     for v in d['stretch']))
        parts.append('HDW: [%s]' % ', '.join(fmt(v) for v in d['HDW']))
        lines.append('    0x%X: { %s },' % (cp, ', '.join(parts)))
    lines.append('};')
    (out / 'mjs' / 'svg' / 'delimiters.js').write_text(
        '\n'.join(lines) + '\n', encoding='utf-8')

    # ---- common.js (parameters + which variants exist) --------------------
    params = regular.params()
    if bold:
        params.setdefault('x_height', regular.params()['x_height'])
    param_lines = ['        %s: %s,' % (k, fmt(v)) for k, v in params.items()]
    variants = ['normal'] + [v for v in variant_const
                             if v != 'normal' and v in covered]
    size_list = [v for v in SIZE_VARIANTS if size_entries[v]]
    common = f"""import {{ FontData }} from '@mathjax/src/mjs/output/common/FontData.js';
export function CommonMathJax{args.name.capitalize()}FontMixin(Base) {{
    var _a;
    return _a = class extends Base {{
        }},
        _a.defaultVariants = [
            ...FontData.defaultVariants,
{chr(10).join("            ['%s', 'normal']," % v for v in size_list if v.startswith('-size'))}
        ],
        _a.defaultCssFonts = Object.assign(Object.assign({{}}, FontData.defaultCssFonts), {{
{chr(10).join("            '%s': ['sans-serif', false, false]," % v for v in size_list if v.startswith('-size'))}
        }}),
        _a.defaultParams = Object.assign(Object.assign({{}}, FontData.defaultParams), {{
{chr(10).join(param_lines)}
        }}),
        _a.defaultSizeVariants = [
{chr(10).join("            '%s'," % v for v in ['normal'] + size_list)}
        ],
        _a.defaultStretchVariants = [
            'normal',
            'normal',
            'normal',
            'normal',
        ],
        _a;
}}
"""
    (out / 'mjs' / 'common.js').write_text(common, encoding='utf-8')

    # ---- svg.js -----------------------------------------------------------
    svg_lines = ["import { SvgFontData } from '@mathjax/src/mjs/output/svg/FontData.js';",
                 "import { CommonMathJax%sFontMixin } from './common.js';"
                 % args.name.capitalize()]
    for export, module, variant in imports:
        svg_lines.append("import { %s } from './svg/%s.js';" % (export, module))
    svg_lines.append("import { delimiters } from './svg/delimiters.js';")
    class_name = 'MathJax%sFont' % args.name.capitalize()
    mixin = 'CommonMathJax%sFontMixin' % args.name.capitalize()
    svg_lines += [
        'const Base = %s(SvgFontData);' % mixin,
        'export class %s extends Base {' % class_name,
        '    constructor(options = {}) {',
        '        super(options);',
        '        for (const variant of Object.keys(this.variant)) {',
        "            this.variant[variant].cacheID = 'MJF-' + variant;",
        '        }',
        '    }',
        '}',
        "%s.NAME = 'MathJax%s';" % (class_name, args.name.capitalize()),
        '%s.OPTIONS = Object.assign(Object.assign({}, Base.OPTIONS), {});'
        % class_name,
        '%s.defaultDelimiters = delimiters;' % class_name,
        '%s.defaultChars = {' % class_name,
    ]
    for export, module, variant in imports:
        svg_lines.append("    '%s': %s," % (variant, export))
    svg_lines.append('};')
    svg_lines.append('%s.dynamicFiles = SvgFontData.defineDynamicFiles([]);'
                     % class_name)
    (out / 'mjs' / 'svg.js').write_text('\n'.join(svg_lines) + '\n',
                                        encoding='utf-8')

    (out / 'package.json').write_text(json.dumps({
        'name': '@mathjax/mathjax-%s-font' % args.name,
        'version': args.version,
        'description': '%s for MathJax v4 (generated)' % args.title,
        'license': args.license,
    }, indent=2) + '\n', encoding='utf-8')
    (out / 'mjs' / 'package.json').write_text('{\n  "type": "module"\n}\n',
                                              encoding='utf-8')

    print('[font] %s -> %s' % (args.title, out))
    print('[font]   variants: %s'
          % ', '.join('%s=%d' % (v, covered[v]) for v in covered))
    print('[font]   sizes: %s'
          % ', '.join('%s=%d' % (v, len(size_entries[v]))
                      for v in SIZE_VARIANTS))
    print('[font]   params: %s' % ', '.join('%s=%s' % (k, fmt(v))
                                            for k, v in params.items()))
    print('[font]   delimiters: %d' % len(clean))
    return out


def _bounds(source, name):
    from fontTools.pens.boundsPen import BoundsPen
    bounds = BoundsPen(source.glyphs)
    source.glyphs[name].draw(bounds)
    return bounds.bounds


def rebased_accent(source, cp):
    """A re-origined copy of an accent glyph, if the font draws it as a mark.

    MathJax draws an accent as a standalone glyph centred over its base and
    expects the ink to start at x=0 in its own advance box, while a combining
    mark is drawn centred on the origin, over the *preceding* character (Lete's
    U+20D7 arrow spans x -500..0 with zero advance, U+0302 spans -367..-33).
    Nothing to do for a glyph the font already draws as a standalone accent.
    """
    name = source.cmap.get(cp)
    if name is None:
        return None
    box = _bounds(source, name)
    if not box:
        return None
    if source.font['hmtx'][name][0] and box[0] >= 0:
        return None
    return glyph_by_name(source, name, rebase=True)


def glyph_extents(source, name):
    """(height, depth) in em for a glyph, or None if it draws nothing.

    The depth is negated: MathJax stores how far the ink reaches *down* from the
    baseline, so a glyph that sits entirely above it has a negative depth (a
    quote mark in its own data is [.676, -.431, .408]).  Writing the raw ymin
    instead puts accents and rules in the wrong place.
    """
    bounds = _bounds(source, name)
    if not bounds:
        return None
    em = 1.0 / float(source.upem)
    return (bounds[3] * em, -bounds[1] * em)


def glyph_by_name(source, name, rebase=False):
    """A [h, d, w, {p}] entry for a glyph.

    The metrics are in em; the path is in the font's units scaled to 1000 per
    em, which is how MathJax writes its own font data.

    ``rebase`` re-origins the glyph: a combining mark is drawn centred on the
    origin (Lete's U+0302 spans x -367..-33 with zero advance, because a
    combining mark sits over the *preceding* character), while MathJax draws an
    accent as a standalone glyph centred over its base, expecting the ink to
    start at x=0 inside its own advance.  Rebasing gives it that.
    """
    if name not in source.glyphs:
        return None
    extents = glyph_extents(source, name)
    if extents is None:
        return None
    bounds = _bounds(source, name)
    em = 1.0 / float(source.upem)
    if rebase:
        dx = -bounds[0]
        advance = bounds[2] - bounds[0]
    else:
        dx = 0.0
        advance = source.font['hmtx'][name][0]
    pen = PathPen(source.glyphs, UNITS / float(source.upem), dx)
    source.glyphs[name].draw(pen)
    entry = [fmt(extents[0]), fmt(extents[1]), fmt(advance * em),
             {'p': pen.data()}]
    # Where an accent goes over this glyph: MathJax shifts it by the base's
    # ``sk`` (common/Wrappers/scriptbase.js: ``let {sk, ic} = base.getOuterBBox()``
    # and the accent is placed at ``sk + 0.75 * ic``), and ``sk`` is the MATH
    # table's top accent attachment measured from the glyph's centre -- which is
    # what a TeX engine uses.  It is a small number for an upright letter and a
    # real one for a slanted glyph: Lete's math italic H needs +54.5/1000 em, and
    # without it the hat sits ~0.05em left of where XeLaTeX puts it (measured at
    # 60pt: -5.0px against TeX's +8.5px; +54.5/1000em is +13.6px, which lands it
    # at +8.6px).
    #
    # The italic correction is deliberately left out even though MathJax adds
    # 0.75 of it: measured, that pushes the accent ~0.09em too far right.  TeX
    # uses the attachment, not a fraction of the correction, so only the part
    # that corresponds to it is emitted.
    if not rebase:
        skew = source.top_accent_skew(name, source.font['hmtx'][name][0])
        if skew:
            entry[3]['sk'] = fmt(skew * em)
    return entry


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--regular', required=True)
    ap.add_argument('--bold')
    ap.add_argument('--name', required=True, help='e.g. lete')
    ap.add_argument('--title', default='a custom font')
    ap.add_argument('--version', default='1.0.0')
    ap.add_argument('--license', default='OFL-1.1')
    ap.add_argument('--template', required=True, action='append',
                    help='an official font package, for the codepoint sets '
                         '(repeatable; the sets are unioned)')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    write_package(args)


if __name__ == '__main__':
    main()
