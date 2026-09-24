"""Verify provenance, maximum dimension, native admission and search semantics."""
import json
import struct
from pathlib import Path
from policy import ROOT, HERE, OUT, WORK, MIB, calibrated_plan, dump
from prepare import BIN, sha, build, asset_for

FIELDS = ('result_ids', 'recall_at_10', 'visited_nodes', 'distance_evaluations',
          'db1_checks', 'db1_survivors', 'full4_candidates', 'rerank_candidates')


def read(path):
    return json.loads(Path(path).read_text())


def traces(path, widths=None):
    values = [json.loads(line) for line in path.read_text().splitlines()]
    return {(r['search_width'], r['query_id']): [r[k] for k in FIELDS]
            for r in values if widths is None or r['search_width'] in widths}


def vectors(path):
    raw = Path(path).read_bytes()
    stride = 4 * (struct.unpack_from('<I', raw)[0] + 1)
    return {raw[start:start + stride] for start in range(0, len(raw), stride)}


def main():
    build()  # Existing build: validates frozen binary and native dependency hashes.
    assert read(OUT / 'state_538.json')['status'] == 'completed'
    assert read(OUT / 'preflight.json')['passed']
    for item in read(OUT / 'preflight.json')['rows']:
        path = OUT / 'preflight' / f'b{item["budget_mib"]}_d{item["dimension"]}' / 'memory_plan.json'
        assert sha(path) == item['memory_plan_sha256']
    source = (WORK / 'native/ours_port.rs').read_text()
    assert source.count('if codec.navigation_keep.is_none() && ablation.uses_gate()') == 2
    assert 'if codec.pca.is_none() && ablation.uses_gate() && estimate.valid != 0' in source
    accepted, queryfiles = [], {}
    measured = 0
    for p in sorted(OUT.glob('*/*/acceptance.json')):
        a = read(p)
        assert a['passed'] and a['binary_sha256'] == sha(BIN)
        for name, digest in a['files'].items():
            assert sha(p.parent / name) == digest, (p, name)
        config = read(p.parent / 'config.json')
        plan = read(p.parent / 'memory_plan.json')
        memory = read(p.parent / 'memory_measurement.json')
        selection = read(p.parent / 'adaptive_selection.json')
        expected = calibrated_plan(config['budget_mib'])
        assert all(selection[k] == v for k, v in expected.items())
        assert selection['M'] in (32, 64)
        assert plan['routing_capacity_pages'] == 0
        assert plan['admission_bytes'] == expected['admission_bytes'] <= config['budget_mib'] * MIB
        assert plan['expected_peak_bytes'] >= memory['sampled_high_water_bytes']['VmPeak']
        assert memory['user_address_space_budget_passed'] and memory['hard_limit_verified']
        cmd = read(p.parent / 'command.json')
        flags = dict(zip(cmd[1::2], cmd[2::2]))
        assert flags['--pca-tail-mode'] == 'route'
        assert int(flags['--pca-route-keep']) == selection['M']
        queryfiles[config['split']] = flags['--query']
        asset = asset_for(selection['dimension'])
        for name, digest in read(asset / 'encoded.json')['files'].items():
            assert sha(asset / name) == digest
        if config['split'] == 'test':
            lock = read(OUT / 'selection_538.json')['selected']
            assert selection['M'] == lock['M'] and config['widths'] == [lock['width']]
        measured += a['query_count']
        accepted.append(str(p.relative_to(OUT)))
    protocol = read(OUT / 'protocol_538.json')
    options = []
    for m in protocol['shortlists']:
        for row in read(OUT / 'tune' / f'b538_d128_m{m}_grid_r1/result.json')['summary_rows']:
            if row['recall'] >= protocol['target_recall']:
                options.append((row['qps'], m, row['search_width']))
    selection = read(OUT / 'selection_538.json')
    selected = selection['selected']
    assert (selected['qps'], selected['M'], selected['width']) == max(options)
    assert selection['selected_before_test'] and selection['binary_sha256'] == sha(BIN)
    a = OUT / 'test/b538_d128_m32_locked_r1/queries.jsonl'
    b = OUT / 'test/b538_d128_m32_locked_r2/queries.jsonl'
    assert len(traces(a)) == 800 and traces(a) == traces(b)
    sets = {name: vectors(path) for name, path in queryfiles.items()}
    assert not sets['train'] & sets['tune'] and not sets['train'] & sets['test'] and not sets['tune'] & sets['test']
    historical = ROOT / 'results/04_ours_memory_budget/gist_pca_routing/tune'
    parity = []
    for m, old_m in ((32, 32), (64, 0)):
        now = OUT / 'tune' / f'b538_d128_m{m}_grid_r1/queries.jsonl'
        before = historical / f'd128_k{old_m}_grid_r1/queries.jsonl'
        assert traces(now, (100, 180)) == traces(before, (100, 180))
        parity.append(dict(current_M=m, historical_M=old_m, widths=[100, 180], exact_fields=list(FIELDS)))
    for p in OUT.glob('competing_build_*.json'):
        note = read(p)
        assert note['resumed'] > note['paused']
    dump(OUT / 'audit.json', dict(passed=True, accepted_runs=len(accepted), measured_queries=measured,
         accepted=accepted, binary_sha256=sha(BIN), maximum_dimension_reproduced=True,
         native_preflight_passed=True, zero_route_io=True, lowdim_hard_prune_disabled=True,
         test_repeats_semantically_identical=True, historical_parity=parity,
         unique_query_vectors={k: len(v) for k, v in sets.items()}, split_overlap=0,
         competing_build_restored=True, script_hashes={p.name: sha(p) for p in HERE.iterdir() if p.is_file()},
         report_sha256=sha(OUT / 'report.md'), hard_prune_document_sha256=sha(OUT / 'hard_prune.md')))
    print('audit passed:', len(accepted), 'runs,', measured, 'measured queries')


if __name__ == '__main__':
    main()
