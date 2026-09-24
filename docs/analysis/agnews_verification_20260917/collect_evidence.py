"""Read existing AGNews evidence and probe resource prerequisites; no benchmark sweep."""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SUITE = REPO / 'experiments/05_disk_system_fair'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def command(args):
    if not shutil.which(args[0]):
        return {'command': args, 'status': 'missing_executable'}
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return {'command': args, 'returncode': p.returncode,
                'stdout': p.stdout, 'stderr': p.stderr}
    except subprocess.TimeoutExpired:
        return {'command': args, 'status': 'timeout'}


def environment():
    files = ['/proc/self/cgroup', '/sys/fs/cgroup/cgroup.controllers',
             '/sys/fs/cgroup/cgroup.subtree_control']
    data = {'uid': os.getuid(), 'allowed_cpus': sorted(os.sched_getaffinity(0)),
            'numactl': shutil.which('numactl'),
            'cgroup_mount': [l for l in Path('/proc/self/mountinfo').read_text().splitlines()
                             if ' - cgroup2 ' in l],
            'files': {p: Path(p).read_text() for p in files},
            'nodes': {p.name: (p/'cpulist').read_text().strip()
                      for p in Path('/sys/devices/system/node').glob('node[0-9]*')},
            'systemd_user': command(['systemctl', '--user', 'show', '--property=ControlGroup']),
            'storage': command(['findmnt', '-T', str(REPO/'work/05_disk_system_fair/disk_root'),
                                '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'])}
    spec = importlib.util.spec_from_file_location('memory_runner_audit', SUITE/'memory_runner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        data['cgroup_check'] = module.check_environment('/sys/fs/cgroup', 4*(1<<30))
    except (module.MemoryEnvironmentError, OSError) as e:
        data['cgroup_check'] = {'status': 'blocked_environment', 'reason': str(e)}
    return data


def historical():
    result = []
    root = REPO/'results/disk_environment'
    runs = ['agnews_05c_rerun_20260901_152210', 'fix_w32_diskpayload_symphony_20260831_140957']
    fields = ['search_dram_budget_gib', 'resident_bytes', 'codebook_bytes',
              'worker_scratch_bytes', 'cache_bytes', 'peak_rss_bytes',
              'cpu_affinity', 'numa_node', 'measurement_scope', 'storage_cache_protocol',
              'memory_accounting_complete', 'memory_enforcement', 'implementation_fingerprint',
              'native_binary_sha256', 'source_index_manifest_sha256']
    for run in runs:
        base = root/'.formal_runs/runs'/run
        csv_path = root/'03_system_fair/agnews/csv'/f'formal_test_rows_{run}.csv'
        with csv_path.open() as f:
            rows = list(csv.DictReader(f))
        preflight = json.loads((base/'manifests/preflight.json').read_text())
        for method in sorted({r['method'] for r in rows}):
            subset = [r for r in rows if r['method'] == method]
            best = max(subset, key=lambda r: float(r['qps']))
            artifact_path = Path(best['artifact_path'])
            a = json.loads(artifact_path.read_text())
            result.append({'run_id': run, 'method': method, 'csv_path': str(csv_path),
                'csv_sha256': sha(csv_path), 'artifact_path': str(artifact_path),
                'artifact_sha256': sha(artifact_path), 'row_count': len(subset),
                'workers': sorted({r['workers'] for r in subset}),
                'repeat_ids': sorted({r['repeat_id'] for r in subset}),
                'best_qps': float(best['qps']), 'recall_at_best_qps': float(best['recall']),
                'metadata': {k: a.get(k) for k in fields},
                'preflight': {k: preflight.get(k) for k in ['disk_root','disk_profile','rotational','storage_medium']}})
    return result


def current_sources():
    old = json.loads((REPO/'docs/analysis/disk_protocol_fix_20260917/verification.json').read_text())
    sources = {name: {'sha256': sha(SUITE/name), 'matches_previous_verification': sha(SUITE/name)==value}
               for name, value in old['source_sha256'].items()}
    ports = json.loads((SUITE/'ports.local.json').read_text())['ports']
    registry = {}
    for name, p in ports.items():
        if not name.startswith('05c:'):
            continue
        binary = REPO/p['command'][0]
        actual = sha(binary) if binary.is_file() else None
        registry[name] = {'status': p['status'], 'binary_path': str(binary),
                          'actual_sha256': actual, 'pinned_sha256': p.get('binary_sha256'),
                          'pin_matches': actual==p.get('binary_sha256') if p.get('binary_sha256') else None,
                          'blocked_reason': p.get('blocked_reason')}
    return {'sources': sources, 'registry': registry}


if __name__ == '__main__':
    mode = sys.argv[1]
    output = HERE/f'{mode}_evidence.json'
    if output.exists():
        raise SystemExit(f'refusing to overwrite {output}')
    data = {'time_unix': time.time(), 'mode': mode, 'full_experiments_run': False}
    if mode in ('sandbox', 'host'):
        data.update(environment())
    elif mode == 'existing':
        data.update(historical=historical(), **current_sources())
    else:
        raise SystemExit('mode must be sandbox, host, or existing')
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    print(output)
    print(json.dumps(data.get('cgroup_check', data.get('registry')), ensure_ascii=False, indent=2))
