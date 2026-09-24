"""Read-only consistency audit of a completed budget series."""
import argparse
import json
import hashlib
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('directory', type=Path)
a = p.parse_args()
rows = json.loads((a.directory/'summary.json').read_text())
def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(8*1024*1024), b''):
            h.update(b)
    return h.hexdigest()
for key in ('query_hash', 'graph_hash', 'binary_hash'):
    assert len({r[key] for r in rows}) == 1, f'mismatched {key}'
for r in rows:
    doc = json.loads(Path(r['artifact']).read_text())
    argv = json.loads(Path(r['artifact']).with_suffix('.argv.json').read_text())
    flags = dict(zip(argv[1::2], argv[2::2]))
    assert digest(flags['--query']) == r['query_hash']
    if r is rows[0]:
        assert digest(argv[0]) == r['binary_hash']
        assert digest(flags['--ours-graph']) == r['graph_hash']
    trace = [json.loads(s) for s in Path(doc['query_trace_path']).read_text().splitlines()]
    assert len(trace) == r['query_count'] == 900
    assert len({t['query_id'] for t in trace}) == 900
    recall = sum(t['recall_at_10'] for t in trace)/len(trace)
    assert abs(recall-r['recall']) < 1e-9
    assert doc['direct_io'] and doc['native_aio']
    assert not doc['whole_payload_in_memory'] and not doc['whole_graph_in_memory']
    assert doc['workers'] == 32
    assert doc['resident_bytes'] + doc['cache_bytes'] + doc['worker_scratch_bytes'] + doc['codebook_bytes'] <= r['budget_gib']*2**30
    if r['variant'] != 'baseline':
        assert all(t['db1_checks'] == 0 for t in trace)
        assert doc['adaptive_route_dim'] == 256 and doc['adaptive_route_keep'] == 32
        assert doc['adaptive_route_ratio'] == 1
        assert not doc.get('adaptive_route_norms')
    print('PASS', r['budget_gib'], r['variant'], 'RSS within nominal budget:', r['peak_rss_mib'] <= r['budget_gib']*1024)
print('Consistency audit passed; not a proof of hard-budget enforcement or unbiased timing.')
