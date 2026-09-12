"""Build the whole plugin: engine, bridge, bundle, and a release zip.

    1. src/build-quickjs-windows.cmd -> data/qjs.dll            (QuickJS, MIT)
    2. node + esbuild                -> data/mathjax_bundle.js  (MathJax, Apache-2.0)
    3. src/build-windows.cmd         -> data/mathjaxbridge.dll  (this project)
    4. dist/veusz-mathjax-plugin-<version>.zip                  (what to hand out)

The three binaries are built separately on purpose: one file, one licence
(see THIRD_PARTY.md).

Usage:
    python tools/build_all.py                  # everything it can build
    python tools/build_all.py --skip-bridge    # only the JS bundle
    python tools/build_all.py --rebuild-quickjs
    python tools/build_all.py --no-verify      # skip the render battery

Environment:
    QUICKJS_SRC   quickjs-ng checkout (also: its shared build, for the import lib)
    QUICKJS_LIB   import library of the shared QuickJS build
    VCVARS        path to vcvars64.bat (Windows, optional)
    CMAKE         path to cmake (optional; used only for the cmake fallback)
    HTTPS_PROXY   npm needs it behind a proxy
"""

import argparse
import importlib.util
import json
import re
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / 'data'
DIST = PROJECT / 'dist'
FONTS = PROJECT / 'fonts'          # single-font bundles, published in the repository
WORK = PROJECT / 'build'
VERSION_FILE = PROJECT / 'VERSION'


def _bundle_module():
    """tools/build_bundle.py, for the list of fonts and their titles.

    Imported rather than copied so there is one place that knows which fonts
    exist -- including any converted from an OpenType font by us.
    """
    path = PROJECT / 'tools' / 'build_bundle.py'
    spec = importlib.util.spec_from_file_location('build_bundle_for_list', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUNDLE = _bundle_module()
# every font this project knows how to ship, and what to call it
ALL_FONT_IDS = list(BUNDLE.ALL_FONTS) + [f for f in sorted(BUNDLE.FONTS)
                                         if f not in BUNDLE.ALL_FONTS]


def font_title(font_id):
    return BUNDLE.font_title(font_id)

# data/ contents, each with the licence it carries
SHIP_DATA = (
    ('qjs.dll', 'MIT (quickjs-ng, unmodified)'),
    ('mathjaxbridge.dll', 'Apache-2.0 (this project)'),
    ('mathjax_bundle.js', 'Apache-2.0 (MathJax 4, one or more fonts)'),
    ('fonts.json', 'font list and x-heights (this project)'),
)
SHIP_FILES = ('README.md', 'LICENSE', 'NOTICE', 'THIRD_PARTY.md',
              'veusz_mathjax.py')


def version():
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding='utf-8').strip()
    return '0.0.0'


def run(cmd, **kw):
    print('$', ' '.join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw)


def find_cmake():
    found = shutil.which('cmake')
    if found:
        return found
    for cand in (r'C:\Qt\Tools\CMake_64\bin\cmake.exe',
                 r'C:\Qt\Tools\CMake\bin\cmake.exe',
                 r'C:\Program Files\CMake\bin\cmake.exe'):
        if Path(cand).exists():
            return cand
    return None


def quickjs_dirs():
    """Where the QuickJS checkout is, and where its build should go.

    Two layouts are accepted, in this order:

        <project>/quickjs-src/          inside the project (.gitignore has it)
        <project>/../quickjs-src/       beside the project

    So cloning this repository and cloning quickjs-ng into it both work. The
    build directory is always a sibling of whichever source tree was found.
    QUICKJS_SRC / QUICKJS_LIB override the whole thing.
    """
    env_src = os.environ.get('QUICKJS_SRC')
    if env_src:
        src = Path(env_src)
        return src, Path(os.environ.get('QUICKJS_BUILD')
                         or PROJECT.parent / 'quickjs-build-shared')
    for parent in (PROJECT, PROJECT.parent):
        src = parent / 'quickjs-src'
        if (src / 'quickjs.h').exists():
            return src, parent / 'quickjs-build-shared'
    return PROJECT / 'quickjs-src', PROJECT / 'quickjs-build-shared'



def build_bundle(args):
    cmd = [sys.executable, str(PROJECT / 'tools' / 'build_bundle.py')]
    if args.flavor == 'allfonts':
        cmd.append('--all-fonts')
    else:
        cmd += ['--font', args.font]
    if args.trim:
        cmd.append('--trim')
    if args.no_verify:
        cmd.append('--no-verify')
    # forward an offline/vendored MathJax tree, so this never has to touch npm
    for packages in (args.packages or []):
        cmd += ['--packages', packages]
    if args.esbuild:
        cmd += ['--esbuild', args.esbuild]
    return run(cmd, cwd=str(PROJECT)).returncode


def build_quickjs(args):
    """Step 1: the engine, as its own shared library."""
    if not args.rebuild_quickjs and (DATA / 'qjs.dll').exists():
        print('[build] data/qjs.dll exists; reusing it '
              '(pass --rebuild-quickjs to rebuild)')
        return 0
    if sys.platform != 'win32':
        print('[build] not Windows: build QuickJS yourself with '
              '-DBUILD_SHARED_LIBS=ON and put the shared library in data/')
        return 0
    script = PROJECT / 'src' / 'build-quickjs-windows.cmd'
    if not script.exists():
        print('[build] %s missing' % script)
        return 1
    return run(['cmd', '/c', str(script)], cwd=str(PROJECT)).returncode


def build_bridge():
    """Step 3: the JS host, linked against qjs's import library."""
    if sys.platform == 'win32':
        cmd = PROJECT / 'src' / 'build-windows.cmd'
        proc = run(['cmd', '/c', str(cmd)], cwd=str(PROJECT))
        if proc.returncode == 0:
            return 0
        print('[build] direct cl build did not work (%d); trying cmake'
              % proc.returncode)
    cmake = find_cmake()
    if not cmake:
        print('[build] cmake not found')
        return 1
    build_dir = PROJECT / 'build' / 'bridge'
    quickjs_src, quickjs_build = quickjs_dirs()
    configure = [cmake, '-S', str(PROJECT / 'src'), '-B', str(build_dir),
                 '-DQUICKJS_SRC=%s' % quickjs_src,
                 '-DQUICKJS_LIB=%s' % (quickjs_build / 'qjs.lib'),
                 '-DQUICKJS_DLL=%s' % (quickjs_build / 'qjs.dll')]
    if run(configure, cwd=str(PROJECT)).returncode != 0:
        return 1
    return run([cmake, '--build', str(build_dir)],
               cwd=str(PROJECT)).returncode


def stage_release(flavor='basic'):
    DIST.mkdir(parents=True, exist_ok=True)
    suffix = '' if flavor == 'basic' else '-allfonts'
    out = DIST / ('veusz-mathjax-plugin-%s%s.zip' % (version(), suffix))
    root = 'veusz-mathjax-plugin'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in SHIP_FILES:
            path = PROJECT / name
            if path.exists():
                z.write(path, '%s/%s' % (root, name))
            else:
                print('[build] warning: %s missing, not packaged' % name)
        for lic in sorted((PROJECT / 'licenses').glob('*')):
            z.write(lic, '%s/licenses/%s' % (root, lic.name))
        for name, _licence in SHIP_DATA:
            path = DATA / name
            if path.exists():
                z.write(path, '%s/data/%s' % (root, name))
            else:
                print('[build] warning: %s missing, not packaged' % name)
    print('[build] release: %s  (%.2f MiB)' % (out, out.stat().st_size / 1048576))
    return out


FONT_HOWTO = """\
One math font for the Veusz MathJax plugin.

Unzip this next to the folder that holds veusz-mathjax-plugin (the one with
veusz_mathjax.py in it) and the file lands in its data/ directory:

    veusz-mathjax-plugin/
      veusz_mathjax.py
      data/
        %s    <- this font
        mathjax_bundle.js  <- the fonts that came with your package

Restart Veusz and the font is in the chooser of the MathJax row, on every text
element.  Nothing lists it: the bundle says what it carries, and the plugin
reads that from the file itself.

Delete the file again to remove the font.  Documents that asked for it then fall
back to the package's own font.
"""


def build_one_font(font_id, args, out):
    """One font's data, as a file to drop into data/.

    It carries no MathJax: the plugin's own bundle is the core, and this
    registers itself into it (see tools/build_bundle.py --font-data).  That is
    the whole point -- one core for every font, instead of a copy in each.
    """
    cmd = [sys.executable, str(PROJECT / 'tools' / 'build_bundle.py'),
           '--font-data', '--font', font_id, '--out', str(out)]
    for packages in (args.packages or []):
        cmd += ['--packages', packages]
    return run(cmd, cwd=str(PROJECT)).returncode


def bundle_mathjax_version(path):
    """The MathJax version a bundle was built with, from its own header."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            head = handle.read(16384)
    except Exception:
        return None
    m = re.search(r'^// MATHJAX-FONT (\{.*\})\s*$', head, re.M)
    if not m:
        return None
    try:
        return json.loads(m.group(1)).get('mathjax')
    except Exception:
        return None


def build_font_packages(args):
    """One plain .js per font, to publish as it is.

    These are what someone downloads to add a font they want: a single file that
    goes into the plugin's data/.  A bundle only changes when the MathJax version
    or that font's data does, so an existing one is left alone -- building them
    is not part of every release.  --rebuild-fonts forces it.
    """
    fonts_dir = FONTS
    fonts_dir.mkdir(parents=True, exist_ok=True)
    wanted = [args.font] if args.font else sorted(set(ALL_FONT_IDS))
    core = BUNDLE.MATHJAX_VERSION
    made = []
    for font_id in wanted:
        bundle = fonts_dir / ('mathjax-%s.js' % font_id)
        if bundle.exists() and not args.rebuild_fonts:
            have = bundle_mathjax_version(bundle)
            if have == core:
                print('[build] %-24s kept (MathJax %s)' % (bundle.name, have))
                made.append(bundle)
                continue
            print('[build] %-24s rebuilt (MathJax %s, wanted %s)'
                  % (bundle.name, have or '?', core))
        else:
            print('[build] %-24s building' % bundle.name)
        if build_one_font(font_id, args, bundle) != 0:
            print('[build] warning: %s did not build, skipped' % font_id)
            continue
        made.append(bundle)
    return made


def main():
    ap = argparse.ArgumentParser(
        description='Build the plugin and stage release zips.  Two kinds come '
                    'out of the same source: basic (the engine with one '
                    'font) and allfonts (every font we know, with a chooser '
                    'in veusz).  --flavor fonts writes the single-font '
                    'bundles into fonts/ instead: those are published in the '
                    'repository, not as release files.')
    ap.add_argument('--flavor', choices=('basic', 'allfonts', 'fonts'),
                    default='basic',
                    help='basic: one font (--font, default tex); '
                         'allfonts: every font we know; '
                         'fonts: the single-font bundles in fonts/')
    ap.add_argument('--font', default=None,
                    help='the font of a basic build (default tex: the smallest), '
                         'or one font of a --flavor fonts build '
                         '(default: every font we know)')
    ap.add_argument('--trim', action='store_true')
    ap.add_argument('--rebuild-fonts', action='store_true',
                    help='rebuild the font bundles in fonts/ even when they '
                         'were built with the same MathJax version')
    ap.add_argument('--skip-quickjs', action='store_true')
    ap.add_argument('--skip-bridge', action='store_true')
    ap.add_argument('--skip-bundle', action='store_true')
    ap.add_argument('--rebuild-quickjs', action='store_true',
                    help='rebuild data/qjs.dll even if it already exists')
    ap.add_argument('--no-verify', action='store_true')
    ap.add_argument('--packages', action='append', default=None,
                    help='node_modules tree holding the MathJax packages '
                         '(repeatable, or ";"-separated); skips npm entirely')
    ap.add_argument('--esbuild', default=None,
                    help='path to the esbuild binary (default: from --packages)')
    args = ap.parse_args()

    print('[build] flavor: %s%s' % (
        args.flavor,
        { 'basic': '',
          'allfonts': '  (every font we know)',
          'fonts': '  (one zip per font)',
        }.get(args.flavor, '')))
    if args.flavor == 'basic':
        args.font = args.font or 'tex'          # the smallest font, as before
        print('[build]   font: %s' % args.font)

    if args.flavor == 'fonts':
        # per-font packages need neither the engine nor the bridge: each is a
        # bundle to drop into an installed plugin's data/
        made = build_font_packages(args)
        print('[build] %d font bundle(s) in %s' % (len(made), FONTS))
        return 0 if made else 1

    qjs_src, qjs_build = quickjs_dirs()
    print('[build] QuickJS source: %s%s'
          % (qjs_src, '' if (qjs_src / 'quickjs.h').exists()
             else '   (MISSING -- clone quickjs-ng, or set QUICKJS_SRC)'))
    print('[build] QuickJS build : %s' % qjs_build)

    if not args.skip_bundle:
        if build_bundle(args) != 0:
            print('[build] bundle build failed')
            return 1

    if not args.skip_quickjs:
        if build_quickjs(args) != 0:
            print('[build] QuickJS build failed (need cmake + MSVC, and a '
                  'quickjs-ng checkout; see README)')

    if not args.skip_bridge:
        rc = build_bridge()
        if rc != 0:
            print('[build] bridge build failed (set QUICKJS_SRC / QUICKJS_LIB, '
                  'or build with cmake by hand; see README)')

    print('[build] data/ (one licence per file):')
    present = []
    for name, licence in SHIP_DATA:
        path = DATA / name
        if path.exists():
            present.append(name)
            print('    %-20s %9.2f MiB   %s'
                  % (name, path.stat().st_size / 1048576, licence))
        else:
            print('    %-20s %s' % (name, 'MISSING'))

    stage_release(args.flavor)

    if 'mathjaxbridge.dll' not in present and sys.platform == 'win32':
        print('[build] note: without the bridge the plugin cannot render; '
              'build it before publishing the release')
    return 0


if __name__ == '__main__':
    sys.exit(main())
