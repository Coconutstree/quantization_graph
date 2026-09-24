"""GIST planned RAM budget + RSS admission; optional separate legacy cgroup mode."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import sys
import time

from cgroup_memory import dump, inspect, measure as measure_cgroup
from rss_memory import measure as measure_rss

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'results/04_ours_memory_budget/gist_ram_budget_rss'
MEMORY_MODE = 'rss_budget'
MIB = 2**20
BUDGETS = [512, 2048, 4096, 8192]
WIDTHS = list(range(10, 31)) + list(range(40, 101, 10)) + list(range(140, 581, 40))
PROFILE = ROOT / 'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'
OURS = ROOT / 'work/ours_memory_budget/gist_auto_inflight256/ours_gist_auto_inflight256'
DISK = ROOT / 'baselines/diskann/target/release/qgraph05_diskann_port'


def measure(command, folder, root, budget, timeout=86400):
    fn = measure_rss if MEMORY_MODE == 'rss_budget' else measure_cgroup
    return fn(command, folder, root, budget, timeout)


def measured_peak(mem):
    return mem['observed_peak_rss_bytes'] if MEMORY_MODE == 'rss_budget' else mem['cgroup_peak_bytes']


def read(p):
    return json.loads(Path(p).read_text())


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def flags(c):
    return dict(zip(c[1::2], c[2::2]))


def freeze(path, value):
    if path.exists():
        if read(path) != value:
            raise RuntimeError('frozen inputs/configuration changed: ' + str(path))
    else:
        dump(path, value)


def vectors(path):
    raw = Path(path).read_bytes()
    stride = 4 * (struct.unpack_from('<I', raw)[0] + 1)
    if len(raw) % stride:
        raise ValueError('invalid vector file')
    return [raw[i:i+stride] for i in range(0, len(raw), stride)]


def sources(method):
    if method == 'ours':
        return flags(read(ROOT / 'results/04_ours_memory_budget/dynamic_records/gist/runs/hot_dynamic_r1/command.json'))
    return flags(read(ROOT / 'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/DiskANN-PQ-Disk/command.json'))


def inputs(split):
    if split in ('train', 'tune'):
        directory = ROOT / 'results/04_ours_memory_budget/hybrid_tuning/splits' / split
        q, gt = directory / 'validation_query.fvecs', directory / 'validation_gt.ivecs'
        order = OUT / 'inputs/order100.u32'
    elif split == 'paper':
        directory = ROOT / 'artifacts/query_splits/gist/shared'
        q, gt = directory / 'test_query.fvecs', directory / 'test_gt.ivecs'
        order = ROOT / 'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/optimized_order.u32'
    else:
        q, gt, order = (OUT / 'inputs' / n for n in ('query.fvecs', 'gt.ivecs', 'order100.u32'))
    return {'--query': str(q), '--groundtruth': str(gt), '--query-order': str(order),
            '--query-order-sha256': sha(order), '--query-split-sha256': sha(q)}


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    directory = OUT / 'inputs'
    directory.mkdir(exist_ok=True)
    order = struct.pack('<100I', *range(100))
    p = directory / 'order100.u32'
    if p.exists() and p.read_bytes() != order:
        raise RuntimeError('input order changed')
    p.write_bytes(order)
    full = inputs('paper')
    ids = struct.unpack('<800I', Path(full['--query-order']).read_bytes())[:100]
    for flag, name in [('--query', 'query.fvecs'), ('--groundtruth', 'gt.ivecs')]:
        rows = vectors(full[flag])
        content = b''.join(rows[i] for i in ids)
        p = directory / name
        if p.exists() and p.read_bytes() != content:
            raise RuntimeError('engineering input changed')
        p.write_bytes(content)
    sets = {s: set(vectors(inputs(s)['--query'])) for s in ('train', 'tune', 'paper')}
    if any(sets[a] & sets[b] for a, b in [('train', 'tune'), ('train', 'paper'), ('tune', 'paper')]):
        raise RuntimeError('train/tune/test vector overlap')
    profile_command = flags(read(PROFILE / 'command.json'))
    if vectors(profile_command['--query']) != vectors(inputs('train')['--query']):
        raise RuntimeError('hot ranking does not use frozen train split')
    build = read(ROOT / 'work/ours_memory_budget/gist_auto_inflight256/build.json')
    if sha(OURS) != build['binary_sha256']:
        raise RuntimeError('Ours binary changed')
    for p, h in build['sources'].items():
        if sha(p) != h:
            raise RuntimeError('Ours frozen source changed: ' + p)
    # Index identities are frozen independently; differing graphs are disclosed.
    artifacts = {}
    for method in ('ours', 'diskann'):
        f = sources(method)
        for k in ('--disk-index-dir', '--locality-layout-dir'):
            if k in f:
                directory = Path(f[k])
                for p in directory.iterdir():
                    if p.is_file():
                        artifacts[str(p)] = sha(p)
    definition = dict(dataset='gist', budgets_mib=BUDGETS, widths=WIDTHS, workers=32, beam=1,
        warmup_queries=100, engineering_queries=100, paper_queries=800, repeats=1,
        memory_mode=MEMORY_MODE,
        memory=('planned RAM budget; observed RSS admission; no OS cap' if MEMORY_MODE=='rss_budget'
                else 'memory.max=B; memory.swap.max=0; unlimited RLIMIT_AS'),
        process_lifecycle='fresh process per width; native warmup before measured batch',
        storage='O_DIRECT required; no full-file pre-read; device cache uncontrolled',
        diskann_candidates='c0 plus native standard with 25%, 50%, 100% calibrated spare bytes',
        tuning_objective='sum log(best QPS at Recall>=0.90/0.95/0.98); missing thresholds first',
        input_hashes={s: {k: sha(v) for k, v in inputs(s).items() if k in ('--query','--groundtruth','--query-order')}
                      for s in ('train', 'tune', 'engineering', 'paper')},
        binaries={str(p): sha(p) for p in (OURS, DISK)}, index_artifacts=artifacts,
        command_templates={m:sources(m) for m in ('ours','diskann')},
        profile={n: sha(PROFILE / n) for n in ('graph_scores.f64','payload_scores.f64')},
        implementation={p.name: sha(p) for p in HERE.glob('*.py')})
    if (OUT / 'experiment.json').exists():
        freeze(OUT / 'experiment.json', definition)
    dump(OUT / 'prepared.json', definition)
    audit = {m: {k: v for k, v in sources(m).items()
                     if any(x in k for x in ('graph', 'manifest', 'seed', 'config'))}
             for m in ('ours', 'diskann')}
    for m in ('ours', 'diskann'):
        directory = Path(sources(m)['--disk-index-dir'])
        for name in ('index.meta.json','index.manifest.json'):
            if (directory/name).exists():
                audit[m][name] = read(directory/name)
    audit['note'] = 'Original method-specific graph/format retained; do not claim identical graph files. Construction parameters are copied verbatim from index metadata.'
    dump(OUT / 'index_audit.json', audit)
    return definition


def command(method, folder, split, budget, widths, config=None, calibration=False):
    f = sources(method)
    for k in list(f):
        if k.startswith(('--memory-', '--routing-', '--auto-')):
            del f[k]
    binary = OURS if method == 'ours' else DISK
    f.update(inputs(split))
    f.update({'--run-id': 'native_integration_gist_same_ram', '--repeat-id': '1',
              '--native-binary-sha256': sha(binary), '--workers': '32', '--warmup-queries': '100',
              '--integration-widths': ','.join(map(str, widths)), '--integration-beams': '1',
              '--parity-mode': 'external', '--result-json': str(folder / 'result.json'),
              '--query-trace': str(folder / 'queries.jsonl'), '--search-dram-budget-gib': str(budget / 2**30)})
    if method == 'ours':
        f.update({'--cache-mode': 'c0', '--memory-policy': 'auto', '--memory-cache-bytes': 'auto',
                  '--memory-profile-dir': str(PROFILE), '--memory-stats-dir': str(folder / 'memory_stats'),
                  '--auto-plan-output': str(folder / 'memory_plan.json'),
                  '--auto-calibration-file': str(OUT / 'calibration/lock.json')})
        if calibration:
            f['--auto-calibration'] = '1'
            f.update(config)
    else:
        f['--shared-graph'] = str(ROOT / 'artifacts/graphs/gist/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin')
        f['--cache-mode'] = (config or {}).get('cache_mode', 'c0')
        f['--search-dram-budget-gib'] = str((config or {}).get('native_budget_bytes', budget) / 2**30)
    return [str(binary)] + [x for pair in f.items() for x in pair]


def validate(folder, widths, count):
    rows = read(folder / 'result.json')['summary_rows']
    traces = [json.loads(line) for line in (folder / 'queries.jsonl').read_text().splitlines()]
    if [r['search_width'] for r in rows] != widths or len(traces) != count * len(widths):
        raise ValueError('incomplete curve/traces')
    if len({(r['search_width'], r['query_id']) for r in traces}) != len(traces):
        raise ValueError('duplicate trace keys')
    for r in rows:
        q = [t for t in traces if t['search_width'] == r['search_width']]
        if len(q) != count or r['query_count'] != count or not math.isfinite(r['qps']) or r['qps'] <= 0:
            raise ValueError('invalid measured batch')
        for summary, trace in [('recall','recall_at_10'), ('bytes_read_per_query','bytes_read'), ('io_requests_per_query','io_requests')]:
            if summary in r and abs(r[summary] - sum(t[trace] for t in q) / count) > 1e-6:
                raise ValueError('trace/summary mismatch: ' + summary)
        if any(len(t.get('result_ids', [])) != 10 for t in q):
            raise ValueError('missing top10 result IDs')
    return rows


def execute(root, folder, method, split, budget, widths, config=None, calibration=False):
    cmd = command(method, folder, split, budget, widths, config, calibration)
    if (folder / 'status.json').exists():
        status = read(folder / 'status.json')
        if read(folder / 'command.json') != cmd:
            raise RuntimeError('resume command differs')
        for name, digest in status['files'].items():
            if sha(folder / name) != digest:
                raise RuntimeError('resume evidence modified')
        return status
    if folder.exists():
        raise RuntimeError('preserve incomplete run: ' + str(folder))
    (folder / 'memory_stats').mkdir(parents=True)
    dump(OUT / 'state.json', dict(status='running', run=str(folder), updated=time.time()))
    print(str(folder.relative_to(OUT)), flush=True)
    mem = measure(cmd, folder, root, budget)
    status = dict(classification=mem['classification'], method=method, budget_bytes=budget, widths=widths)
    if mem['classification'] == 'completed':
        try:
            validate(folder, widths, 800 if split == 'paper' else 100)
            if method == 'ours':
                plan = read(folder / 'memory_plan.json')
                if plan['budget_bytes'] != budget or plan['admission_bytes'] > budget:
                    raise ValueError('allocation exceeds input budget')
                spec = importlib.util.spec_from_file_location('routing_stats_reader', HERE.parent / 'gist_fixed_factors_budget/driver_base.py')
                s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
                route = s.stats(folder)
                traces = [json.loads(l) for l in (folder / 'queries.jsonl').read_text().splitlines()]
                for w in widths:
                    st = read(folder / 'memory_stats' / f'L{w}.json')
                    if st['cache_reserved_bytes'] > plan['optional_cache_bytes']:
                        raise ValueError('records exceed allocation')
                    for field, op, routing in [('bytes_read','read_bytes','bytes'), ('io_requests','io_requests','reads'), ('sectors_4k','read_pages','reads')]:
                        actual = sum(t[field] for t in traces if t['search_width'] == w)
                        expected = sum(v[op] for v in st['operations'].values()) + route.get(w, {}).get(routing, 0)
                        if actual != expected:
                            raise ValueError('physical I/O attribution mismatch')
                dump(folder / 'routing_stats.json', route)
        except Exception as exc:
            status.update(classification='validation_failure', error=str(exc))
    status['files'] = {str(p.relative_to(folder)): sha(p) for p in folder.rglob('*') if p.is_file()}
    dump(folder / 'status.json', status)
    if status['classification'] not in ('completed','budget_exceeded','swap_observed','cgroup_oom','allocation_failure','admission_rejected'):
        raise RuntimeError(str(status))
    return status


def calibrate(root):
    lock = OUT / 'calibration/lock.json'
    if lock.exists():
        return read(lock)
    observations = []
    for mode, cap in [('resident', 0), ('paged', MIB), ('hot_dynamic', 64*MIB)]:
        folder = OUT / 'calibration' / mode
        status = execute(root, folder, 'ours', 'train', 2**31, WIDTHS,
                         {'--memory-policy': mode, '--memory-cache-bytes': str(cap)}, True)
        if status['classification'] != 'completed':
            raise RuntimeError('physical calibration failed')
        mem, p = read(folder / 'memory_measurement.json'), read(folder / 'memory_plan.json')
        # This small hot calibration fits within the static hot set. Static records
        # are read into RAM at setup. Do not subtract unfilled dynamic capacity.
        resident = p['factors_bytes'] + (p['codes_bytes'] if mode != 'paged' else 0)
        if mode == 'hot_dynamic':
            cache = [read(x) for x in (folder/'memory_stats').glob('L*.json')]
            # Small fixed static calibration; retain any dynamic allocation in the
            # conservative residual rather than assuming all reserved bytes resident.
            dynamic = [c.get('dynamic_records') or {} for c in cache]
            if all(not d.get('capacity_nodes', 0) for d in dynamic):
                resident += min(c['cache_reserved_bytes'] for c in cache)
        observations.append(dict(mode=mode, peak=measured_peak(mem),
                                 definitely_resident_bytes=resident,
                                 fixed_observation=max(0, measured_peak(mem)-resident)))
    value = dict(locked=True, scope=MEMORY_MODE+'; conservative residual calibration',
                 binary_sha256=sha(OURS), fixed_bytes=math.ceil((max(o['fixed_observation'] for o in observations)+8*MIB)/MIB)*MIB,
                 reserve_bytes=64*MIB, full_query_extra_bytes=8*MIB, observations=observations)
    freeze(lock, value)
    return value


def tune(root):
    target = OUT / 'diskann_selection.json'
    if target.exists():
        return read(target)
    selections = {}
    for mib in BUDGETS:
        budget = mib*MIB
        zero = OUT / 'validation' / str(mib) / 'c0'
        st = execute(root, zero, 'diskann', 'tune', budget, WIDTHS, {'cache_mode':'c0'})
        if st['classification'] != 'completed':
            selections[str(mib)] = dict(cache_mode='c0', native_budget_bytes=budget,
                                        validation_status=st['classification'], minimum_cache=True)
            continue
        c0rows = read(zero / 'result.json')['summary_rows']
        peak = measured_peak(read(zero / 'memory_measurement.json'))
        # Native formula: PQ + codebook + 8 MiB/worker + node bytes.
        metadata = read(Path(sources('diskann')['--disk-index-dir']) / 'index.meta.json')
        native_fixed = metadata['resident_bytes'] + metadata['codebook_bytes'] + 32*8*MIB
        spare = max(0, budget - peak - 64*MIB)
        candidates = [({'cache_mode':'c0','native_budget_bytes':budget}, c0rows)]
        for num, den in [(1,4),(1,2),(1,1)]:
            cfg = dict(cache_mode='standard', native_budget_bytes=min(budget, native_fixed+spare*num//den))
            if cfg['native_budget_bytes'] <= native_fixed:
                continue
            folder = OUT / 'validation' / str(mib) / f'standard_{num}_{den}'
            st = execute(root, folder, 'diskann', 'tune', budget, WIDTHS, cfg)
            if st['classification'] == 'completed':
                candidates.append((cfg, read(folder / 'result.json')['summary_rows']))
        def score(item):
            cfg, rows = item
            qps = [max((r['qps'] for r in rows if r['recall'] >= t), default=0) for t in (.90,.95,.98)]
            return (sum(q>0 for q in qps), sum(math.log(q) for q in qps if q>0),
                    -int(cfg['cache_mode'] != 'c0'), -cfg['native_budget_bytes'])
        selections[str(mib)] = max(candidates, key=score)[0]
    freeze(target, selections)
    return selections


def parity(stage):
    fields = ['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates']
    checked = 0
    for w in WIDTHS:
        reference = None
        for mib in BUDGETS:
            folder = OUT / stage / str(mib) / 'ours' / f'L{w}'
            if read(folder / 'status.json')['classification'] != 'completed':
                continue
            rows = {r['query_id']: r for r in (json.loads(l) for l in (folder / 'queries.jsonl').read_text().splitlines())}
            if reference is None:
                reference = rows
            else:
                if rows.keys() != reference.keys() or any(rows[k][f] != reference[k][f] for k in rows for f in fields):
                    raise RuntimeError('Ours cross-budget result parity failed')
                checked += len(rows)
    return dict(passed=True, query_comparisons=checked, fields=fields)


def sweep(root, stage, selections):
    completed = 0
    for mib in BUDGETS:
        for method in ('ours', 'diskann'):
            for w in WIDTHS:
                folder = OUT / stage / str(mib) / method / f'L{w}'
                cfg = selections[str(mib)] if method == 'diskann' else None
                status = execute(root, folder, method, stage, mib*MIB, [w], cfg)
                completed += status['classification'] == 'completed'
                if method == 'diskann' and status['classification'] in ('budget_exceeded','swap_observed','cgroup_oom','allocation_failure','admission_rejected') and cfg['cache_mode'] != 'c0':
                    # Diagnostic only: never substitute c0 QPS into the frozen curve.
                    execute(root, OUT/'diagnostics'/stage/str(mib)/f'c0_L{w}', method, stage,
                            mib*MIB, [w], dict(cache_mode='c0',native_budget_bytes=mib*MIB))
    if not completed:
        raise RuntimeError('no successful engineering/paper points; cannot pass gate')
    dump(OUT / stage / 'completed.json', dict(**parity(stage), accepted_points=completed,
                                             attempted_points=len(BUDGETS)*2*len(WIDTHS)))


def main():
    global OUT, MEMORY_MODE
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    p.add_argument('--memory-mode', choices=('rss_budget','cgroup'), default='rss_budget')
    p.add_argument('--cgroup-root', type=Path, default=None)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--run', action='store_true', help='calibrate, freeze, engineering gate, then paper')
    a = p.parse_args(); MEMORY_MODE = a.memory_mode
    if MEMORY_MODE == 'rss_budget' and a.cgroup_root is not None:
        p.error('--cgroup-root requires --memory-mode cgroup')
    if MEMORY_MODE == 'cgroup' and a.cgroup_root is None:
        p.error('--memory-mode cgroup requires --cgroup-root')
    OUT = (a.output or (ROOT / 'results/04_ours_memory_budget' /
                       ('gist_ram_budget_rss' if MEMORY_MODE=='rss_budget' else 'gist_same_ram_cgroup_v2'))).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check = (inspect(a.cgroup_root) if MEMORY_MODE=='cgroup' else
                 dict(ready=True, memory_mode=MEMORY_MODE, memory_enforcement='none'))
        dump(OUT / 'memory_preflight.json', check)
        definition = prepare()
        if not a.run:
            print(json.dumps(check, indent=2)); return
        if not check['ready']:
            dump(OUT / 'state.json', dict(status='blocked_cgroup', details=check, performance_started=False))
            raise RuntimeError('No writable delegated cgroup; refusing unconstrained execution')
        freeze(OUT / 'experiment.json', definition)
        if MEMORY_MODE == 'cgroup':
            probe = OUT / 'cgroup_probe'
            if not (probe / 'memory_measurement.json').exists():
                m = measure([sys.executable, '-c', 'import time; x=bytearray(16*1024**2); time.sleep(.2)'], probe, a.cgroup_root, 64*MIB)
                if m['classification'] != 'completed':
                    raise RuntimeError('cgroup positive probe failed')
            if read(probe/'memory_measurement.json')['classification'] != 'completed':
                raise RuntimeError('saved cgroup positive probe failed')
            negative = OUT / 'cgroup_oom_probe'
            if not (negative / 'memory_measurement.json').exists():
                m = measure([sys.executable, '-c', 'x=bytearray(256*1024**2)'], negative, a.cgroup_root, 64*MIB)
                if m['classification'] != 'cgroup_oom':
                    raise RuntimeError('cgroup enforcement probe did not OOM')
            if read(negative/'memory_measurement.json')['classification'] != 'cgroup_oom':
                raise RuntimeError('saved cgroup enforcement probe failed')
        calibrate(a.cgroup_root)
        selections = tune(a.cgroup_root)
        freeze(OUT/'performance_lock.json',dict(
            experiment_sha256=sha(OUT/'experiment.json'),
            calibration_sha256=sha(OUT/'calibration/lock.json'),
            diskann_selection_sha256=sha(OUT/'diskann_selection.json')))
        sweep(a.cgroup_root, 'engineering', selections)
        sweep(a.cgroup_root, 'paper', selections)
        import report
        report.generate(OUT)
        dump(OUT / 'state.json', dict(status='completed', updated=time.time()))


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        if not isinstance(exc, SystemExit):
            current = read(OUT/'state.json') if (OUT/'state.json').exists() else {}
            if current.get('status') != 'blocked_cgroup':
                dump(OUT/'state.json',dict(status='failed',error=repr(exc),updated=time.time()))
        raise
