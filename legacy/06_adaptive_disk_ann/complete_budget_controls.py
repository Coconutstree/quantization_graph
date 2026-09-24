"""Complete same-budget original-Ours test controls after frozen selection.

Never reselect. Existing controls and selected-baseline runs are reused rather
than repeated. Each missing control is measured once on the same test slice.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory', type=Path)
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
test = json.loads((a.directory/'test.json').read_text())
dest = a.directory/'budget_controls.json'
controls = json.loads(dest.read_text()) if dest.exists() else []
for r in test['selected_results']:
    b = r['budget_gib']
    if any(c['budget_gib'] == b for c in controls):
        continue
    reference = test['reference']
    if r['variant'] == 'baseline':
        c = r.copy()
    elif reference['budget_gib'] == b:
        c = reference.copy()
    else:
        print('START same-budget control', b, flush=True)
        cmd = [sys.executable, 'legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py',
               '--queries','900','--query-offset','100','--budget-gib',str(b),'--variants','baseline']
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        (a.directory/f'control_B{b}.log').write_text(proc.stdout+proc.stderr)
        if proc.returncode:
            raise RuntimeError(proc.stdout+proc.stderr)
        folder = Path(next(s for s in proc.stdout.splitlines() if s.startswith(str(root))))
        artifact = folder/'baseline.json'
        d = json.loads(artifact.read_text())
        s = d['summary_rows'][0]
        c = dict(status='done', split='test', variant='baseline', budget_gib=b,
                 recall=s['recall'], qps=s['qps'], peak_rss_bytes=s['peak_rss_bytes'],
                 resident_bytes=d['resident_bytes'], cache_bytes=d['cache_bytes'], io=s['io_requests_per_query'],
                 accounted_bytes=sum(d[k] for k in ('resident_bytes','codebook_bytes','worker_scratch_bytes','cache_bytes')),
                 artifact=str(artifact))
    candidate = json.loads(Path(r['artifact']).read_text())
    control = json.loads(Path(c['artifact']).read_text())
    for key in ('query_split_sha256','query_order_sha256','source_graph_sha256','native_binary_sha256','workers','cache_mode'):
        assert candidate[key] == control[key], f'control mismatch: {key}'
    assert candidate['summary_rows'][0]['query_count'] == control['summary_rows'][0]['query_count'] == 900
    controls.append(c)
    dest.write_text(json.dumps(controls, indent=2))
    print('DONE control',b,'recall',c['recall'],'qps',round(c['qps'],2),flush=True)
print('COMPLETE',dest,flush=True)
