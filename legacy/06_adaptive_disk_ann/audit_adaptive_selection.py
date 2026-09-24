"""Read-only audit of selection, trace metrics, and matched-budget controls."""
import argparse
import json
from pathlib import Path
from select_route_profile import select_from_archive

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory', type=Path)
a = p.parse_args()
frozen = json.loads((a.directory/'frozen_selection.json').read_text())
test = json.loads((a.directory/'test.json').read_text())
controls = json.loads((a.directory/'budget_controls.json').read_text())
choices = {d['budget_gib']:d for d in frozen['decisions']}
expected = {b for b,d in choices.items() if d['selected'] is not None}
assert {r['budget_gib'] for r in test['selected_results']} == expected
assert {r['budget_gib'] for r in controls} == expected
for b,d in choices.items():
    recomputed = select_from_archive(a.directory,b)
    assert recomputed['selected'] == d['selected'], 'frozen choice is not reproducible from validation'
for r in test['selected_results']:
    c = next(x for x in controls if x['budget_gib']==r['budget_gib'])
    assert choices[r['budget_gib']]['selected']['variant'] == r['variant']
    docs=[]
    for row in (r,c):
        doc = json.loads(Path(row['artifact']).read_text())
        trace = [json.loads(x) for x in Path(doc['query_trace_path']).read_text().splitlines()]
        assert len(trace)==900 and len({x['query_id'] for x in trace})==900
        assert abs(sum(x['recall_at_10'] for x in trace)/900-row['recall'])<1e-9
        assert doc['direct_io'] and doc['native_aio']
        assert not doc['whole_payload_in_memory'] and not doc['whole_graph_in_memory']
        if row['variant'] != 'baseline':
            assert doc['adaptive_route_dim'] == int(row['variant'].split('_')[0][1:])
            assert doc['adaptive_route_keep'] == 32 and doc['adaptive_route_ratio'] == 1
            assert not doc.get('adaptive_route_norms')
            assert all(x['db1_checks'] == 0 for x in trace)
        assert max(row['accounted_bytes'],row['peak_rss_bytes']) <= row['budget_gib']*2**30
        docs.append(doc)
    for key in ('query_split_sha256','query_order_sha256','source_graph_sha256','native_binary_sha256','workers','cache_mode','search_dram_budget_gib'):
        assert docs[0][key]==docs[1][key],key
    tol=json.loads((a.directory/'config.json').read_text())['max_recall_drop']
    assert r['recall']+1e-12 >= c['recall']-tol, 'frozen choice fails same-budget test recall'
    print('PASS budget',r['budget_gib'],r['variant'],'QPS',round(r['qps'],2),'control',round(c['qps'],2))
print('PASS: frozen choices, query traces, matched-budget controls, recall and memory accounting. Not a hard-RAM certificate.')
