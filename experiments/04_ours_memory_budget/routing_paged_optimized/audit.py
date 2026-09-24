"""Recheck immutable build and run artifacts after both pilot phases finish."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'results/04_ours_memory_budget/routing_paged_optimized'
WORK = ROOT / 'work/ours_memory_budget/routing_paged_optimized'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((WORK / 'build.json').read_text())
    binary = sha(WORK / 'ours_routing_paged_optimized')
    assert binary == manifest['binary_sha256']
    for path, digest in manifest['source_sha256'].items():
        assert sha(ROOT / path) == digest, path
    old = sha(WORK / 'before/ours_routing_paged_v1')
    assert old == json.loads((WORK / 'before/build.json').read_text())['binary_sha256']
    records = list(OUT.glob('*/runs/*/acceptance.json'))
    assert len(records) == 38, len(records)
    queries = 0
    capacities, inputs = {}, {}
    for path in records:
        folder = path.parent
        a = json.loads(path.read_text())
        assert a['passed'] and a['binary_sha256'] == (old if a['mode'] == 'v1' else binary)
        for name, digest in a['files'].items():
            assert sha(folder / name) == digest, str(folder / name)
        queries += a['queries']
        m = json.loads((folder / 'memory_measurement.json').read_text())
        assert m['hard_limit_verified'] and m['user_address_space_budget_passed']
        assert m['rlimit_as_bytes'] == a['budget']
        dataset = folder.parent.parent.name
        p = json.loads((folder / 'routing_storage_precondition.json').read_text())
        identity = (p['path'], p['sha256'], p['bytes'])
        assert inputs.setdefault(dataset, identity) == identity
        b = json.loads((folder / 'budget.json').read_text())
        stats = json.loads((folder / 'routing_stats.json').read_text())
        if a['mode'] == 'resident':
            assert not stats
        for s in stats.values():
            key = (dataset, b['fraction'])
            capacity = (a['budget'], s['capacity'], s['inflight'])
            assert capacities.setdefault(key, capacity) == capacity
            assert s['peak_active'] <= s['inflight'] == 64
            assert s['peak_pages'] <= s['capacity']
    assert queries == 4800, queries
    report = json.loads((OUT / 'pooled.json').read_text())
    assert len(report) == 12
    result = dict(passed=True, groups=len(records), measured_queries=queries,
                  binary_sha256=binary, old_binary_sha256=old,
                  source_and_run_hashes_verified=True,
                  same_budget_and_capacity_verified=True,
                  memory_and_inflight_verified=True,
                  stable_sidecar_inputs=inputs,
                  supplementary_sources={str(p.relative_to(ROOT)): sha(p)
                                         for p in Path(__file__).parent.glob('*')
                                         if p.suffix in ('.py', '.cpp')})
    (OUT / 'validation/final_audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f"Verified {len(records)} groups, {queries} measured queries, immutable sources and artifacts.")


if __name__ == '__main__':
    main()
