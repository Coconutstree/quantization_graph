"""Read-only acceptance of measured artifacts; never relabel formal readiness."""
import collections
import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/single_test_w32_20260911'
path = ROOT / 'src/disk_bench/native_contract.py'
spec = importlib.util.spec_from_file_location('agnews_contract', path)
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    run = json.loads((RUN / 'run.json').read_text())
    output = RUN / ('acceptance_agnews_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    output.mkdir()
    report = dict(formal_accepted=False, dataset='agnews', methods=[],
                  scope='Saved artifacts/traces/commands and contract only. No performance rerun or large index scan.',
                  open_checks=['complete workspace memory accounting', 'binary-specific timing scope audit',
                               'storage-cache protocol verification', 'independent baseline parity provenance'])
    for method in contract.LAYER_METHODS['05c']:
        folder = RUN / 'agnews' / method
        artifact_path = folder / 'result.json'
        data = json.loads(artifact_path.read_text())
        args = json.loads((folder / 'command.json').read_text())
        flags = dict(zip(args[1::2], args[2::2]))
        widths = [w for w in run['widths'] if method != 'DiskANN-PQ-Disk' or w >= 10]
        expected = {k: flags['--' + k.replace('_', '-')] for k in
                    ['dataset', 'phase', 'storage_mode', 'cache_mode', 'query_split_sha256',
                     'query_order_sha256', 'native_binary_sha256']}
        expected.update(workers=32, repeat_id=0, warmup_queries=100,
                        query_order_seed=int(flags['--query-order-seed']), search_dram_budget_gib=2.0)
        try:
            contract.validate_artifact(artifact_path, spec=contract.SPECS_BY_KEY['05c:' + method],
                                       expected=expected, disk_root=ROOT / 'work/05_disk_system_fair/disk_root')
            error = None
        except contract.ContractError as exc:
            error = str(exc)
        checks = dict(binary_hash=sha(Path(args[0])) == data['native_binary_sha256'],
                      query_order_hash=sha(Path(flags['--query-order'])) == data['query_order_sha256'])
        binary_candidates = [Path(args[0])] + list(Path(args[0]).parent.glob(Path(args[0]).name + '.before_*'))
        matching_binary = next((str(p) for p in binary_candidates if sha(p) == data['native_binary_sha256']), None)
        command_binary_matches = checks['binary_hash']
        checks['binary_hash'] = matching_binary is not None
        counts = collections.Counter()
        totals = collections.defaultdict(lambda: collections.defaultdict(float))
        ids = collections.defaultdict(set)
        invalid = []
        cache_peak = 0
        with (folder / 'queries.jsonl').open() as stream:
            for line_no, line in enumerate(stream, 1):
                row = json.loads(line)
                width, query = row['search_width'], row['query_id']
                if query in ids[width]:
                    invalid.append(f'duplicate query: {width}/{query}')
                ids[width].add(query)
                counts[width] += 1
                cache = row.get('query_cache_allocated_bytes', 0)
                cache_peak = max(cache_peak, cache)
                if not 0 < cache <= 4 * 1024 * 1024 or row['bytes_read'] != row['sectors_4k'] * 4096:
                    invalid.append(f'cache/I/O violation at line {line_no}')
                for key in ['recall_at_10', 'latency_us', 'bytes_read', 'io_requests']:
                    value = row[key]
                    if not math.isfinite(value) or value < 0:
                        invalid.append(f'invalid {key} at line {line_no}')
                    totals[width][key] += value
        checks['trace_complete'] = set(counts) == set(widths) and all(v == 800 for v in counts.values())
        checks['query_ids_complete'] = all(s == set(range(800)) for s in ids.values())
        checks['trace_accounting'] = not invalid
        rows = data['summary_rows']
        checks['summary_complete'] = len(rows) == len(widths) and {r['search_width'] for r in rows} == set(widths)
        checks['summary_matches_trace'] = all(
            abs(r[key] - totals[r['search_width']][trace_key] / counts[r['search_width']]) <= 1e-5 * max(1, abs(r[key]))
            for r in rows for key, trace_key in [('recall', 'recall_at_10'), ('latency_mean_us', 'latency_us'),
                                                ('bytes_read_per_query', 'bytes_read'), ('io_requests_per_query', 'io_requests')])
        item = dict(method=method, checks=checks, contract_pass=error is None, contract_error=error,
                    command_binary_matches=command_binary_matches, matching_binary=matching_binary,
                    summary_relative_tolerance=1e-5, tolerance_reason='C++ summaries use limited decimal output precision; agreement is numerical, not bitwise.',
                    query_rows=sum(counts.values()), summary_rows=len(rows), query_cache_peak_bytes=cache_peak,
                    process_peak_rss_bytes=data.get('peak_rss_bytes'), resident_bytes=data.get('resident_bytes'),
                    worker_scratch_bytes=data.get('worker_scratch_bytes'),
                    memory_accounting_complete=data.get('memory_accounting_complete'),
                    errors=invalid[:20], artifact_sha256=sha(artifact_path),
                    width_caveat='1..9 are requested labels; actual Ours width is 10' if method == 'Ours-Disk' else None)
        report['methods'].append(item)
        print(method, checks, 'contract_pass', error is None, flush=True)
    report['formal_accepted'] = all(m['contract_pass'] and all(m['checks'].values()) for m in report['methods']) and not report['open_checks']
    (output / 'acceptance.json').write_text(json.dumps(report, indent=2))
    lines = ['# AGNews acceptance', '', 'Verdict: NOT ACCEPTED as final formal results.', '',
             '| Method | Rows | Queries | Integrity checks | Formal contract |', '| --- | ---: | ---: | --- | --- |']
    for m in report['methods']:
        lines.append(f"| {m['method']} | {m['summary_rows']} | {m['query_rows']} | {'PASS' if all(m['checks'].values()) else 'FAIL'} | {'PASS' if m['contract_pass'] else 'FAIL'} |")
    lines += ['', '## Contract failures']
    for m in report['methods']:
        lines += ['', '### ' + m['method'], '```text', m['contract_error'] or 'None', '```']
    lines += ['', '## Remaining checks'] + ['- ' + x for x in report['open_checks']]
    lines += ['', 'Low process RSS and bounded page cache do not establish full index-workspace accounting.',
              'Existing data and readiness flags were not modified. Background queue was not interrupted.']
    (output / 'ACCEPTANCE.md').write_text('\n'.join(lines) + '\n')
    print(output, flush=True)

if __name__ == '__main__':
    main()
