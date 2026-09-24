#!/usr/bin/env python3
"""Audit a supplied pinned upstream Git checkout and build isolated Symphony ports.

No downloads and no guessed upstream identity. Local differences are emitted for
review and block certification; a clean match is built before hashes are recorded.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from disk_bench.symphony_provenance import COMMIT, reviewed_changes
from disk_bench.protocol import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-repo', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True,
                        help='Fresh, already configured CMake build directory (never the historical build)')
    parser.add_argument('--source-root', type=Path,
                        help='Actual headers compiled by CMake; defaults to the legacy vendor tree')
    parser.add_argument('--reviewed-patches', type=Path,
                        help='Explicit per-file review of instrumentation/thread API changes with pinned hashes')
    args = parser.parse_args()
    upstream, build = args.upstream_repo.resolve(), args.build_dir.resolve()
    if build == (ROOT / 'build/disk/native').resolve():
        parser.error('use an isolated build directory')
    build.mkdir(parents=True, exist_ok=True)
    output = build / 'symphonyqg.source.json'
    if output.exists():
        parser.error('audit already exists; use a new build directory')
    def git(*argv):
        return subprocess.check_output(['git', '-C', str(upstream), *argv])
    if git('rev-parse', '--show-toplevel').decode().strip() != str(upstream):
        parser.error('upstream-repo must be the root of a SymphonyQG Git checkout')
    if git('rev-parse', 'HEAD').decode().strip() != COMMIT:
        parser.error('upstream checkout is not at the pinned commit')
    git('cat-file', '-e', COMMIT + '^{commit}')
    paths = git('ls-tree', '-r', '--name-only', COMMIT, '--', 'symqglib').decode().splitlines()
    if not paths:
        parser.error('pinned commit contains no symqglib source')
    local = (args.source_root or ROOT / 'baselines/symphonyqg').resolve()
    cache = (build / 'CMakeCache.txt').read_text()
    configured = [line.split('=', 1)[1] for line in cache.splitlines()
                  if line.startswith('QGRAPH_SYMPHONY_ROOT:PATH=')]
    if len(configured) != 1 or Path(configured[0]).resolve() != local:
        parser.error('CMake QGRAPH_SYMPHONY_ROOT does not match --source-root')
    files = {p.relative_to(local).as_posix() for p in (local / 'symqglib').rglob('*') if p.is_file()}
    differences, patches, originals, sources = [], [], {}, {}
    reviews = reviewed_changes(args.reviewed_patches)
    for name in sorted(set(paths) | files):
        original = git('show', COMMIT + ':' + name) if name in paths else b''
        current = (local / name).read_bytes() if name in files else b''
        originals[name] = hashlib.sha256(original).hexdigest() if name in paths else None
        if name in files:
            sources[str((local / name).resolve())] = hashlib.sha256(current).hexdigest()
        if original != current or (name in paths) != (name in files):
            review = reviews.get(name, {})
            accepted = (name in paths and name in files and
                        review.get('upstream_sha256') == originals[name] and
                        review.get('local_sha256') == sources.get(str((local / name).resolve())))
            differences.append(dict(path=name, classification=review['category'] if accepted else 'unreviewed_local_change'))
            patches.extend(difflib.unified_diff(original.decode(errors='replace').splitlines(True),
                current.decode(errors='replace').splitlines(True), fromfile='upstream/' + name, tofile='local/' + name))
    patch = build / 'symphonyqg.local.patch'
    patch.write_text(''.join(patches))
    blocked = any(d['classification'] == 'unreviewed_local_change' for d in differences)
    if set(reviews) - {d['path'] for d in differences}:
        parser.error('patch review contains entries not present in current differences')
    report = dict(status='blocked_local_changes' if blocked else 'building', upstream_commit=COMMIT,
                  source_root=str(local),
                  upstream_checkout=str(upstream),
                  upstream_repository='https://github.com/gouyt13/SymphonyQG',
                  upstream_blobs=originals, sources=sources, differences=differences,
                  patch_path=str(patch), patch_sha256=sha256(patch))
    if args.reviewed_patches:
        report.update(patch_review_path=str(args.reviewed_patches.resolve()), patch_review_sha256=sha256(args.reviewed_patches))
    output.write_text(json.dumps(report, indent=2) + '\n')
    if blocked:
        print(f'Blocked: {len(differences)} local changes require algorithm review: {patch}')
        return 2
    names = ['qgraph05_symphonyqg_disk_port', 'qgraph05_symphonyqg_real_reference', 'qgraph05_symphonyqg_search_test']
    command = ['cmake', '--build', str(build), '--clean-first', '--target', *names, '--', '-j2']
    report['build_command'] = command
    with (build / 'symphonyqg.build.log').open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        report['status'] = 'build_failed'
    else:
        subprocess.run([str(build / names[2])], check=True)
        report['binaries'] = {str(build / name): sha256(build / name) for name in names}
        for folder in ('experiments/03_disk_system/native', 'src/disk_bench/native'):
            for p in (ROOT / folder).rglob('*'):
                if p.is_file() and p.suffix in ('.cpp', '.hpp', '.txt'):
                    sources[str(p.resolve())] = sha256(p)
        build_files = [build / 'CMakeCache.txt', build / 'symphonyqg.build.log', patch]
        build_files += list(build.rglob('flags.make')) + list(build.rglob('link.txt'))
        if (build / 'build.ninja').is_file():
            build_files.append(build / 'build.ninja')
        report['build_files'] = {str(p): sha256(p) for p in build_files}
        report['status'] = 'verified'
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(output)
    return 0 if report['status'] == 'verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
