"""Replay local queue traversal using official per-node distances, diagnostic only."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'baselines/svs/python'))
import svs


def main():
    parent = ROOT / 'results/diagnostics/03_other_methods_admission_20260921'
    pairs = [json.loads(s) for s in (parent / 'og_search/pairs.jsonl').read_text().splitlines()]
    qids = sorted({r['query_id'] for r in pairs if r['official'] != r['native']})
    out = parent / 'og_oracle_replay'
    out.mkdir(exist_ok=False)
    suffix = '05_disk_system_fair/05C_disk_system_fair/agnews/OG-LVQ-DiskPort/hybrid_disk'
    disk = ROOT / 'work/05_disk_system_fair/disk_root' / suffix
    official = ROOT / 'artifacts/indexes/legacy_disk_store' / suffix / 'official_svs'
    query = ROOT / 'artifacts/query_splits/agnews/shared/validation_query.fvecs'
    binary = ROOT / 'build/disk/native/qgraph05_og_lvq_distance_probe'
    reference = svs.Vamana(str(official / 'config'), svs.GraphLoader(str(official / 'graph')),
                           svs.LVQLoader(str(official / 'data')), svs.DistanceType.L2, num_threads=1)
    queries = np.ascontiguousarray(svs.read_vecs(str(query)), dtype=np.float32)
    results = []
    for qid in qids:
        table = out / f'distances_{qid}.f32'
        distances = np.fromiter((reference.get_distance(i, queries[qid]) for i in range(reference.size)),
                                dtype='<f4', count=reference.size)
        distances.tofile(table)
        for row in (r for r in pairs if r['query_id'] == qid):
            width = row['width']
            trace = out / f'q{qid}_w{width}.csv'
            subprocess.run([str(binary), str(disk), str(query), str(trace), str(table), str(qid), str(width)],
                           env=dict(os.environ, QG05_REFERENCE_MMAP='1'), check=True)
            actual = list(map(int, next(csv.reader(trace.open()))))[2:]
            reference.search_window_size = width
            expected, _ = reference.search(queries[qid], 10)
            expected = list(map(int, np.asarray(expected).reshape(-1)))
            if expected != row['official']:
                raise ValueError('official reference differs from saved result')
            results.append(dict(query_id=qid, width=width, original_native=row['native'],
                                official=expected, oracle_replay=actual, equal=actual == expected))
        print(f'query {qid}: {sum(r["equal"] for r in results if r["query_id"] == qid)}/9 match', flush=True)
        (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    report = dict(scope='Seven previously differing AGNews queries, all nine widths',
                  comparisons=len(results), ordered_equal=sum(r['equal'] for r in results),
                  originally_differing=sum(r['original_native'] != r['official'] for r in results),
                  formal_ready=False,
                  limitation='Diagnostic oracle table and copied local queue operations; not production distance-kernel integration or a full official traversal-counter audit.',
                  probe_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
