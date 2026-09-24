#!/usr/bin/env python3
"""Serial 03 source runs; pending methods remain explicit missing results.

Each method has an immutable run ID. This queue never assembles an incomplete
four-method table or changes registry admission. Existing completed phases are
revalidated by the standard orchestrator on resume.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from disk_bench.native_contract import atomic_write_json

from disk_bench.system03 import PROTOCOL, WIDTHS, storage_errors

METHODS = [('Ours-Disk', 'ours'), ('DiskANN-PQ-Disk', 'diskann'),
           ('Starling-Disk', 'starling'), ('AiSAQ-Disk', 'aisaq')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tag', required=True)
    p.add_argument('--ports', type=Path, required=True)
    p.add_argument('--disk-root', type=Path, required=True)
    p.add_argument('--cpu-affinity', required=True)
    p.add_argument('--numa-node', type=int, required=True)
    p.add_argument('--datasets', default='gist,bigann10m,agnews,dbpedia')
    p.add_argument('--prepare-only', action='store_true',
                   help='Export/reuse indexes only; no validation or test timing')
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    datasets = a.datasets.split(',')
    if not datasets or datasets[0] != 'gist' or len(set(datasets)) != len(datasets):
        p.error('process GIST first, without duplicate datasets')
    if any(d not in ('gist', 'bigann10m', 'agnews', 'dbpedia') for d in datasets):
        p.error('unknown dataset')
    if not a.tag.replace('_', '').replace('-', '').isalnum():
        p.error('invalid tag')
    folder = ROOT / 'results/diagnostics' / ('03_trusted_queue_' + a.tag)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ports = a.ports.resolve()
        registry_bytes = ports.read_bytes()
        registry = json.loads(registry_bytes)['ports']
        config = dict(tag=a.tag, datasets=datasets, methods=[m for m, _ in METHODS],
                      ports=str(ports), ports_sha256=hashlib.sha256(registry_bytes).hexdigest(),
                      disk_root=str(a.disk_root.resolve()), cpu_affinity=a.cpu_affinity,
                      numa_node=a.numa_node, workers=32, budget_gib=4, repeats=1,
                      cache_mode='standard', parameter_selection='user_fixed_no_performance_tuning',
                      protocol_id=PROTOCOL, fixed_beam=4, search_widths=list(WIDTHS),
                      ours_cache_allocation='graph_first', memory_policy='rss',
                      warmup_policy='independent_validation_prefix_100',
                      process_scope='one_method_one_width_one_process',
                      full_comparison_requires_all_methods=True)
        config['method_versions'] = {m: registry['05c:'+m].get('source_commit', '') for m, _ in METHODS}
        config_file = folder / 'configuration.json'
        if config_file.exists() and json.loads(config_file.read_text()) != config:
            raise ValueError('queue configuration changed; use a new tag')
        atomic_write_json(config_file, config)
        env = dict(os.environ)
        for key in ('QG05_FAST', 'QG05_FAST_WIDTHS', 'QG05_SKIP_EXTERNAL_PARITY'):
            env.pop(key, None)
        env['PATH'] = str(ROOT / 'work/tools/numactl/root/usr/bin') + os.pathsep + env['PATH']
        jobs = []
        for dataset in datasets:
            for method, slug in METHODS:
                port = registry['05c:' + method]
                jobs.append(dict(dataset=dataset, method=method, budget_gib=4,
                                 run_id=f'{dataset}_03_fixed_{slug}_{a.tag}',
                                 status='pending' if port['status'] == 'ready' else 'blocked_registry',
                                 reason=port.get('blocked_reason', '') if port['status'] != 'ready' else '',
                                 phases=[]))
                if method == 'Starling-Disk' and dataset in ('agnews', 'dbpedia') and port['status'] == 'ready':
                    jobs[-1].update(status='unsupported_layout',
                        reason='official FP32 node exceeds 4 KiB; no input/layout modification')
        state = dict(configuration=config, status='running' if a.execute else 'planned',
                     full_comparison_ready=False, jobs=jobs)
        def save():
            state['updated_unix'] = time.time()
            atomic_write_json(folder / 'status.json', state)
        save()
        if a.execute and not a.prepare_only:
            errors = storage_errors(a.disk_root)
            if errors:
                state.update(status='blocked_storage', blockers=errors)
                save()
                print('; '.join(errors), file=sys.stderr)
                return 2
        for job in jobs:
            if job['status'] != 'pending':
                continue
            base = [sys.executable, str(ROOT / 'experiments/03_disk_system/run.py'),
                    '--datasets', job['dataset'], '--methods', job['method'],
                    '--run-id', job['run_id'], '--ports', str(ports),
                    '--disk-root', config['disk_root'], '--disk-profile', 'auto',
                    '--workers', '32', '--repeats', '1', '--cpu-affinity', a.cpu_affinity,
                    '--numa-node', str(a.numa_node), '--search-dram-budget-gib', '4',
                    '--cache-mode', 'standard', '--fixed-beam', '4',
                    '--ours-cache-allocation', 'graph_first', '--system03-fixed']
            for phase in (('export',) if a.prepare_only else ('export', 'run')):
                command = base + ['--phase', phase] + (['--method-pipeline'] if phase == 'run' else [])
                entry = dict(phase=phase, command=command, status='planned')
                job['phases'].append(entry)
                save()
                if not a.execute:
                    continue
                if ports.read_bytes() != registry_bytes:
                    raise ValueError('registry changed while running queue')
                attempt = folder / job['run_id']
                attempt.mkdir(exist_ok=True)
                log = attempt / f'{phase}_{time.time_ns()}.log'
                entry.update(status='running', log=str(log), started_unix=time.time())
                job['status'] = 'running'
                save()
                print(job['run_id'], phase, flush=True)
                with log.open('x') as output:
                    code = subprocess.call(command, cwd=ROOT, env=env,
                                           stdout=output, stderr=subprocess.STDOUT)
                entry.update(status='passed' if code == 0 else 'failed',
                             exit_code=code, finished_unix=time.time())
                if code:
                    job.update(status='failed', reason=f'{phase} failed; see {log}')
                    save()
                    break
                save()
            else:
                job['status'] = ('index_prepared' if a.prepare_only else 'admitted_source_run') if a.execute else 'planned'
            save()
        if a.execute:
            expected = 'index_prepared' if a.prepare_only else 'admitted_source_run'
            complete = all(j['status'] in (expected, 'unsupported_layout') for j in jobs)
            state['status'] = ('indexes_prepared' if a.prepare_only else 'sources_completed') if complete else 'finished_with_missing_results'
            # Full-list aggregation must independently validate every source.
            state['full_comparison_ready'] = False
            if complete and not a.prepare_only:
                from disk_bench.system03_report import publish
                report = publish(folder, jobs, config, ROOT/'results')
                state.update(full_comparison_ready=True, report=str(report))
            save()
            return 0 if complete else 2
        print(folder / 'status.json')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
