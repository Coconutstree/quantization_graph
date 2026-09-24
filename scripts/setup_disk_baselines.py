#!/usr/bin/env python3
"""Fetch pinned official AiSAQ/Starling sources and build their native CLIs.

Requires system MKL, Boost, libaio, tcmalloc and liburing development packages.
A repository-local liburing-dev extraction is supported without a system install.
Applies only the hash-pinned storage metadata patch; never changes algorithms.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src/disk_bench'))
from official_sources import verify_source

TARGETS = {
    'aisaq': ['build_disk_index', 'search_disk_index'],
    'starling': ['build_disk_index', 'build_memory_index', 'search_disk_index',
                 'partitioner', 'index_relayout', 'gen_random_slice'],
}


def run(cmd):
    subprocess.run(list(map(str, cmd)), check=True, cwd=REPO)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--methods', nargs='+', choices=TARGETS, default=list(TARGETS))
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--fetch-only', action='store_true')
    p.add_argument('--build-root', type=Path, default=REPO/'baselines/builds',
                   help='Use an isolated build directory for new measurement instrumentation')
    args = p.parse_args()
    if args.jobs < 1:
        p.error('--jobs must be positive')
    deps = json.loads((REPO/'baselines/DEPENDENCY_LOCK.json').read_text())['dependencies']
    for name in args.methods:
        dep = deps[name]
        source = REPO/dep['path']
        if not source.exists():
            run(['git', 'clone', '--no-checkout', '--depth', '1', dep['url'], source])
            run(['git', '-C', source, 'fetch', '--depth', '1', 'origin', dep['commit']])
            run(['git', '-C', source, 'checkout', '--detach', dep['commit']])
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if head != dep['commit']:
            raise RuntimeError(f'{source}: expected {dep["commit"]}, got {head}; existing checkout left unchanged')
        if name == 'starling':
            for subpath in dep.get('submodules', {}):
                if not (source/subpath/'.git').exists():
                    run(['git', '-c', 'url.https://github.com/.insteadOf=git@github.com:', '-C', source,
                         'submodule', 'update', '--init', '--recursive', '--depth', '1', '--', subpath])
        for subpath, expected in dep.get('submodules', {}).items():
            actual = subprocess.check_output(['git', '-C', str(source/subpath), 'rev-parse', 'HEAD'], text=True).strip()
            if actual != expected:
                raise RuntimeError(f'wrong official submodule revision: {subpath}')
        verify_source(REPO, name, apply_patches=True)
        if args.fetch_only:
            continue
        build = args.build_root.resolve()/name
        options = ['-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5']
        uring = REPO/'baselines/deps/liburing-dev/usr'
        if name == 'aisaq' and (uring/'include/liburing.h').exists():
            lib = uring/'lib/x86_64-linux-gnu'
            options += [f'-DCMAKE_CXX_FLAGS=-I{uring / "include"}',
                        f'-DCMAKE_EXE_LINKER_FLAGS=-L{lib}', f'-DCMAKE_SHARED_LINKER_FLAGS=-L{lib}']
        run(['cmake', '-S', source, '-B', build, *options])
        run(['cmake', '--build', build, '--target', *TARGETS[name], '--parallel', args.jobs])


if __name__ == '__main__':
    main()
