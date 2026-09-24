"""Explain saved search differences using official and native candidate distances.

Does not change the kernel or relax formal admission. Exact ties are distinguished
from near ties; candidate distances alone do not prove traversal equivalence.
"""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'baselines/svs/python'))
import svs


def main():
    parent = ROOT / 'results/diagnostics/03_other_methods_admission_20260921'
    source = parent / 'og_search/pairs.jsonl'
    rows = [json.loads(s) for s in source.read_text().splitlines()]
    different = [r for r in rows if r['official'] != r['native']]
    requests = sorted({(r['query_id'], i) for r in different
                       for i in set(r['official']) | set(r['native'])})
    out = parent / 'og_mismatch_distances'
    out.mkdir(exist_ok=False)
    pairs = out / 'requests.txt'
    pairs.write_text(''.join(f'{q} {i}\n' for q, i in requests))
    suffix = '05_disk_system_fair/05C_disk_system_fair/agnews/OG-LVQ-DiskPort/hybrid_disk'
    disk = ROOT / 'work/05_disk_system_fair/disk_root' / suffix
    official = ROOT / 'artifacts/indexes/legacy_disk_store' / suffix / 'official_svs'
    query = ROOT / 'artifacts/query_splits/agnews/shared/validation_query.fvecs'
    binary = ROOT / 'build/disk/native/qgraph05_og_lvq_distance_probe'
    subprocess.run([str(binary), str(disk), str(query), str(out / 'native.csv'), str(pairs)], check=True)
    native = {(int(q), int(i)): float(d) for q, i, d in csv.reader((out / 'native.csv').open())}
    if set(native) != set(requests):
        raise ValueError('incomplete candidate probe')
    index = svs.Vamana(str(official / 'config'), svs.GraphLoader(str(official / 'graph')),
                       svs.LVQLoader(str(official / 'data')), svs.DistanceType.L2, num_threads=1)
    queries = np.ascontiguousarray(svs.read_vecs(str(query)), dtype=np.float32)
    distances = {(q, i): float(index.get_distance(i, queries[q])) for q, i in requests}
    details = []
    for r in different:
        q = r['query_id']
        a, b = r['official'], r['native']
        common = set(a) & set(b)
        inverted = [(i, j) for pi, i in enumerate(a) for j in a[pi + 1:]
                    if i in common and j in common and b.index(i) > b.index(j)]
        compare = lambda i, j: dict(first=i, second=j,
                                    official_delta=distances[q, i] - distances[q, j],
                                    native_delta=native[q, i] - native[q, j])
        inversions = [compare(i, j) for i, j in inverted]
        boundary = [compare(i, j) for i in set(a) - set(b) for j in set(b) - set(a)]
        details.append(dict(width=r['width'], query_id=q, official=a, native=b,
                            inversions=inversions, boundary_comparisons=boundary,
                            candidates=[dict(id=i, official_distance=distances[q, i],
                                             native_distance=native[q, i]) for i in sorted(set(a) | set(b))]))
    (out / 'details.json').write_text(json.dumps(details, indent=2) + '\n')
    inv = [i for r in details for i in r['inversions']]
    boundary = [i for r in details for i in r['boundary_comparisons']]
    report = dict(dataset='agnews', search_comparisons=len(rows), differing_searches=len(details),
                  distinct_queries=len({r['query_id'] for r in details}),
                  unique_candidate_distances=len(requests),
                  set_differences=sum(bool(r['boundary_comparisons']) for r in details),
                  inversions=len(inv), exact_official_tie_inversions=sum(i['official_delta'] == 0 for i in inv),
                  native_tie_inversions=sum(i['native_delta'] == 0 for i in inv),
                  strict_distance_sign_flips=sum(i['official_delta'] * i['native_delta'] < 0 for i in inv),
                  boundary_pairs=len(boundary), exact_official_tie_boundary_pairs=sum(i['official_delta'] == 0 for i in boundary),
                  max_candidate_distance_absolute_error=max(abs(distances[k] - native[k]) for k in requests),
                  formal_ready=False,
                  limitation='Candidate distance audit only; non-tie set changes require traversal evidence.',
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  probe_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
