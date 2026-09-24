#!/usr/bin/env python3
"""Run official AiSAQ/Starling CLIs with recorded storage compatibility patches on fvecs inputs.

This integration runner preserves native logs and result-ID/distance binaries.
It does not manufacture the per-query evidence required by run_disk_suite.py.
Use a fresh work directory for every run; --execute is required to run commands.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
from official_sources import verify_source

REPO = Path(__file__).resolve().parents[2]
METHODS = {"AiSAQ-Disk": "aisaq", "Starling-Disk": "starling"}


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def fvecs_to_bin(source: Path, target: Path):
    with source.open('rb') as f, target.open('xb') as out:
        raw = f.read(4)
        if len(raw) != 4:
            raise ValueError(f'empty fvecs: {source}')
        dim, = struct.unpack('<i', raw)
        if dim <= 0 or source.stat().st_size % (4 + dim * 4):
            raise ValueError(f'invalid fvecs: {source}')
        n = source.stat().st_size // (4 + dim * 4)
        if n >= 2**32:
            raise ValueError('official bin format requires uint32 row count')
        out.write(struct.pack('<II', n, dim))
        f.seek(0)
        for _ in range(n):
            if struct.unpack('<i', f.read(4))[0] != dim:
                raise ValueError('mixed fvecs dimensions')
            out.write(f.read(dim * 4))
    return n, dim


def commands(args, n: int):
    folder = METHODS[args.method]
    exe = REPO / 'baselines/builds' / folder
    root = args.work_dir
    index = root / 'index'
    base, query = root / 'base.bin', root / 'query.bin'
    common = ['--data_type', 'float', '--dist_fn', 'l2']
    build = ['--data_path', str(base), '--index_path_prefix', str(index),
             '-R', str(args.degree), '-L', str(args.build_width),
             '-B', str(args.pq_bytes * n / 1024**3),
             '-M', str(args.build_memory_gib), '-T', str(args.build_threads)]
    search = ['--index_path_prefix', str(index), '--query_file', str(query),
              '--result_path', str(root / 'result'), '-K', str(args.k),
              '-L', *map(str, args.widths), '-W', str(args.beam),
              '-T', str(args.workers), '--num_nodes_to_cache', str(args.node_cache)]
    if folder == 'aisaq':
        return [
            [str(exe/'apps/build_disk_index'), *common, *build, '--QD', str(args.pq_bytes),
             '--use_aisaq', '--inline_pq', str(args.inline_pq),
             *(['--rearrange'] if args.rearrange else [])],
            [str(exe/'apps/search_disk_index'), *common, *search, '--use_aisaq',
             '--pq_read_io_engine', 'aio', '-V', str(args.vector_beam),
             '--pq_cache_size', args.pq_cache,
             '--pq_read_page_cache_size', args.pq_page_cache],
        ]
    sample, nav = root/'nav_sample', root/'nav_index'
    partition = root/'index_partition.bin'
    return [
        [str(exe/'tests/build_disk_index'), *common, *build],
        [str(exe/'tests/utils/gen_random_slice'), 'float', str(base), str(sample), str(args.nav_sample)],
        [str(exe/'tests/build_memory_index'), *common, '--data_path', str(sample),
         '--index_path_prefix', str(nav), '-R', str(args.nav_degree),
         '-L', str(args.nav_build_width), '--alpha', '1.2', '-T', str(args.build_threads)],
        [str(exe/'graph_partition/partitioner'), '--index_file', str(root/'index_disk.index'),
         '--data_type', 'float', '--gp_file', str(partition),
         '-T', str(args.build_threads), '--ldg_times', str(args.partition_iterations)],
        [str(exe/'tests/utils/index_relayout'), str(root/'index_disk.index'), str(partition)],
        [str(exe/'tests/search_disk_index'), *common, *search,
         '--disk_file_path', str(root/'index_partition_tmp.index'),
         '--mem_index_path', str(nav), '--mem_L', str(args.nav_width),
         '--use_page_search', '1', '--use_ratio', str(args.page_ratio), '--use_sq', '0'],
    ]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--method', choices=METHODS, required=True)
    p.add_argument('--base', type=Path, required=True, help='float32 .fvecs')
    p.add_argument('--query', type=Path, required=True, help='float32 .fvecs')
    p.add_argument('--work-dir', type=Path, required=True, help='new directory on target disk')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--degree', type=int, default=64)
    p.add_argument('--build-width', type=int, default=400)
    p.add_argument('--build-memory-gib', type=float, default=8)
    p.add_argument('--build-threads', type=int, default=3)
    p.add_argument('--pq-bytes', type=int, default=32)
    p.add_argument('--widths', type=int, nargs='+', default=[20, 40, 80, 160, 320])
    p.add_argument('--beam', type=int, default=4)
    p.add_argument('--workers', type=int, default=32)
    p.add_argument('--k', type=int, default=10)
    p.add_argument('--node-cache', type=int, default=0)
    p.add_argument('--inline-pq', type=int, default=-1)
    p.add_argument('--rearrange', action='store_true')
    p.add_argument('--vector-beam', type=int, default=1)
    p.add_argument('--pq-cache', default='0')
    p.add_argument('--pq-page-cache', default='0')
    p.add_argument('--nav-sample', type=float, default=0.01)
    p.add_argument('--nav-degree', type=int, default=48)
    p.add_argument('--nav-build-width', type=int, default=128)
    p.add_argument('--nav-width', type=int, default=32)
    p.add_argument('--partition-iterations', type=int, default=8)
    p.add_argument('--page-ratio', type=float, default=1.0)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    args.base, args.query, args.work_dir = (p.resolve() for p in (args.base, args.query, args.work_dir))
    with args.base.open('rb') as f:
        dim, = struct.unpack('<i', f.read(4))
    if dim <= 0 or args.base.stat().st_size % (4 + 4*dim):
        raise ValueError('invalid base fvecs geometry')
    n = args.base.stat().st_size // (4 + 4*dim)
    if not 1 <= args.pq_bytes <= min(dim, 512):
        raise ValueError('PQ bytes must be in [1, min(dim,512)]')
    if n <= args.degree or args.k > n or min(args.widths) < args.k:
        raise ValueError('require n > degree and search widths >= k')
    if min(args.build_threads, args.workers, args.beam, args.vector_beam) < 1:
        raise ValueError('thread counts and beam widths must be positive')
    if args.vector_beam > args.beam or not 0 < args.nav_sample <= 1 or not 0 < args.page_ratio <= 1:
        raise ValueError('invalid beam/sample/page ratio')
    if args.nav_width < 1 or args.inline_pq < -1 or args.inline_pq > args.degree:
        raise ValueError('invalid navigation width or inline PQ count')
    if args.pq_page_cache != '0' and not args.rearrange and args.method == 'AiSAQ-Disk':
        raise ValueError('official AiSAQ PQ page cache requires --rearrange')
    plan = commands(args, n)
    if not args.execute:
        print(json.dumps({'formal_ready': False, 'commands': plan}, indent=2))
        return 0
    folder = METHODS[args.method]
    lock = verify_source(REPO, folder)
    head = lock['commit']
    for cmd in plan:
        if not Path(cmd[0]).is_file():
            raise RuntimeError(f'missing official executable: {cmd[0]}')
    args.work_dir.mkdir(parents=True, exist_ok=False)
    fvecs_to_bin(args.base, args.work_dir/'base.bin')
    _, qdim = fvecs_to_bin(args.query, args.work_dir/'query.bin')
    if qdim != dim:
        raise ValueError('query dimension differs from base')
    metadata = {'method': args.method, 'source_commit': head, 'formal_ready': False,
                'status': 'running', 'commands': plan, 'source_patches': lock.get('patches', []),
                'parameters': {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                'inputs': {str(p): sha256(p) for p in (args.base, args.query)},
                'binaries': {c[0]: sha256(c[0]) for c in plan},
                'note': 'Official CLI diagnostics. No enforced search DRAM bound or formal per-query artifact.'}
    manifest = args.work_dir/'official_run.json'
    manifest.write_text(json.dumps(metadata, indent=2)+'\n')
    env = dict(os.environ, OMP_NUM_THREADS=str(args.build_threads), MKL_NUM_THREADS='1')
    for i, cmd in enumerate(plan):
        logfile = args.work_dir/f'{i:02d}_{Path(cmd[0]).name}.log'
        print(f'[{i+1}/{len(plan)}] {cmd[0]} -> {logfile}', flush=True)
        with logfile.open('x') as log:
            result = subprocess.run(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            metadata.update(status='failed', failed_command=i, returncode=result.returncode)
            manifest.write_text(json.dumps(metadata, indent=2)+'\n')
            raise RuntimeError(f'official command failed ({result.returncode}): see {logfile}')
    metadata.update(status='done', results={str(p): sha256(p) for p in args.work_dir.glob('result*') if p.is_file()})
    manifest.write_text(json.dumps(metadata, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
