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
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / 'data'
DIST = PROJECT / 'dist'
VERSION_FILE = PROJECT / 'VERSION'

# data/ contents, each with the licence it carries
SHIP_DATA = (
    ('qjs.dll', 'MIT (quickjs-ng, unmodified)'),
    ('mathjaxbridge.dll', 'Apache-2.0 (this project)'),
    ('mathjax_bundle.js', 'Apache-2.0 (MathJax 4)'),
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
    cmd = [sys.executable, str(PROJECT / 'tools' / 'build_bundle.py'),
           '--font', args.font]
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


def stage_release():
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / ('veusz-mathjax-plugin-%s.zip' % version())
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--font', default='newcm')
    ap.add_argument('--trim', action='store_true')
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

    stage_release()

    if 'mathjaxbridge.dll' not in present and sys.platform == 'win32':
        print('[build] note: without the bridge the plugin cannot render; '
              'build it before publishing the release')
    return 0


if __name__ == '__main__':
    sys.exit(main())
