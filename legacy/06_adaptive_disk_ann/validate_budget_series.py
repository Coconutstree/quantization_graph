"""Serial single-repeat budget validation; logs and combined CSV are retained."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--budgets', default='0.375,0.5,1,2')
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
out = root/'results/disk_environment/06_adaptive_disk_ann/agnews'/time.strftime('budget_validation_%Y%m%d_%H%M%S')
out.mkdir(parents=True)
rows = []
print('SERIES', out, flush=True)
for budget in a.budgets.split(','):
    cmd = [sys.executable, 'legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py',
           '--queries', '900', '--query-offset', '100', '--budget-gib', budget,
           '--variants', 'baseline,d256_k32_t100']
    print('START budget', budget, flush=True)
    with (out/f'B{budget}.log').open('w') as log:
        proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        run_dir = None
        for line in proc.stdout:
            print(line, end='', flush=True)
            log.write(line)
            log.flush()
            if run_dir is None and line.startswith(str(root)):
                run_dir = Path(line.strip())
        code = proc.wait()
    if code:
        raise SystemExit(f'Budget {budget} failed: {code}; see {out}')
    for variant in ('baseline', 'd256_k32_t100'):
        doc = json.loads((run_dir/f'{variant}.json').read_text())
        for row in doc['summary_rows']:
            rows.append(dict(budget_gib=float(budget), variant=variant,
                recall=row['recall'], qps=row['qps'], io=row['io_requests_per_query'],
                peak_rss_mib=row['peak_rss_bytes']/2**20,
                resident_mib=doc['resident_bytes']/2**20,
                cache_bytes=doc['cache_bytes'], cache_nodes=doc['cache_nodes'],
                scratch_bytes=doc['worker_scratch_bytes'],
                query_count=row['query_count'],
                query_hash=doc['query_split_sha256'], graph_hash=doc['source_graph_sha256'],
                binary_hash=doc['native_binary_sha256'], artifact=str(run_dir/f'{variant}.json')))
    with (out/'summary.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out/'summary.json').write_text(json.dumps(rows, indent=2))
    print('DONE budget', budget, flush=True)
print('COMPLETE', out, flush=True)
