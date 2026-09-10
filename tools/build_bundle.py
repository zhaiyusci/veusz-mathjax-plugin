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
}

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


def ensure_dependencies(font_package, force=False):
    WORK.mkdir(parents=True, exist_ok=True)

    def install(*packages):
        cmd = [NPM, 'install', '--no-audit', '--no-fund'] + list(packages)
        print('[bundle]', ' '.join(cmd))
        # npm spawns helpers of its own; let it inherit our stdio (capturing
        # its output through pipes makes those spawns fail on some systems)
        proc = subprocess.run(cmd, cwd=str(WORK), env=npm_env())
        if proc.returncode != 0:
            raise SystemExit('npm install failed (see the output above)')

    pkgdir = WORK / 'node_modules'
    have_mathjax = (pkgdir / '@mathjax' / 'src').is_dir()
    have_font = pkgdir.joinpath(*font_package.split('/')).is_dir()
    have_esbuild = ESBUILD.exists()
    if force or not (have_mathjax and have_font and have_esbuild):
        install(
            '@mathjax/src@%s' % MATHJAX_VERSION,
            '%s@%s' % (font_package, MATHJAX_VERSION),
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
    for base in PACKAGES_DIRS:
        cand = base
        for part in name.split('/'):
            cand = cand / part
        if cand.is_dir():
            return cand
    return None


def font_x_height(package):
    """x_height/em of the font, which the bridge needs for ex -> pt."""
    pkgdir = find_package(package)
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


# ---------------------------------------------------------------------------
# entry + esbuild
# ---------------------------------------------------------------------------

def make_entry(font, font_class, trim):
    pkgdir = find_package(font)
    if pkgdir is None:
        raise SystemExit('%s is not installed' % font)
    ranges = sorted(
        f.stem for f in (pkgdir / 'mjs' / 'svg' / 'dynamic').glob('*.js'))
    keep = [r for r in ranges if r not in trim]

    lines = [
        '// MathJax %s bundle for the veusz MathJax plugin.' % MATHJAX_VERSION,
        '//',
        '// Generated by tools/build_bundle.py -- do not edit by hand.',
        '//   core      : @mathjax/src %s' % MATHJAX_VERSION,
        '//   font      : %s (%s)' % (font, font_class),
        '//   extensions: mhchem / bboldx / dsfont / bbm font extensions',
        '//   ranges    : %d of %d' % (len(keep), len(ranges)),
        '//',
        '// QuickJS is synchronous, so every glyph range is imported here and',
        '// merged into the font instance at load time.',
        'import { mathjax } from "@mathjax/src/js/mathjax.js";',
        'import { TeX } from "@mathjax/src/js/input/tex.js";',
        'import { SVG } from "@mathjax/src/js/output/svg.js";',
        'import { liteAdaptor } from "@mathjax/src/js/adaptors/liteAdaptor.js";',
        'import { RegisterHTMLHandler } from "@mathjax/src/js/handlers/html.js";',
        'import { %s } from "%s/mjs/svg.js";' % (font_class, font),
    ]
    for klass, extpkg in FONT_EXTENSIONS:
        if find_package(extpkg) is not None:
            lines.append('import { %s } from "%s/mjs/svg.js";' % (klass, extpkg))
    lines.append('')
    for ext in TEX_EXTENSIONS:
        lines.append('import "@mathjax/src/js/input/tex/%s.js";' % ext)
    lines.append('')
    for r in keep:
        lines.append('import "%s/mjs/svg/dynamic/%s.js";' % (font, r))
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
        '// the font is selected with the documented option name (fontData)',
        'const svgOutput = new SVG({',
        '  fontData: %s,' % font_class,
        '  fontCache: "local",',
        '  linebreaks: { inline: false },',
        '});',
    ]
    for klass, extpkg in FONT_EXTENSIONS:
        if find_package(extpkg) is not None:
            lines.append('svgOutput.addExtension(%s);' % klass)
    lines += [
        'const htmlDoc = mathjax.document("", {',
        '  InputJax: texInput,',
        '  OutputJax: svgOutput,',
        '});',
        '',
        '// pre-load every glyph range and merge it into the font',
        'svgOutput.font.loadDynamicFilesSync();',
        '(function () {',
        '  const font = svgOutput.font;',
        '  const merge = (files) => Object.keys(files || {}).forEach((name) => {',
        '    try { files[name].setup(font); } catch (e) { /* keep going */ }',
        '  });',
        '  merge(font.CLASS.dynamicFiles);',
        '  const ext = font.CLASS.dynamicExtensions;',
        '  if (ext) for (const data of ext.values()) merge(data.files);',
        '})();',
        '',
        'function extractSvg(node) {',
        '  for (const child of adaptor.childNodes(node)) {',
        '    if (adaptor.kind(child) === "svg") return adaptor.serializeXML(child);',
        '  }',
        '  return adaptor.innerHTML(node);',
        '}',
        '',
        'globalThis.render = function (latex) {',
        '  try {',
        '    return extractSvg(htmlDoc.convert(latex,',
        '      { display: true, containerWidth: 1e7 }));',
        '  } catch (e) {',
        '    throw new Error("MathJax render error: " + (e.message || String(e)));',
        '  }',
        '};',
        '',
        'globalThis.renderInline = function (latex) {',
        '  try {',
        '    return extractSvg(htmlDoc.convert(latex,',
        '      { display: false, containerWidth: 1e7 }));',
        '  } catch (e) {',
        '    throw new Error("MathJax render error: " + (e.message || String(e)));',
        '  }',
        '};',
    ]
    return '\n'.join(lines) + '\n', len(keep), len(ranges)


def run_esbuild(source, outfile):
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
    # npm ships a JS shim (bin/esbuild) plus a native binary; run the native
    # one directly when it is what we were given
    cmd = ([str(esbuild)] if esbuild.suffix.lower() == '.exe'
           else [node, str(esbuild)])
    cmd += [str(entry), '--bundle', '--minify',
            '--platform=browser', '--format=iife', '--alias:%s' % alias,
            '--outfile=%s' % outfile]
    proc = subprocess.run(cmd, cwd=str(WORK))
    entry.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SystemExit('esbuild failed (see the output above)')
    return Path(outfile)


BANNER = '''/*!
 * mathjax_bundle.js -- GENERATED FILE, do not edit by hand.
 *
 * Built by tools/build_bundle.py in the veusz-mathjax-plugin project.
 *
 *   MathJax 4.1.3 (@mathjax/src), the @mathjax/mathjax-%(font)s-font package,
 *   and the mhchem / bboldx / dsfont / bbm font extensions
 *       Copyright (c) 2010-2026 The MathJax Consortium
 *       Licensed under the Apache License, Version 2.0 -- see LICENSE
 *   mhchemParser 4.2.1
 *       Copyright (c) 2015-2023 Martin Hensel, Apache-2.0
 *   bundled with esbuild (MIT; build tool only, not distributed)
 *
 * The font glyph ranges are inlined because the embedded engine cannot fetch
 * anything at render time.  Font: %(font)s, %(kept)d/%(total)d glyph ranges.
 */
'''


def prepend_banner(bundle, font, kept, total):
    """Write this file's own provenance into the file.

    esbuild keeps only the comments it recognises as legal comments, and the
    MathJax sources carry none of them (measured: one survives, mhchemParser's),
    so without this the bundle would name no copyright holder at all.
    """
    text = bundle.read_text(encoding='utf-8')
    bundle.write_text(
        BANNER % {'font': font, 'kept': kept, 'total': total} + text,
        encoding='utf-8')


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


# ---------------------------------------------------------------------------

def main():
    global ESBUILD_OVERRIDE
    ap = argparse.ArgumentParser()
    ap.add_argument('--font', default='newcm', choices=sorted(FONTS))
    ap.add_argument('--trim', action='store_true',
                    help='drop glyph ranges for scripts plots rarely need')
    ap.add_argument('--out', default=None)
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
    if args.esbuild:
        ESBUILD_OVERRIDE = args.esbuild

    font, font_class = FONTS[args.font]
    find_tools()
    if not given:
        ensure_dependencies(font, force=args.reinstall)
    elif find_package('@mathjax/src') is None:
        raise SystemExit('@mathjax/src not found under %s'
                         % ', '.join(str(p) for p in PACKAGES_DIRS))

    data_dir = OUT.parent
    data_dir.mkdir(parents=True, exist_ok=True)

    source, kept, total = make_entry(
        font, font_class, TRIM if args.trim else set())
    out = Path(args.out) if args.out else OUT
    bundle = run_esbuild(source, out)
    prepend_banner(bundle, args.font, kept, total)
    print('[bundle] built %s  %.2f MiB  (%d/%d glyph ranges, font %s)'
          % (bundle, bundle.stat().st_size / 1048576, kept, total, args.font))

    xh = font_x_height(font)
    if xh:
        print('[bundle] font x_height %.3f em'
              ' -> bridge env VEUSZ_MATHJAX_EXHEIGHT=%.3f' % (xh, xh))

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
    return 0


if __name__ == '__main__':
    sys.exit(main())
