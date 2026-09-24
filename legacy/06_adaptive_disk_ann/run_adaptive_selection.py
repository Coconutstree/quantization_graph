"""Profile on queries [0,100), freeze selection, then evaluate [100,1000).

These slices have appeared in earlier development: this validates the workflow,
not an untouched final test set. One run per configuration, no hard RAM claim.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from select_route_profile import select_profile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--budgets', default='0.3,0.375,0.5')
p.add_argument('--max-recall-drop', type=float, default=.001)
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
out = root/'results/disk_environment/06_adaptive_disk_ann/agnews'/time.strftime('adaptive_selection_%Y%m%d_%H%M%S')
out.mkdir(parents=True)
(out/'config.json').write_text(json.dumps(vars(a), indent=2))
print('OUTPUT', out, flush=True)
signatures = {}

def run(variant, budget, split):
    count, offset = (100, 0) if split == 'validation' else (900, 100)
    cmd = [sys.executable, 'legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py',
           '--queries', str(count), '--query-offset', str(offset), '--budget-gib', str(budget),
           '--variants', variant]
    print('START', split, budget, variant, flush=True)
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    (out/f'{split}_B{budget}_{variant}.log').write_text(result.stdout+result.stderr)
    paths = [line for line in result.stdout.splitlines() if line.startswith(str(root))]
    if not paths:
        raise RuntimeError(result.stdout+result.stderr)
    folder = Path(paths[0])
    if result.returncode:
        log = (folder/f'{variant}.log').read_text()
        if 'worker scratch require' not in log or 'budget is' not in log:
            raise RuntimeError(f'unexpected failure: {folder}\n{log}')
        print('REJECT memory admission', budget, variant, flush=True)
        return dict(status='admission_rejected', split=split, budget_gib=budget,
                    variant=variant, artifact=str(folder))
    doc = json.loads((folder/f'{variant}.json').read_text())
    signature = (doc['query_split_sha256'], doc['source_graph_sha256'], doc['native_binary_sha256'])
    assert signatures.setdefault(split, signature) == signature, 'inconsistent inputs'
    if split == 'test':
        assert signature[1:] == signatures['validation'][1:], 'graph or binary changed after selection'
    r = doc['summary_rows'][0]
    assert r['query_count'] == count
    row = dict(status='done', split=split, budget_gib=budget, variant=variant,
               recall=r['recall'], qps=r['qps'], peak_rss_bytes=r['peak_rss_bytes'],
               accounted_bytes=doc['resident_bytes']+doc['codebook_bytes']+doc['cache_bytes']+doc['worker_scratch_bytes'],
               resident_bytes=doc['resident_bytes'], cache_bytes=doc['cache_bytes'],
               io=r['io_requests_per_query'], artifact=str(folder/f'{variant}.json'))
    print('DONE', split, budget, variant, 'recall',row['recall'],'qps',round(row['qps'],2),flush=True)
    return row

reference = run('baseline', .5, 'validation')
assert reference['status'] == 'done'
profiles = []
budgets = [float(b) for b in a.budgets.split(',')]
for budget in budgets:
    for variant in ('baseline', 'd64_k32_t100', 'd128_k32_t100', 'd256_k32_t100', 'd512_k32_t100'):
        row = reference if budget == .5 and variant == 'baseline' else run(variant, budget, 'validation')
        profiles.append(row)
        (out/'validation.json').write_text(json.dumps(profiles, indent=2))
decisions = [select_profile(profiles, b, reference['recall'], a.max_recall_drop) for b in budgets]
# Freeze before any test query executes. Never replace a failed selection with a
# different profile after viewing test results.
with (out/'frozen_selection.json').open('x') as f:
    json.dump(dict(reference=reference, decisions=decisions, hard_limit='unavailable: no delegated writable cgroup',
                   reused_development_queries=True), f, indent=2)
print('FROZEN', [(d['budget_gib'], d['selected']['variant'] if d['selected'] else None) for d in decisions], flush=True)
test_reference = run('baseline', .5, 'test')
tests = []
(out/'test.json').write_text(json.dumps(dict(reference=test_reference, selected_results=tests), indent=2))
for d in decisions:
    if d['selected'] is None:
        continue
    b, variant = d['budget_gib'], d['selected']['variant']
    r = test_reference.copy() if b == .5 and variant == 'baseline' else run(variant, b, 'test')
    r['recall_target'] = test_reference['recall']-a.max_recall_drop
    r['passes_test_recall'] = r['status'] == 'done' and r['recall']+1e-12 >= r['recall_target']
    r['passes_test_memory'] = r['status'] == 'done' and max(r['accounted_bytes'],r['peak_rss_bytes']) <= b*2**30
    tests.append(r)
    (out/'test.json').write_text(json.dumps(dict(reference=test_reference, selected_results=tests), indent=2))
print('COMPLETE', out, flush=True)
