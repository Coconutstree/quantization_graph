"""Compare actual disk-port traversal with pinned SVS on existing AGNews data.

Diagnostic only: the native mmap run is not a throughput measurement.
"""
from pathlib import Path
import csv
import hashlib
import json
import os
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'baselines/svs/python'))
import svs


def main():
    out = ROOT / 'results/diagnostics/03_other_methods_admission_20260921/og_search'
    out.mkdir(exist_ok=False)
    suffix = '05_disk_system_fair/05C_disk_system_fair/agnews/OG-LVQ-DiskPort/hybrid_disk'
    disk = ROOT / 'work/05_disk_system_fair/disk_root' / suffix
    official = ROOT / 'artifacts/indexes/legacy_disk_store' / suffix / 'official_svs'
    query = ROOT / 'artifacts/query_splits/agnews/shared/validation_query.fvecs'
    binary = ROOT / 'build/disk/native/qgraph05_og_lvq_distance_probe'
    subprocess.run([str(binary), str(disk), str(query), str(out / 'native.csv'), 'search'],
                   env=dict(os.environ, QG05_REFERENCE_MMAP='1'), check=True)
    reference = svs.Vamana(str(official / 'config'), svs.GraphLoader(str(official / 'graph')),
                           svs.LVQLoader(str(official / 'data')), svs.DistanceType.L2, num_threads=1)
    queries = np.ascontiguousarray(svs.read_vecs(str(query)), dtype=np.float32)
    native = {}
    for row in csv.reader((out / 'native.csv').open()):
        width, qid, *ids = map(int, row)
        if (width, qid) in native:
            raise ValueError('duplicate native row')
        native[width, qid] = ids
    widths = [10, 20, 40, 60, 100, 160, 240, 400, 580]
    if len(native) != len(queries) * len(widths):
        raise ValueError('incomplete native coverage')
    summaries = []
    with (out / 'pairs.jsonl').open('w') as f:
        for width in widths:
            reference.search_window_size = width
            expected, _ = reference.search(queries, 10)
            equal = 0
            overlap = 0
            for qid, row in enumerate(expected):
                ids = list(map(int, row))
                observed = native[width, qid]
                equal += ids == observed
                overlap += len(set(ids) & set(observed)) / 10
                f.write(json.dumps(dict(width=width, query_id=qid, official=ids, native=observed)) + '\n')
            summaries.append(dict(width=width, queries=len(queries), ordered_equal=equal,
                                  mean_top10_overlap=overlap / len(queries)))
            print(summaries[-1], flush=True)
    report = dict(dataset='agnews', scope='official ordered top10 vs native mmap traversal',
                  formal_ready=False, rows=summaries,
                  all_ordered_equal=all(r['ordered_equal'] == r['queries'] for r in summaries),
                  binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  query_sha256=hashlib.sha256(query.read_bytes()).hexdigest(),
                  remaining=['GIST reference validation', 'official traversal-counter evidence',
                             'common-budget direct-I/O admission'])
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
