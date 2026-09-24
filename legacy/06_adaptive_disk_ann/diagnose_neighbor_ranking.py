"""Compare rankings on identical fresh-neighbor sets from diagnostic search traces.

No graph-search recall or throughput claims: original FP32 local ranking is the
reference, not the deployed full4 distance. Aggregates weight each expansion equally.
"""
import argparse
import json
from pathlib import Path
import re
import numpy as np


def run():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--variant', default='d256_k32_t100')
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    config = json.loads((a.run_dir / 'pilot_config.json').read_text())
    ds = config['dataset']
    args = json.loads((a.run_dir / (a.variant + '.argv.json')).read_text())
    flags = dict(zip(args[1::2], args[2::2]))
    dim = int(flags['--adaptive-route-dim'])
    route = Path(flags['--adaptive-route-dir'])
    mean = np.load(route.parent / 'projection_nnp/mean.f32.npy')
    comp = np.load(route.parent / 'projection_nnp/components.f32.npy')[:dim]
    base = np.memmap(root / f'data/{ds}/{ds}_base.fvecs', dtype='<f4', mode='r').reshape(-1, len(mean)+1)
    queries = np.fromfile(a.run_dir / 'query.fvecs', dtype='<f4').reshape(-1, len(mean)+1)[:, 1:]
    code = np.memmap(route / f'codes_d{dim}.bin', dtype='u1', mode='r').reshape(len(base), -1)
    scales = np.memmap(route / f'scales_d{dim}.f16', dtype='<f2', mode='r')
    rows = []
    for path in sorted((a.run_dir / (a.variant + '.neighbors')).glob('q*_w*.tsv')):
        query_id = int(re.fullmatch(r'q(\d+)_w\d+\.tsv', path.name)[1])
        q = queries[query_id]
        projected = (q-mean) @ comp.T
        for line in path.read_text().splitlines():
            node, before, after = line.split('\t')
            ids = np.array([int(x) for x in before.split(',')])
            kept = set(int(x) for x in after.split(',') if x)
            x = np.asarray(base[ids, 1:])
            y = (x-mean) @ comp.T
            signs = np.unpackbits(code[ids], axis=1, bitorder='little')[:, :dim].astype(np.float32)*2-1
            s = scales[ids].astype(np.float32)
            distances = {
                'original_float': np.sum((x-q)**2, axis=1),
                'projected_float': np.sum((y-projected)**2, axis=1),
                'projected_sign': dim*s*s-2*s*(signs @ projected),
            }
            orders = {key: ids[np.argsort(value, kind='stable')] for key, value in distances.items()}
            row = dict(query_id=query_id, node=int(node), candidates=len(ids), kept=len(kept))
            # Top-1/10 preservation, and same-size shortlist comparison, exclude
            # gate effects from representation-only ranking comparisons.
            for k in (1, 10):
                count = min(k, len(ids))
                ref = set(orders['original_float'][:count])
                row[f'actual_keep_reference_top{k}'] = len(ref & kept)/count
                for name in ('projected_float', 'projected_sign'):
                    row[f'{name}_overlap_top{k}'] = len(ref & set(orders[name][:count]))/count
                    row[f'{name}_shortlist32_reference_top{k}'] = len(ref & set(orders[name][:32]))/count
            rows.append(row)
    if not rows:
        raise ValueError('no neighbor traces found')
    metrics = [k for k in rows[0] if k not in ('query_id', 'node', 'candidates', 'kept')]
    summary = {}
    for label, subset in [('all', rows), ('more_than_32_candidates', [r for r in rows if r['candidates'] > 32])]:
        summary[label] = dict(expansions=len(subset), **{k: float(np.mean([r[k] for r in subset])) for k in metrics}) if subset else dict(expansions=0)
    output = dict(dataset=ds, dimension=dim, query_offset=config['query_offset'],
                  queries=len(set(r['query_id'] for r in rows)), graph_search_recall=False,
                  qps_comparison=False, reference='original FP32 local-neighbor ranking, not full4',
                  weighting='equal expansion weights', summary=summary, expansions=rows)
    target = a.run_dir / (a.variant + '.neighbor_ranking.json')
    with target.open('x') as f:
        json.dump(output, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    run()
