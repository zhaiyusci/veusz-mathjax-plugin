"""Build the MathJax bundle that the plugin evaluates (data/mathjax_bundle.js).

Everything comes from the matched MathJax 4 sources installed from npm:

  * @mathjax/src                       - MathJax core + TeX extensions
  * @mathjax/mathjax-<font>-font       - the math font (default: newcm)
  * @mathjax/mathjax-*-font-extension  - mhchem arrows, extra blackboard/fraktur

The font's dynamic glyph ranges are imported explicitly and merged into the
font instance: QuickJS is synchronous, so nothing can be fetched at render
time.  The result defines

    globalThis.render(tex)        -> SVG string (display style)
    globalThis.renderInline(tex)  -> SVG string (inline style)

which is the only contract the bridge needs.

Usage:
    python tools/build_bundle.py                    # newcm, all glyph ranges
    python tools/build_bundle.py --font tex         # Computer Modern instead
    python tools/build_bundle.py --trim             # drop rare script ranges
    python tools/build_bundle.py --no-verify        # skip the render battery

npm needs network access; behind a proxy set HTTPS_PROXY / npm_config_proxy.
"""

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / 'build' / 'bundle'          # npm workspace, not shipped
DATA = PROJECT / 'data'                      # what the plugin loads
BRIDGE = DATA / 'mathjaxbridge.dll'
OUT = DATA / 'mathjax_bundle.js'
FONTS_JSON = DATA / 'fonts.json'

# where the MathJax packages are resolved from; --packages / MATHJAX_NODE_MODULES
# can point at one or more existing node_modules trees instead of installing
PACKAGES_DIRS = []
ESBUILD_OVERRIDE = None

MATHJAX_VERSION = '4.1.3'

FONTS = {
    'newcm': ('@mathjax/mathjax-newcm-font', 'MathJaxNewcmFont'),
    'tex': ('@mathjax/mathjax-tex-font', 'MathJaxTexFont'),
    'modern': ('@mathjax/mathjax-modern-font', 'MathJaxModernFont'),
    'stix2': ('@mathjax/mathjax-stix2-font', 'MathJaxStix2Font'),
    'asana': ('@mathjax/mathjax-asana-font', 'MathJaxAsanaFont'),
    'bonum': ('@mathjax/mathjax-bonum-font', 'MathJaxBonumFont'),
    'dejavu': ('@mathjax/mathjax-dejavu-font', 'MathJaxDejavuFont'),
    'fira': ('@mathjax/mathjax-fira-font', 'MathJaxFiraFont'),
    'pagella': ('@mathjax/mathjax-pagella-font', 'MathJaxPagellaFont'),
    'schola': ('@mathjax/mathjax-schola-font', 'MathJaxScholaFont'),
    'termes': ('@mathjax/mathjax-termes-font', 'MathJaxTermesFont'),
    # generated from an OpenType math font by tools/build_mathjax_font.py,
    # because MathJax does not ship this one (see local-fonts/README.md)
    'lete': ('@mathjax/mathjax-lete-font', 'MathJaxLeteFont'),
}

# what the font chooser in veusz shows
FONT_TITLES = {
    'newcm': 'New Computer Modern',
    'tex': 'Computer Modern (TeX)',
    'modern': 'Modern',
    'stix2': 'STIX Two',
    'fira': 'Fira',
    'pagella': 'Pagella',
    'schola': 'Schola',
    'termes': 'Termes',
    'bonum': 'Bonum',
    'dejavu': 'DejaVu',
    'asana': 'Asana',
    'lete': 'Lete Sans Math',
}

# the fonts in a full build, cheapest first, default first.
#
# 'lete' is the odd one out: it is converted from an OpenType font by us
# (tools/build_mathjax_font.py, see local-fonts/README.md) rather than shipped by
# MathJax, and sits last because it is the largest.  A build can also take one
# font on its own (--font lete), which is what the per-font release zips do.
ALL_FONTS = ['newcm', 'tex', 'stix2', 'modern', 'fira', 'pagella', 'schola',
             'termes', 'bonum', 'dejavu', 'asana', 'lete']

# glyph ranges that only matter for scripts a plot rarely needs
TRIM = {
    'arabic', 'braille', 'braille-d', 'cherokee', 'cyrillic', 'cyrillic-ss',
    'devanagari', 'hebrew', 'phonetics', 'phonetics-ss', 'greek-ss',
    'monospace-ex', 'sans-serif-ex', 'sans-serif-r',
}

TEX_PACKAGES = [
    'base', 'ams', 'newcommand', 'boldsymbol', 'braket', 'cancel',
    'color', 'enclose', 'extpfeil', 'html', 'mhchem', 'noerrors',
    'noundefined', 'physics', 'mathtools', 'amscd', 'action', 'bbox',
    'unicode', 'verb', 'textmacros', 'textcomp', 'cases',
]

TEX_EXTENSIONS = [
    'ams/AmsConfiguration', 'newcommand/NewcommandConfiguration',
    'boldsymbol/BoldsymbolConfiguration', 'braket/BraketConfiguration',
    'cancel/CancelConfiguration', 'color/ColorConfiguration',
    'enclose/EncloseConfiguration', 'extpfeil/ExtpfeilConfiguration',
    'html/HtmlConfiguration', 'mhchem/MhchemConfiguration',
    'noerrors/NoErrorsConfiguration', 'noundefined/NoUndefinedConfiguration',
    'physics/PhysicsConfiguration', 'mathtools/MathtoolsConfiguration',
    'amscd/AmsCdConfiguration', 'action/ActionConfiguration',
    'bbox/BboxConfiguration', 'unicode/UnicodeConfiguration',
    'verb/VerbConfiguration', 'textmacros/TextMacrosConfiguration',
    'textcomp/TextcompConfiguration', 'cases/CasesConfiguration',
]

FONT_EXTENSIONS = [
    ('MathJaxMhchemFontExtension', '@mathjax/mathjax-mhchem-font-extension'),
    ('MathJaxBboldxFontExtension', '@mathjax/mathjax-bboldx-font-extension'),
    ('MathJaxDsfontFontExtension', '@mathjax/mathjax-dsfont-font-extension'),
    ('MathJaxBbmFontExtension', '@mathjax/mathjax-bbm-font-extension'),
]

BATTERY = [
    ('plain', r'$x^2 + \alpha + \frac{1}{2}$'),
    ('mathbf', r'$\mathbf{A} + \boldsymbol{\beta}$'),
    ('mathbb', r'$\mathbb{R} \subset \mathbb{C}$'),
    ('mathcal', r'$\mathcal{L}\{f\}$'),
    ('mathfrak', r'$\mathfrak{g} + \mathfrak{su}(2)$'),
    ('mathsf', r'$\mathsf{ABC}$'),
    ('mathtt', r'$\mathtt{abc}$'),
    ('text', r'$\text{hello}$'),
    ('greek', r'$\Gamma, \Delta, \Theta, \Lambda, \Xi, \Pi$'),
    ('arrows', r'$A \xrightarrow{f} B \rightleftharpoons C$'),
    ('bigop', r'$\sum_{n=1}^{\infty} \int_0^1 \oint_C \prod_k$'),
    ('mhchem', r'$\ce{2H2 + O2 -> 2H2O}$'),
    ('physics', r'$\dv{f}{x} + \qty(\pdv{g}{y})$'),
    ('matrix', r'$\begin{pmatrix} a & b \\ c & d \end{pmatrix}$'),
    ('cases', r'$f(x) = \begin{cases} 1 & x>0 \\ 0 & x\le0 \end{cases}$'),
    ('superscript', r'$b^2$'),
]

# ---------------------------------------------------------------------------
# npm dependencies
# ---------------------------------------------------------------------------

NODE = None
NPM = None


def find_tools():
    """Locate node and npm: env override, PATH, then a few usual places."""
    global NODE, NPM
    if NODE and NPM:
        return NODE, NPM

    node = os.environ.get('NODE') or shutil.which('node')
    if not node:
        candidates = []
        if os.name == 'nt':
            candidates += [
                PROJECT.parent / 'tools' / 'node',
                PROJECT.parent / 'node',
                Path(r'C:\Program Files\nodejs'),
                Path(os.environ.get('LOCALAPPDATA', ''),
                     'Programs', 'nodejs'),
            ]
        for base in candidates:
            if not base.exists():
                continue
            hits = sorted(base.glob('*/node.exe')) or sorted(
                base.glob('node.exe'))
            if hits:
                node = str(hits[0])
                break
    if not node:
        raise SystemExit(
            'node was not found.  Install Node.js (https://nodejs.org) and put '
            'it on PATH, or point the NODE / NPM environment variables at it.')

    NODE = node
    npm = os.environ.get('NPM')
    if not npm:
        beside = Path(node).parent / ('npm.cmd' if os.name == 'nt' else 'npm')
        npm = str(beside) if beside.exists() else (shutil.which('npm') or 'npm')
    NPM = npm
    return NODE, NPM


def npm_env():
    env = dict(os.environ)
    # npm does not read HTTPS_PROXY by default
    for var in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy',
                'ALL_PROXY', 'all_proxy'):
        if var in os.environ:
            env.setdefault('npm_config_proxy', os.environ[var])
            env.setdefault('npm_config_https_proxy', os.environ[var])
    if 'npm_config_cache' not in env:
        env['npm_config_cache'] = str(PROJECT / 'build' / 'npm-cache')
    return env


def ensure_dependencies(font_packages, force=False):
    """Make sure npm has everything the requested fonts need.

    font_packages is a list of npm package names (one per font).
    """
    WORK.mkdir(parents=True, exist_ok=True)

    def install(*packages):
        cmd = [NPM, 'install', '--no-audit', '--no-fund'] + list(packages)
        print('[bundle]', ' '.join(cmd))
        # npm spawns helpers of its own; let it inherit our stdio (capturing
        # its output through pipes makes those spawns fail on some systems)
        proc = subprocess.run(cmd, cwd=str(WORK), env=npm_env())
        if proc.returncode != 0:
            raise SystemExit('npm install failed (see the output above)')

    if isinstance(font_packages, str):
        font_packages = [font_packages]
    pkgdir = WORK / 'node_modules'
    have_mathjax = (pkgdir / '@mathjax' / 'src').is_dir()
    have_fonts = all(
        pkgdir.joinpath(*p.split('/')).is_dir() for p in font_packages)
    have_esbuild = ESBUILD.exists()
    if force or not (have_mathjax and have_fonts and have_esbuild):
        install(
            '@mathjax/src@%s' % MATHJAX_VERSION,
            *(('%s@%s' % (p, MATHJAX_VERSION)) for p in font_packages),
            *(('%s@%s' % (pkg, MATHJAX_VERSION)) for _, pkg in FONT_EXTENSIONS),
            'esbuild',
        )


def esbuild_path():
    if ESBUILD_OVERRIDE:
        return Path(ESBUILD_OVERRIDE)
    for base in PACKAGES_DIRS:
        cand = base / 'esbuild' / 'bin' / 'esbuild'
        if cand.exists():
            return cand
    return PACKAGES_DIRS[0] / 'esbuild' / 'bin' / 'esbuild'


def find_package(name):
    """A package by npm name, or by path to the folder that holds it.

    A path is what lets someone bundle a font package of their own without
    adding it to FONTS: `--font-package /somewhere/mathjax-mine-font`.
    """
    direct = Path(name)
    if direct.is_dir() and (direct / 'package.json').exists():
        return direct
    for base in PACKAGES_DIRS:
        cand = base
        for part in name.split('/'):
            cand = cand / part
        if cand.is_dir():
            return cand
    return None


# font packages given by path on the command line (--font-package), filled in by
# register_font_package(): id -> {'package': path, 'class': class name,
# 'title': title}.  Everything else in this script reads fonts through
# font_source()/font_title(), so a font from a path behaves like a known one.
EXTRA_FONTS = {}


def register_font_package(path):
    """Take a font package from a folder and work out what it calls itself.

    Nothing has to be declared: the id comes from the package name
    (@mathjax/mathjax-<id>-font, or the folder name), the exported font class
    from mjs/svg.js, the title from the package's own metadata if it has any.
    """
    pkgdir = find_package(str(path))
    if pkgdir is None:
        raise SystemExit('%s is not a font package (no package.json)' % path)
    meta = {}
    pkgjson = pkgdir / 'package.json'
    try:
        meta = json.loads(pkgjson.read_text(encoding='utf-8'))
    except Exception:
        pass
    name = str(meta.get('name') or pkgdir.name)
    parts = name.split('/')[-1]
    if parts.startswith('mathjax-') and parts.endswith('-font'):
        font_id = parts[len('mathjax-'):-len('-font')]
    else:
        font_id = parts
    svg = pkgdir / 'mjs' / 'svg.js'
    font_class = None
    if svg.exists():
        m = re.search(r'export\s+class\s+(\w+)',
                      svg.read_text(encoding='utf-8', errors='replace'))
        if m:
            font_class = m.group(1)
    if font_class is None:
        raise SystemExit('%s does not export a font class from mjs/svg.js'
                         % pkgdir)
    EXTRA_FONTS[font_id] = {
        'package': name,          # how the entry imports it (esbuild aliases it)
        'path': str(pkgdir),      # where it lives
        'class': font_class,
        'title': str(meta.get('title') or meta.get('description') or font_id),
    }
    return font_id


def font_source(font_id):
    """(package name for the bundle's import, exported font class)."""
    if font_id in EXTRA_FONTS:
        extra = EXTRA_FONTS[font_id]
        return extra['package'], extra['class']
    return FONTS[font_id]


def font_path(font_id):
    """The folder a font package lives in, whether it is known or given."""
    if font_id in EXTRA_FONTS:
        return Path(EXTRA_FONTS[font_id]['path'])
    pkg = font_source(font_id)
    if isinstance(pkg, tuple):
        pkg = pkg[0]
    return find_package(pkg)


def font_title(font_id):
    if font_id in EXTRA_FONTS:
        return EXTRA_FONTS[font_id]['title']
    return FONT_TITLES.get(font_id, font_id)


def font_x_height(font_id):
    """x_height/em of the font, which the bridge needs for ex -> pt.

    Takes a font id, so a package given by path -- which no search path knows
    about -- resolves through font_path() like any other.
    """
    if font_id in EXTRA_FONTS:
        pkgdir = font_path(font_id)
    else:
        pkgdir = find_package(font_source(font_id)[0])
    if pkgdir is None:
        return None
    common = pkgdir / 'mjs' / 'common.js'
    if not common.exists():
        return None
    m = re.search(r'x_height:\s*([0-9.]+)',
                  common.read_text(encoding='utf-8', errors='replace'))
    if not m:
        return None
    raw = m.group(1)
    if raw.startswith('.'):
        raw = '0' + raw
    try:
        return float(raw)
    except ValueError:
        return None


def _strip_module(text, needed=None):
    """Turn one ES module of a font package into a plain piece of a script.

    Imports of MathJax itself are collected into ``needed``; they all read the
    running core (globalThis.__veuszMathjax), and the caller declares them once
    for the whole file -- declaring them per module collided, since common.js and
    svg.js import the same names ("invalid redefinition of lexical identifier").
    Imports of the package's own files disappear, because those files are
    concatenated into the same scope, and `export` goes away, since nothing
    imports this script.
    """
    out = []
    for line in text.splitlines():
        if line.startswith('//# sourceMappingURL'):
            continue
        m = re.match(r"^import\s*\{([^}]*)\}\s*from\s*'([^']+)';?\s*$", line)
        if m:
            names = [n.strip() for n in m.group(1).split(',') if n.strip()]
            source = m.group(2)
            if source.startswith('@mathjax/') and needed is not None:
                needed.update(names)
            # a relative import: that module is already in this file
            continue
        m = re.match(r"^import\s*'([^']+)';?\s*$", line)
        if m:
            continue
        out.append(re.sub(r'^export\s+(const|function|class|let|var)\s',
                          r'\1 ', line))
    return '\n'.join(out)


def write_font_data(font_id, out):
    """Write one font as a file of its own data, registering into the core.

    Nothing of MathJax is in it: the base classes and constants it needs come
    from the bundle the plugin already has loaded, and it ends by registering
    itself there.  So a font file costs its own data and no more -- one core
    serves every font, instead of each font carrying a copy.
    """
    pkgdir = font_path(font_id)
    if pkgdir is None:
        raise SystemExit('no package folder for font %s' % font_id)
    _, font_class = font_source(font_id)
    mjs = pkgdir / 'mjs'
    needed = set()
    pieces = [_strip_module((mjs / 'common.js').read_text(encoding='utf-8'),
                            needed)]
    # the glyph tables of each variant, then the delimiters, then the font class
    # itself -- the class has to be defined before anything calls it
    for table in sorted((mjs / 'svg').glob('*.js')):
        if table.name == 'default.js':
            continue        # it names the font class, so it comes after svg.js
        pieces.append(_strip_module(table.read_text(encoding='utf-8'), needed))
    pieces.append(_strip_module((mjs / 'svg.js').read_text(encoding='utf-8'),
                                needed))
    late = mjs / 'svg' / 'default.js'
    if late.exists():
        pieces.append(_strip_module(late.read_text(encoding='utf-8'), needed))
    # ... and only then the dynamic ranges, whose files call
    # FontClass.dynamicSetup() and so need that class to exist
    dynamic = sorted((mjs / 'svg' / 'dynamic').glob('*.js'))
    for table in dynamic:
        pieces.append(_strip_module(table.read_text(encoding='utf-8'), needed))
    # everything the modules wanted from MathJax, declared once
    prelude = ('  const { %s } = __veuszMathjax;' % ', '.join(sorted(needed))
               if needed else '')
    title = font_title(font_id)
    x_height = font_x_height(font_id)
    body = '\n'.join(pieces)
    text = (
        '/*!\n'
        ' * mathjax-%(id)s.js -- GENERATED FILE, do not edit by hand.\n'
        ' *\n'
        ' * The data for one math font, for the Veusz MathJax plugin.  It carries\n'
        ' * no MathJax: it reads the base classes out of the bundle the plugin has\n'
        ' * already loaded and registers this font there, so any number of these\n'
        ' * share one core.  Put it in the plugin\'s data/ directory and restart\n'
        ' * Veusz.  Built by tools/build_bundle.py --font-data.\n'
        ' *\n'
        ' *   %(title)s -- see the LICENSE and NOTICE of the plugin\n'
        ' */\n'
        '// MATHJAX-FONT %(header)s\n'
        '(function () {\n'
        '  const __veuszMathjax = globalThis.__veuszMathjax;\n'
        '  if (!__veuszMathjax || !__veuszMathjax.registerFont) {\n'
        '    throw new Error("this font needs the MathJax bundle from the plugin "\n'
        '                    + "(data/mathjax_bundle.js); it is not there");\n'
        '  }\n'
        '%(prelude)s\n'
        '%(body)s\n'
        '  __veuszMathjax.registerFont({ id: %(id_json)s, title: %(title_json)s,\n'
        '                               xHeight: %(xheight)s,\n'
        '                               FontClass: %(class)s });\n'
        '})();\n'
    ) % {
        'id': font_id,
        'title': title,
        'body': body,
        'prelude': prelude,
        'id_json': json.dumps(font_id),
        'title_json': json.dumps(title),
        'xheight': json.dumps(x_height),
        'class': font_class,
        'header': json.dumps({
            'mathjax': MATHJAX_VERSION,
            'kind': 'data',
            'default': font_id,
            'fonts': [{'id': font_id, 'title': title, 'x_height': x_height,
                       'ranges': len(dynamic)}],
        }, sort_keys=True),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding='utf-8')
    return out


# ---------------------------------------------------------------------------
# entry + esbuild
# ---------------------------------------------------------------------------

def make_entry(font_ids, trim):
    """Generate the bundle entry for one or more fonts.

    Every font gets its own SVG output jax and its own MathJax document, built
    on first use; the core (@mathjax/src) is shared.  The bundle exposes

        render(latex) / renderInline(latex)   using the current font
        setFont(name)                         switch to another font

    so the plugin can offer a font chooser without shipping one bundle per font
    (measured: 11 fonts in one bundle is ~28 MiB against 51 MiB for 11 separate
    bundles, and the output is byte-identical to the per-font bundle).

    Returns (source, meta) where meta lists what each font contributed.
    """
    meta = []
    for font_id in font_ids:
        pkg, font_class = font_source(font_id)
        pkgdir = font_path(font_id)
        if pkgdir is None:
            raise SystemExit('%s is not installed (needed for font %s)'
                             % (pkg, font_id))
        ranges = sorted(
            f.stem for f in (pkgdir / 'mjs' / 'svg' / 'dynamic').glob('*.js'))
        keep = [r for r in ranges if r not in trim]
        meta.append({
            'id': font_id,
            'title': font_title(font_id),
            'package': pkg,
            'x_height': font_x_height(font_id),
            'ranges': len(keep),
            'ranges_total': len(ranges),
        })

    lines = [
        '// MathJax %s bundle for the veusz MathJax plugin.' % MATHJAX_VERSION,
        '//',
        '// Generated by tools/build_bundle.py -- do not edit by hand.',
        '//   core      : @mathjax/src %s' % MATHJAX_VERSION,
        '//   fonts     : %s' % ', '.join(font_ids),
        '//   extensions: mhchem / bboldx / dsfont / bbm font extensions',
        '//',
        '// QuickJS is synchronous, so every glyph range is imported here and',
        '// merged into the font instance at load time.',
        'import { mathjax } from "@mathjax/src/js/mathjax.js";',
        'import { TeX } from "@mathjax/src/js/input/tex.js";',
        'import { SVG } from "@mathjax/src/js/output/svg.js";',
        'import { liteAdaptor } from "@mathjax/src/js/adaptors/liteAdaptor.js";',
        'import { RegisterHTMLHandler } from "@mathjax/src/js/handlers/html.js";',
        '// what a font data file needs from the core: the two base classes and the',
        '// direction constants (MathJax has already resolved and bundled them here)',
        'import { FontData } from "@mathjax/src/mjs/output/common/FontData.js";',
        'import { SvgFontData } from "@mathjax/src/mjs/output/svg/FontData.js";',
        'import { V, H } from "@mathjax/src/mjs/output/common/Direction.js";',
    ]
    for font_id in font_ids:
        pkg, font_class = font_source(font_id)
        lines.append('import { %s } from "%s/mjs/svg.js";' % (font_class, pkg))
    for klass, extpkg in FONT_EXTENSIONS:
        if find_package(extpkg) is not None:
            lines.append('import { %s } from "%s/mjs/svg.js";' % (klass, extpkg))
    lines.append('')
    for ext in TEX_EXTENSIONS:
        lines.append('import "@mathjax/src/js/input/tex/%s.js";' % ext)
    lines.append('')
    for font_id, info in zip(font_ids, meta):
        pkg = info['package']
        pkgdir = font_path(font_id)
        keep = sorted(
            f.stem for f in (pkgdir / 'mjs' / 'svg' / 'dynamic').glob('*.js')
            if f.stem not in trim)
        for r in keep:
            lines.append('import "%s/mjs/svg/dynamic/%s.js";' % (pkg, r))
    lines += [
        '',
        'mathjax.asyncLoad = () => {};',
        'mathjax.asyncIsSynchronous = true;',
        '',
        'const adaptor = liteAdaptor();',
        'RegisterHTMLHandler(adaptor);',
        '',
        'const texInput = new TeX({ packages: %s });'
        % ('["' + '", "'.join(TEX_PACKAGES) + '"]'),
        '',
        '// One MathJax, many fonts.  A font can be built in (imported here, the',
        '// way the released packages do it) or registered at run time by a font',
        '// data file, which is what someone drops into data/: the file uses the',
        '// classes below and calls registerFont().  Both end up in FONT_CLASSES,',
        '// so nothing downstream knows the difference.',
        'const FONT_CLASSES = {};',
        'const FONT_TITLES = {};',
        'const FONT_XHEIGHT = {};',
        'function registerFont(spec) {',
        '  if (!spec || !spec.FontClass) return false;',
        '  FONT_CLASSES[spec.id] = spec.FontClass;',
        '  FONT_TITLES[spec.id] = spec.title || spec.id;',
        '  FONT_XHEIGHT[spec.id] = spec.xHeight;',
        '  return true;',
        '}',
        'globalThis.__veuszMathjax = {',
        '  FontData: FontData, SvgFontData: SvgFontData, V: V, H: H,',
        '  registerFont: registerFont,',
        '  fonts: () => Object.keys(FONT_CLASSES),',
        '};',
    ]
    # the fonts this bundle was built with go in through the same door a font
    # data file uses, so there is one code path for both
    for info in meta:
        lines.append('registerFont({ id: %s, title: %s, xHeight: %s, '
                     'FontClass: %s });'
                     % (json.dumps(info['id']), json.dumps(info['title']),
                        json.dumps(info['x_height']), font_source(info['id'])[1]))
    lines += [
        'const docs = {};',
        'let current = %r;' % font_ids[0],
        '',
        'function docFor(name) {',
        '  if (!FONT_CLASSES[name]) name = current;',
        '  if (!docs[name]) {',
        '    // the font is selected with the documented option name (fontData)',
        '    const svgOutput = new SVG({',
        '      fontData: FONT_CLASSES[name],',
        '      fontCache: "local",',
        '      linebreaks: { inline: false },',
        '    });',
    ]
    for klass, extpkg in FONT_EXTENSIONS:
        if find_package(extpkg) is not None:
            lines.append('    svgOutput.addExtension(%s);' % klass)
    lines += [
        '    const doc = mathjax.document("", {InputJax: texInput,',
        '                                    OutputJax: svgOutput});',
        '    // pre-load every glyph range and merge it into the font',
        '    svgOutput.font.loadDynamicFilesSync();',
        '    const font = svgOutput.font;',
        '    const merge = (files) => Object.keys(files || {}).forEach((n) => {',
        '      try { files[n].setup(font); } catch (e) { /* keep going */ }',
        '    });',
        '    merge(font.CLASS.dynamicFiles);',
        '    const ext = font.CLASS.dynamicExtensions;',
        '    if (ext) for (const data of ext.values()) merge(data.files);',
        '    docs[name] = doc;',
        '  }',
        '  return docs[name];',
        '}',
        '',
        'function extractSvg(node) {',
        '  for (const child of adaptor.childNodes(node)) {',
        '    if (adaptor.kind(child) === "svg") return adaptor.serializeXML(child);',
        '  }',
        '  return adaptor.innerHTML(node);',
        '}',
        '',
        '// called through js_host_render(handle, "setFont", name, ...)',
        'globalThis.fontNames = %s;' % json.dumps(font_ids),
        'globalThis.setFont = function (name) {',
        '  const wanted = String(name).trim();',
        '  if (Object.prototype.hasOwnProperty.call(FONT_CLASSES, wanted)) {',
        '    current = wanted;',
        '  }',
        '  return current;',
        '};',
        'globalThis.currentFont = function () { return current; };',
        '',
        'globalThis.render = function (latex) {',
        '  try {',
        '    return extractSvg(docFor(current).convert(latex,',
        '      { display: true, containerWidth: 1e7 }));',
        '  } catch (e) {',
        '    throw new Error("MathJax render error: " + (e.message || String(e)));',
        '  }',
        '};',
        '',
        'globalThis.renderInline = function (latex) {',
        '  try {',
        '    return extractSvg(docFor(current).convert(latex,',
        '      { display: false, containerWidth: 1e7 }));',
        '  } catch (e) {',
        '    throw new Error("MathJax render error: " + (e.message || String(e)));',
        '  }',
        '};',
    ]
    return '\n'.join(lines) + '\n', meta


def run_esbuild(source, outfile, font_ids=()):
    esbuild = esbuild_path()
    if not esbuild.exists():
        raise SystemExit('esbuild not found at %s (run npm install, or pass '
                         '--esbuild)' % esbuild)
    node, _ = find_tools()
    # the generated entry must sit next to a node_modules tree we resolve from,
    # otherwise esbuild cannot find @mathjax/src
    mjx = find_package('@mathjax/src')
    entry = mjx.parent.parent / 'entry.generated.js'
    entry.write_text(source, encoding='utf-8')
    alias = '@mathjax/src/mjs=%s' % (mjx / 'mjs')
    # a font package of ours does not live in the node_modules tree esbuild
    # resolves from, so point esbuild straight at it
    local_aliases = []
    for font_id in font_ids:
        pkg = font_source(font_id)[0]
        found = font_path(font_id)
        if found is None:
            continue
        if font_id in EXTRA_FONTS or PROJECT / 'local-fonts' in found.parents:
            local_aliases.append('--alias:%s=%s' % (pkg, found))
    # esbuild runs in WORK, so a relative --out would land under WORK: resolve
    # it here instead (this bit us: the build wrote to build/bundle/data/ and
    # the size printed was the stale file)
    outfile = Path(outfile).resolve()
    # npm ships a JS shim (bin/esbuild) plus a native binary; run the native
    # one directly when it is what we were given
    cmd = ([str(esbuild)] if esbuild.suffix.lower() == '.exe'
           else [node, str(esbuild)])
    cmd += [str(entry), '--bundle', '--minify',
            '--platform=browser', '--format=iife', '--alias:%s' % alias]
    cmd += local_aliases
    cmd += ['--outfile=%s' % outfile]
    proc = subprocess.run(cmd, cwd=str(WORK))
    entry.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SystemExit('esbuild failed (see the output above)')
    return outfile


BANNER = '''/*!
 * mathjax_bundle.js -- GENERATED FILE, do not edit by hand.
 *
 * Built by tools/build_bundle.py in the veusz-mathjax-plugin project.
 *
 *   MathJax 4.1.3 (@mathjax/src) with %(fonts)s,
 *   and the mhchem / bboldx / dsfont / bbm font extensions
 *       Copyright (c) 2010-2026 The MathJax Consortium
 *       Licensed under the Apache License, Version 2.0 -- see LICENSE
 *   mhchemParser 4.2.1
 *       Copyright (c) 2015-2023 Martin Hensel, Apache-2.0
 *   bundled with esbuild (MIT; build tool only, not distributed)
 *
 * The font glyph ranges are inlined because the embedded engine cannot fetch
 * anything at render time.  Fonts: %(fonts)s
 */
'''


def prepend_banner(bundle, font_ids, meta=None, default=None):
    """Write this file's own provenance, and what it carries, into the file.

    esbuild keeps only the comments it recognises as legal comments, and the
    MathJax sources carry none of them (measured: one survives, mhchemParser's),
    so without this the bundle would name no copyright holder at all.

    The MATHJAX-FONT line is what makes a bundle self-describing: the plugin
    scans data/ for *.js, reads the first few KB of each and knows which fonts
    are inside without running it.  Drop a bundle in, restart, and it is in the
    font chooser -- no list anywhere has to be kept in step with it.
    """
    text = bundle.read_text(encoding='utf-8')
    names = ', '.join(font_title(f) for f in font_ids)
    header = BANNER % {'fonts': names}
    if meta:
        header += ('// MATHJAX-FONT %s\n'
                   % json.dumps({
                       'mathjax': MATHJAX_VERSION,
                       'default': default or (meta[0]['id'] if meta else None),
                       'fonts': [{'id': m['id'], 'title': m['title'],
                                  'x_height': m['x_height'],
                                  'ranges': m['ranges']} for m in meta],
                   }, sort_keys=True))
    bundle.write_text(header + text, encoding='utf-8')


def write_fonts_json(path, meta, default):
    """Record what the bundle contains, for the plugin's font chooser.

    The x-height matters as much as the name: 1ex = size * x_height decides how
    MathJax's geometry becomes points, and every font declares its own
    (newcm 0.442, stix2 0.479, dejavu 0.519).  Using one font's value for
    another renders it up to 17% off.
    """
    payload = {
        'mathjax': MATHJAX_VERSION,
        'default': default,
        'fonts': [{'id': m['id'], 'title': m['title'],
                   'x_height': m['x_height'], 'ranges': m['ranges']}
                  for m in meta],
    }
    Path(path).write_text(json.dumps(payload, indent=2) + '\n',
                          encoding='utf-8')
    return Path(path)


# ---------------------------------------------------------------------------
# optional verification through the bridge (no Qt, no veusz needed)
# ---------------------------------------------------------------------------

def load_bridge(path):
    lib = ctypes.CDLL(str(path))
    lib.mathjax_initialize.argtypes = [ctypes.c_char_p]
    lib.mathjax_initialize.restype = ctypes.c_int
    lib.mathjax_shutdown.argtypes = []
    lib.mathjax_shutdown.restype = None
    lib.mathjax_render_svg.argtypes = [
        ctypes.c_char_p, ctypes.c_float, ctypes.c_int, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_void_p)]
    lib.mathjax_render_svg.restype = ctypes.c_int
    lib.mathjax_free.argtypes = [ctypes.c_void_p]
    lib.mathjax_free.restype = None
    return lib


def verify(bundle, bridge=None):
    bridge = Path(bridge) if bridge else BRIDGE
    if not bridge.exists():
        print('[bundle] no bridge at %s -- skipping verification' % bridge)
        return None, {}
    lib = load_bridge(bridge)
    lib.mathjax_shutdown()
    if lib.mathjax_initialize(str(bundle).encode('utf-8')) != 0:
        return False, {'init': 'failed'}

    results = {}
    for label, tex in BATTERY:
        out_svg = ctypes.c_void_p()
        out_len = ctypes.c_size_t()
        w, h, b = (ctypes.c_float(), ctypes.c_float(), ctypes.c_float())
        err = ctypes.c_void_p()
        rc = lib.mathjax_render_svg(
            tex.encode('utf-8'), 20.0, 1, None, ctypes.byref(out_svg),
            ctypes.byref(out_len), ctypes.byref(w), ctypes.byref(h),
            ctypes.byref(b), ctypes.byref(err))
        if rc != 0:
            results[label] = 'ERROR rc=%d' % rc
            continue
        svg = ctypes.string_at(out_svg.value, out_len.value)
        lib.mathjax_free(out_svg)
        if re.search(rb'fill="#C00|mathcolor="#C00', svg):
            results[label] = 'red error'
        else:
            results[label] = 'ok'
    lib.mathjax_shutdown()
    return all(v == 'ok' for v in results.values()), results


def verify_fonts(bundle, meta, bridge=None):
    """Render the same formula in every font the bundle carries.

    A font that was not really bundled renders nothing or falls back, so this
    checks each name renders, and that the fonts do not all produce the same
    bytes (which would mean the switch is inert).  The ex-height correction is
    the plugin's job, not the bundle's, so sizes are only reported here.
    """
    bridge = Path(bridge) if bridge else BRIDGE
    if not bridge.exists():
        print('[bundle] no bridge -- skipping per-font verification')
        return True
    lib = load_bridge(bridge)
    lib.js_host_init.argtypes = [ctypes.c_char_p]
    lib.js_host_init.restype = ctypes.c_int
    lib.js_host_render.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_float,
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_void_p)]
    lib.js_host_render.restype = ctypes.c_int

    def call(handle, fn, arg, size=20.0, display=1):
        out = ctypes.c_void_p()
        n = ctypes.c_size_t()
        w, h, b = (ctypes.c_float(), ctypes.c_float(), ctypes.c_float())
        err = ctypes.c_void_p()
        rc = lib.js_host_render(handle, fn.encode(), arg.encode(), size,
                                display, None, 1, ctypes.byref(out),
                                ctypes.byref(n), ctypes.byref(w),
                                ctypes.byref(h), ctypes.byref(b),
                                ctypes.byref(err))
        if rc != 0:
            if err.value:
                lib.mathjax_free(err)
            return None, None
        data = ctypes.string_at(out.value, n.value)
        lib.mathjax_free(out)
        return data, (w.value, h.value, b.value)

    handle = lib.js_host_init(str(bundle).encode('utf-8'))
    if handle <= 0:
        print('[bundle] per-font check: bundle would not load')
        return False

    ok = True
    seen = {}
    print('[bundle] per-font check (\\frac{a}{b} at 20pt, before the plugin '
          'applies each font\'s x-height):')
    for m in meta:
        got = call(handle, 'setFont', m['id'])
        if got[0] is None:
            print('   %-8s FAILED to switch' % m['id'])
            ok = False
            continue
        svg, size = call(handle, 'render', r'\frac{a}{b}')
        if not svg:
            print('   %-8s FAILED to render' % m['id'])
            ok = False
            continue
        # compare the whole output: fonts that share metrics (newcm and modern
        # both come from Computer Modern and have the same x-height, so their
        # SVG header is byte-identical) still differ in their glyph paths
        digest = hashlib.sha256(svg).hexdigest()
        if digest in seen:
            print('   %-8s renders identically to %s -- the switch is inert'
                  % (m['id'], seen[digest]))
            ok = False
            continue
        seen[digest] = m['id']
        print('   %-8s %6d bytes   %.2f x %.2f pt'
              % (m['id'], len(svg), size[0], size[1]))
    lib.js_host_shutdown(handle)
    return ok


# ---------------------------------------------------------------------------

def main():
    global ESBUILD_OVERRIDE
    ap = argparse.ArgumentParser(
        description='Build the JavaScript bundle.  One font gives a small '
                    'bundle; several give a font chooser in veusz.')
    ap.add_argument('--font', default=None, choices=sorted(FONTS),
                    help='a single font (same as --fonts NAME)')
    ap.add_argument('--fonts', default=None,
                    help='comma-separated font ids for a multi-font bundle; '
                         'the first one is the default')
    ap.add_argument('--font-package', action='append', default=None,
                    metavar='DIR', dest='font_package',
                    help='a font package by folder, for a font this script does '
                         'not know: its id, title and class are read from the '
                         'package itself (repeatable).  This is how a font '
                         'someone converted for themselves is bundled without '
                         'being added to any list.')
    ap.add_argument('--all-fonts', action='store_true',
                    help='every MathJax font: %s' % ', '.join(ALL_FONTS))
    ap.add_argument('--list-fonts', action='store_true',
                    help='print the known MathJax fonts and which are '
                         'installed, then exit')
    ap.add_argument('--trim', action='store_true',
                    help='drop glyph ranges for scripts plots rarely need')
    ap.add_argument('--font-data', action='store_true', dest='font_data',
                    help='write one font as a file of its own data, for data/ '
                         '(it registers itself into the core bundle rather than '
                         'carrying a copy of MathJax); needs --font and --out')
    ap.add_argument('--out', default=None)
    ap.add_argument('--fonts-json', default=None,
                    help='where to write the font list '
                         '(default: fonts.json next to the bundle)')
    ap.add_argument('--no-verify', action='store_true')
    ap.add_argument('--reinstall', action='store_true',
                    help='force npm install of the dependencies')
    ap.add_argument('--packages', action='append', default=None,
                    metavar='DIR', help='use an existing node_modules tree '
                    'instead of npm install (repeatable; a ;-separated list '
                    'also works).  All packages must be found in one of them.')
    ap.add_argument('--esbuild', default=None,
                    help='path to the esbuild binary (default: from --packages)')
    args = ap.parse_args()

    if args.fonts:
        font_ids = [f.strip() for f in args.fonts.split(',') if f.strip()]
    elif args.font:
        font_ids = [args.font]
    elif args.all_fonts:
        font_ids = list(ALL_FONTS)
    elif args.font_package:
        # a bundle for exactly the packages given, and nothing else: this is the
        # drop-in case, where someone has one font of their own
        font_ids = []
    else:
        font_ids = ['newcm']
    for path in (args.font_package or []):
        extra_id = register_font_package(path)
        print('[bundle] font package %s -> id %r (%s)'
              % (path, extra_id, font_title(extra_id)))
        if extra_id not in font_ids:
            font_ids.append(extra_id)
    unknown = [f for f in font_ids if f not in FONTS and f not in EXTRA_FONTS]
    if unknown:
        raise SystemExit('unknown font(s): %s\nknown: %s'
                         % (', '.join(unknown),
                            ', '.join(sorted(set(FONTS) | set(EXTRA_FONTS)))))
    env_packages = os.environ.get('MATHJAX_NODE_MODULES')
    given = []
    for value in (args.packages or ([env_packages] if env_packages else [])):
        given += [p for p in str(value).split(os.pathsep) if p]
    if given:
        PACKAGES_DIRS.extend(Path(p).resolve() for p in given)
        print('[bundle] packages: %s'
              % ', '.join(str(p) for p in PACKAGES_DIRS))
    else:
        PACKAGES_DIRS.append(WORK / 'node_modules')
    # fonts converted from an OpenType math font that MathJax does not ship live
    # here, so they resolve whether or not --packages was given
    local = PROJECT / 'local-fonts'
    if (local / '@mathjax').is_dir() and local not in PACKAGES_DIRS:
        PACKAGES_DIRS.append(local)
    if args.esbuild:
        ESBUILD_OVERRIDE = args.esbuild

    if args.font_data:
        # after the search paths above: a font data file is read from the font's
        # package, which may be a converted one under local-fonts/
        if not args.font or not args.out:
            raise SystemExit('--font-data needs --font NAME and --out FILE')
        path = write_font_data(args.font, Path(args.out))
        print('[bundle] font data: %s  (%.2f MiB, MathJax not included)'
              % (path, path.stat().st_size / 1048576))
        return 0

    find_tools()
    if args.list_fonts:
        print('%-8s %-22s %-12s %s' % ('id', 'title', 'x_height', 'installed'))
        for fid in sorted(FONTS, key=lambda f: ALL_FONTS.index(f)
                          if f in ALL_FONTS else 99):
            pkg = font_source(fid)[0]
            xh = font_x_height(fid)
            print('%-8s %-22s %-12s %s'
                  % (fid, font_title(fid),
                     ('%.3f' % xh) if xh else '-',
                     'yes' if find_package(pkg) else 'no'))
        return 0
    if not given:
        ensure_dependencies([font_source(f)[0] for f in font_ids],
                            force=args.reinstall)
    elif find_package('@mathjax/src') is None:
        raise SystemExit('@mathjax/src not found under %s'
                         % ', '.join(str(p) for p in PACKAGES_DIRS))

    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)

    source, meta = make_entry(font_ids, TRIM if args.trim else set())
    bundle = run_esbuild(source, out, font_ids)
    prepend_banner(bundle, font_ids, meta, font_ids[0])
    print('[bundle] built %s  %.2f MiB  (%d font%s: %s)'
          % (bundle, bundle.stat().st_size / 1048576, len(font_ids),
             '' if len(font_ids) == 1 else 's', ', '.join(font_ids)))
    for m in meta:
        print('[bundle]   %-8s %-22s x_height %s   %d/%d ranges'
              % (m['id'], m['title'],
                 ('%.3f em' % m['x_height']) if m['x_height'] else 'unknown',
                 m['ranges'], m['ranges_total']))

    fonts_json = (Path(args.fonts_json) if args.fonts_json
                  else out.parent / 'fonts.json')
    write_fonts_json(fonts_json, meta, font_ids[0])
    print('[bundle] font list: %s' % fonts_json)

    if not args.no_verify:
        ok, results = verify(bundle)
        if ok is None:
            pass
        else:
            bad = {k: v for k, v in results.items() if v != 'ok'}
            print('[bundle] capability battery: %d/%d %s'
                  % (len(results) - len(bad), len(results),
                     '' if not bad else str(bad)))
            if not ok:
                return 1
        if len(font_ids) > 1 and not verify_fonts(bundle, meta):
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
