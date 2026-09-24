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
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src/disk_bench"))
from official_sources import verify_source
from memory_runner import run_measured, check_environment
from storage_precondition import prepare_search
from protocol import SYSTEM_PRIMARY_BUDGET_GIB, PROTOCOL_ID

REPO = Path(__file__).resolve().parents[3]
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
    exe = args.build_root.resolve() / folder
    root = args.work_dir
    index = (args.index_dir or root) / 'index'
    base, query = root / 'base.bin', root / 'query.bin'
    common = ['--data_type', 'float', '--dist_fn', 'l2']
    build = ['--data_path', str(base), '--index_path_prefix', str(index),
             '-R', str(args.degree or 64), '-L', str(args.build_width),
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
    index_root = args.index_dir or root
    sample, nav = index_root/'nav_sample', index_root/'nav_index'
    partition = index_root/'index_partition.bin'
    return [
        [str(exe/'tests/build_disk_index'), *common, *build],
        [str(exe/'tests/utils/gen_random_slice'), 'float', str(base), str(sample), str(args.nav_sample)],
        [str(exe/'tests/build_memory_index'), *common, '--data_path', str(sample),
         '--index_path_prefix', str(nav), '-R', str(args.nav_degree),
         '-L', str(args.nav_build_width), '--alpha', '1.2', '-T', str(args.build_threads)],
        [str(exe/'graph_partition/partitioner'), '--index_file', str(index_root/'index_disk.index'),
         '--data_type', 'float', '--gp_file', str(partition),
         '-T', str(args.build_threads), '--ldg_times', str(args.partition_iterations)],
        [str(exe/'tests/utils/index_relayout'), str(index_root/'index_disk.index'), str(partition)],
        [str(exe/'tests/search_disk_index'), *common, *search,
         '--disk_file_path', str(index_root/'index_partition_tmp.index'),
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
    p.add_argument('--build-root', type=Path, default=REPO/'baselines/builds')
    p.add_argument('--index-dir', type=Path, help='Reuse a prepared official index directory; never rebuild it')
    p.add_argument('--prepare-only', action='store_true', help='Build indexes only; no search timing')
    p.add_argument('--warmup-query', type=Path, help='Independent validation fvecs for external native warmup')
    p.add_argument('--warmup-count', type=int, default=100)
    p.add_argument('--cpu-affinity', help='comma-separated CPUs')
    p.add_argument('--numa-node', type=int)
    p.add_argument('--dataset', choices=('gist','bigann10m','agnews','dbpedia'))
    p.add_argument('--search-memory-gib', type=float, default=SYSTEM_PRIMARY_BUDGET_GIB,
                   help='planned search RAM budget in GiB (default: 03 shared 4 GiB); not an OS cap under rss policy')
    p.add_argument('--memory-policy', choices=('rss', 'observe', 'cgroup'), default='rss',
                   help='rss: shared peak-RSS budget admission; observe/cgroup: explicit separate diagnostics')
    p.add_argument('--cgroup-parent', type=Path, default=os.environ.get('QG05_CGROUP_PARENT'))
    p.add_argument('--degree', type=int, default=None)
    p.add_argument('--build-width', type=int, default=400)
    p.add_argument('--build-memory-gib', type=float, default=8)
    p.add_argument('--build-threads', type=int, default=3)
    p.add_argument('--pq-bytes', type=int, default=32)
    p.add_argument('--widths', type=int, nargs='+', default=[10,20,40,60,100,160,240,400,580])
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


def memory_options(args):
    """Keep planned RAM admission distinct from observation and legacy OS caps."""
    if not math.isfinite(args.search_memory_gib) or args.search_memory_gib <= 0:
        raise ValueError('search memory budget must be finite and positive')
    budget = int(args.search_memory_gib * (1 << 30))
    if budget <= 0:
        raise ValueError('search memory budget must contain at least one byte')
    return dict(rss_budget=args.memory_policy == 'rss', observe_only=args.memory_policy == 'observe',
                budget_bytes=None if args.memory_policy == 'observe' else budget,
                cgroup_parent=args.cgroup_parent if args.memory_policy == 'cgroup' else None)


def main(argv=None):
    args = parser().parse_args(argv)
    memory = memory_options(args)
    args.base, args.query, args.work_dir = (p.resolve() for p in (args.base, args.query, args.work_dir))
    with args.base.open('rb') as f:
        dim, = struct.unpack('<i', f.read(4))
    if dim <= 0 or args.base.stat().st_size % (4 + 4*dim):
        raise ValueError('invalid base fvecs geometry')
    n = args.base.stat().st_size // (4 + 4*dim)
    if args.degree is None:
        args.degree = 48 if args.method == 'Starling-Disk' and args.dataset == 'gist' else 64
    if args.method == 'Starling-Disk' and dim*4 + (args.degree+1)*4 > 4096:
        raise ValueError('unsupported official Starling FP32 node layout (>4096 bytes)')
    warmup_evidence = None
    if args.warmup_query:
        sys.path.insert(0, str(REPO/'src'))
        from disk_bench.system03 import verify_disjoint
        warmup_evidence = verify_disjoint(args.warmup_query, args.query, args.warmup_count)
    if args.index_dir:
        args.index_dir = args.index_dir.resolve()
        prepared = json.loads((args.index_dir/'official_run.json').read_text())
        if prepared.get('method') != args.method or prepared.get('inputs', {}).get(str(args.base)) != sha256(args.base):
            raise ValueError('reused official index base/method mismatch')
        for key in ('degree','build_width','pq_bytes','inline_pq','rearrange','nav_sample','nav_degree','nav_build_width','partition_iterations'):
            if prepared['parameters'].get(key) != getattr(args,key):
                raise ValueError('reused official index configuration mismatch: '+key)
        files = prepared.get('index_files', {})
        if not files or any(sha256(Path(name)) != digest for name,digest in files.items()):
            raise ValueError('reused official index file hash mismatch')
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
    build_plan = [] if args.index_dir else plan[:-1]
    searches = []
    if not args.prepare_only:
        for width in args.widths:
            command = list(plan[-1])
            start, end = command.index('-L') + 1, command.index('-W')
            command[start:end] = [str(width)]
            searches.append(command)
    plan = build_plan + searches
    if not args.execute:
        print(json.dumps({'formal_ready': False, 'commands': plan, 'memory_policy': args.memory_policy,
                          'planned_budget_bytes': memory['budget_bytes'] if memory['rss_budget'] else None}, indent=2))
        return 0
    folder = METHODS[args.method]
    memory_environment = (check_environment(args.cgroup_parent, memory['budget_bytes'])
                          if args.memory_policy == 'cgroup' else
                          {'status': 'planned_ram_rss_admission' if memory['rss_budget'] else 'native_memory_observation',
                           'memory_enforcement': 'none', 'protocol_id': PROTOCOL_ID,
                           'planned_budget_bytes': memory['budget_bytes']})
    lock = verify_source(REPO, folder)
    head = lock['commit']
    for cmd in plan:
        if not Path(cmd[0]).is_file():
            raise RuntimeError(f'missing official executable: {cmd[0]}')
    args.work_dir.mkdir(parents=True, exist_ok=False)
    if not args.index_dir:
        fvecs_to_bin(args.base, args.work_dir/'base.bin')
    if args.warmup_query:
        fvecs_to_bin(args.warmup_query, args.work_dir/'warmup.bin')
    _, qdim = fvecs_to_bin(args.query, args.work_dir/'query.bin')
    if qdim != dim:
        raise ValueError('query dimension differs from base')
    metadata = {'method': args.method, 'source_commit': head, 'formal_ready': False,
                'status': 'running', 'commands': plan, 'source_patches': lock.get('patches', []),
                'parameters': {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                'inputs': {str(p): sha256(p) for p in (args.base, args.query)},
                'binaries': {c[0]: sha256(c[0]) for c in plan},
                'memory_environment': memory_environment,
                'note': 'Official CLI diagnostics; shared RAM/RSS budget admission by default. Formal throughput, query and algorithm acceptance still required.'}
    manifest = args.work_dir/'official_run.json'
    manifest.write_text(json.dumps(metadata, indent=2)+'\n')
    env = dict(os.environ, OMP_NUM_THREADS=str(args.build_threads), MKL_NUM_THREADS='1')
    search_evidence = []
    for i, cmd in enumerate(plan):
        logfile = args.work_dir/f'{i:02d}_{Path(cmd[0]).name}.log'
        print(f'[{i+1}/{len(plan)}] {cmd[0]} -> {logfile}', flush=True)
        if i >= len(build_plan):
            width = int(cmd[cmd.index('-L')+1])
            preparation = args.work_dir/f'L{width}.storage_precondition.json'
            prepare_search(cmd, preparation, args.method)
            metadata['storage_precondition_path'] = str(preparation)
            metadata['storage_precondition_sha256'] = sha256(preparation)
            manifest.write_text(json.dumps(metadata, indent=2)+'\n')
            evidence = run_measured(cmd, evidence_path=args.work_dir/f'L{width}.resources.json',
                                    log_path=logfile, cwd=REPO,
                                    env=dict(env, OMP_NUM_THREADS=str(args.workers), QG05_OFFICIAL_TRACE_PREFIX=str(args.work_dir/"query_stats"),
                                             **({'QG03_WARMUP_BIN':str(args.work_dir/'warmup.bin'), 'QG03_WARMUP_COUNT':str(args.warmup_count)} if args.warmup_query else {})),
                                    cpu_affinity=tuple(map(int,args.cpu_affinity.split(','))) if args.cpu_affinity else None,
                                    numa_node=args.numa_node,
                                    **memory)
            returncode = evidence['exit_code']
            metadata['search_resource_status'] = evidence['status']
            metadata['search_resource_path'] = str(args.work_dir/f'L{width}.resources.json')
            metadata['search_resource_sha256'] = sha256(args.work_dir/f'L{width}.resources.json')
            metadata['budget_admitted'] = evidence['budget_admitted']
            search_evidence.append(dict(width=width, resource_path=metadata['search_resource_path'], resource_sha256=metadata['search_resource_sha256'], status=evidence['status']))
            failed = evidence['status'] != 'completed'
        else:
            with logfile.open('x') as log:
                returncode = subprocess.run(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
            failed = returncode != 0
        if failed:
            metadata.update(status='failed', failed_command=i, returncode=returncode)
            manifest.write_text(json.dumps(metadata, indent=2)+'\n')
            raise RuntimeError(f'official command failed ({returncode}): see {logfile}')
    metadata['searches'] = search_evidence
    metadata['independent_warmup'] = warmup_evidence
    metadata['index_files'] = {str(p.resolve()):sha256(p) for p in (args.index_dir or args.work_dir).iterdir()
                               if p.is_file() and p.name.startswith(('index','nav_'))}
    metadata['query_stats'] = {str(p): sha256(p) for p in args.work_dir.glob('query_stats_*.jsonl')}
    if not args.prepare_only and len(metadata['query_stats']) != len(args.widths):
        raise RuntimeError('official per-query statistics missing')
    metadata.update(status='index_prepared' if args.prepare_only else 'done', results={str(p): sha256(p) for p in args.work_dir.glob('result*') if p.is_file()})
    manifest.write_text(json.dumps(metadata, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
