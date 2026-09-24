"""Audit all curve points against raw traces and matched experiment inputs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=Path)
a=p.parse_args()
with (a.directory/'source_data.csv').open() as f: rows=list(csv.DictReader(f))
assert len(rows)==12
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
    return h.hexdigest()
checks=[]
for ds in ('gist','dbpedia'):
    frozen=json.loads((a.directory/f'{ds}_frozen.json').read_text())
    docs=[]
    for v in ('baseline',frozen['curve_lowdim']):
        data=[r for r in rows if r['dataset']==ds and r['variant']==v]
        assert {int(r['width']) for r in data}=={16,49,96}
        artifact=Path(data[0]['artifact'])
        doc=json.loads(artifact.read_text());docs.append(doc)
        argv=json.loads(artifact.with_suffix('.argv.json').read_text())
        flags=dict(zip(argv[1::2],argv[2::2]))
        assert sha(flags['--query'])==doc['query_split_sha256']
        assert sha(flags['--ours-graph'])==doc['source_graph_sha256']
        assert sha(argv[0])==doc['native_binary_sha256']
        trace=[json.loads(line) for line in Path(doc['query_trace_path']).read_text().splitlines()]
        assert len(trace)==2700
        assert doc['dataset']==ds and doc['workers']==32
        assert doc['direct_io'] and doc['native_aio'] and not doc['whole_payload_in_memory']
        for r in data:
            points=[t for t in trace if t['search_width']==int(r['width'])]
            assert len(points)==900 and len({t['query_id'] for t in points})==900
            assert abs(sum(t['recall_at_10'] for t in points)/900-float(r['recall']))<1e-9
            summary=next(s for s in doc['summary_rows'] if s['search_width']==int(r['width']))
            assert float(r['qps'])==summary['qps'] and summary['beam_width']==1
        if v!='baseline':
            assert all(t['db1_checks']==0 for t in trace)
            assert doc['adaptive_route_dim']==int(v.split('_')[0][1:])
            assert not doc.get('adaptive_route_norms')
        checks.append(dict(dataset=ds,variant=v,points=3,queries_per_point=900,status='pass'))
    for key in ('query_split_sha256','query_order_sha256','source_graph_sha256','native_binary_sha256','search_dram_budget_gib','cache_mode'):
        assert docs[0][key]==docs[1][key],key
(a.directory/'data_audit.json').write_text(json.dumps(checks,indent=2))
print('PASS: 12 points, per-width query trace recalls, actual query/graph/binary hashes, matched inputs, native direct I/O.')
