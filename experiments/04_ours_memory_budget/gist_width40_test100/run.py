"""Serial, resumable 40-width test100 memory/performance experiment.
Never promotes automatically to test800. Existing experiments remain immutable.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import measurement
spec = importlib.util.spec_from_file_location('fixed_driver', HERE.parent/'gist_fixed_factors_budget/run.py')
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)
s = auto.s
sys.path.insert(0, str(HERE))
OLD = s.OUT
OUT = ROOT/'results/04_ours_memory_budget/gist_width40_test100'
WIDTHS = s.WIDTHS
BUDGETS = [538, 616, 768, 1024, 1536]
MIB = 2**20
s.pilot.measurement = measurement


def dump(path, value):
    s.dump(path, value)


def disk_command(folder, budget, cache, rep):
    original = json.loads((ROOT/'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/DiskANN-PQ-Disk/command.json').read_text())
    flags = s.flags(original)
    binary = ROOT/'baselines/diskann/target/release/qgraph05_diskann_port'
    base = s.base('pilot')
    for key in ('--query','--groundtruth','--query-split-sha256','--query-order','--query-order-sha256','--input-manifest'):
        flags[key] = base[key]
    flags.update({'--shared-graph': str(ROOT/'artifacts/graphs/gist/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin'),
                  '--run-id': 'native_integration_gist_width40_test100', '--repeat-id': str(rep),
                  '--result-json': str(folder/'result.json'), '--query-trace': str(folder/'queries.jsonl'),
                  '--integration-widths': ','.join(map(str, WIDTHS)), '--integration-beams': '1',
                  '--cache-mode': cache, '--search-dram-budget-gib': str(budget/1024),
                  '--native-binary-sha256': s.sha(binary), '--parity-mode': 'external'})
    return [str(binary)] + [item for pair in flags.items() for item in pair]


def validate_disk(folder):
    result = json.loads((folder/'result.json').read_text())
    rows = result['summary_rows']
    assert [r['search_width'] for r in rows] == WIDTHS
    traces = s.pilot.traces(folder)
    assert len(traces) == 100*len(WIDTHS)
    assert len({(r['search_width'], r['query_id']) for r in traces}) == len(traces)
    for row in rows:
        q = [r for r in traces if r['search_width'] == row['search_width']]
        assert len(q) == 100 and row['query_count'] == 100 and row['qps'] > 0
        assert abs(sum(r['recall_at_10'] for r in q)/100-row['recall']) < 1e-8
        assert abs(sum(r['bytes_read'] for r in q)/100-row['bytes_read_per_query']) < .01
    dump(folder/'acceptance.json', dict(passed=True, queries=len(traces), files={str(p.relative_to(folder)): s.sha(p) for p in folder.rglob('*') if p.is_file() and p.name != 'acceptance.json'}))


def disk_run(folder, budget, cache, rep):
    if (folder/'acceptance.json').exists():
        for name, value in json.loads((folder/'acceptance.json').read_text())['files'].items():
            assert s.sha(folder/name) == value
        return
    if folder.exists():
        raise RuntimeError(f'Unaccepted run preserved: {folder}')
    folder.mkdir(parents=True)
    command = disk_command(folder, budget, cache, rep)
    s.pilot.prepare_search(command, folder/'storage_precondition.json')
    # Native budget selects shared cache; no claim of an enforced RSS/AS cap.
    measurement.measure(command, folder, budget=None)
    validate_disk(folder)


def refresh():
    import report
    try:
        report.main()
    except Exception as exc:
        dump(OUT/'report_error.json', dict(error=str(exc), traceback=traceback.format_exc(), updated=time.time()))
        print('Report generation failed; raw accepted measurements are preserved:', exc, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--wait-for-pid', type=int)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    lock = (OUT/'run.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert len(WIDTHS) == len(set(WIDTHS)) == 40
    definition = dict(dataset='gist', queries=100, widths=WIDTHS, warmup_per_width=100,
        workers=32, beam=1, repeats=1, ours_budgets_mib=BUDGETS,
        diskann_configs=[['c0',2048]],
        ours_binary_sha256=s.sha(s.BIN), diskann_binary_sha256=s.sha(ROOT/'baselines/diskann/target/release/qgraph05_diskann_port'),
        calibration_sha256=s.sha(OLD/'calibration/lock.json'),
        memory_semantics='Ours: RLIMIT_AS hard cap; DiskANN: native cache sizing budget, uncapped process. Compare measured RSS, not budget labels.',
        storage='sequential O_DIRECT pre-read per run; device cache uncontrolled',
        full800_enabled=False)
    if (OUT/'experiment.json').exists():
        assert json.loads((OUT/'experiment.json').read_text()) == definition
    dump(OUT/'experiment.json', definition)
    # Keep the existing calibrated lock and test selection; write no old outputs.
    base = s.base('pilot')
    for flag in ('--query', '--groundtruth','--query-order','--input-manifest','--disk-index-dir'):
        assert Path(base[flag]).exists(), (flag,base[flag])
    s.OUT = OUT
    s.base = lambda split: dict(base)
    probe = disk_command(OUT/'unused', 2048, 'c0', 1)
    for flag in ('--shared-graph','--disk-index-dir'):
        assert Path(s.flags(probe)[flag]).exists(), (flag,s.flags(probe)[flag])
    if args.prepare_only:
        print(json.dumps(definition, indent=2)); return
    if args.wait_for_pid:
        blocker = Path(f'/proc/{args.wait_for_pid}')
        while blocker.exists():
            dump(OUT/'state.json', dict(status='waiting_for_resources', blocker_pid=args.wait_for_pid, pid=os.getpid(), updated=time.time()))
            refresh()
            time.sleep(30)
    configs = [('diskann_c0', 'disk', 2048, 'c0'), ('ours_resident','ours',2048,'resident')]
    configs += [(f'ours_{b}', 'ours',b,'auto') for b in reversed(BUDGETS)]
    # The already accepted c0 curve is reused; no DiskANN cache sweep.
    try:
        for rep in range(1, definition['repeats'] + 1):
            order = configs if rep == 1 else list(reversed(configs))
            # Reference must precede Ours variants to validate every width/query.
            ref = OUT/'runs'/f'ours_resident_r{rep}'
            if rep == 2:
                s.execute(ref,'pilot',2048*MIB,'resident',rep,WIDTHS)
            for name, method, budget, policy in order:
                folder = OUT/'runs'/f'{name}_r{rep}'
                dump(OUT/'state.json', dict(status='running', run=folder.name, pid=os.getpid(), updated=time.time()))
                print(time.strftime('%F %T'),folder.name,flush=True)
                if method == 'ours':
                    s.execute(folder,'pilot',budget*MIB,policy,rep,WIDTHS,reference=ref if policy=='auto' else None)
                else:
                    disk_run(folder,budget,policy,rep)
                refresh()
        dump(OUT/'state.json',dict(status='completed', updated=time.time(), full800_enabled=False))
        refresh()
    except BaseException as exc:
        dump(OUT/'state.json',dict(status='failed', error=str(exc), traceback=traceback.format_exc(), updated=time.time()))
        refresh()
        raise


if __name__ == '__main__':
    # Historical implementation above remains importable for forensic replay.
    # Executing this established CLI now uses the validated 256-page policy.
    _spec = importlib.util.spec_from_file_location('current_auto_entry', HERE.parent/'gist_auto_inflight256.py')
    _current = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_current)
    _current.compatibility_main()

