"""Isolated old/new layout query comparison with exact returned-ID parity."""
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import argparse

ROOT = pathlib.Path(__file__).resolve().parents[2]

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reverse', action='store_true')
    parser.add_argument('--widths', default='12,100')
    options = parser.parse_args()
    base = ROOT / 'results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair'
    output = base / ('locality_query_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    output.mkdir()
    layout = ROOT / 'work/05_disk_system_fair/experimental_layouts/agnews_bfs_split_v1'
    manifest = json.loads((layout / 'manifest.json').read_text())
    for path, digest in manifest['source_hashes'].items():
        if sha(pathlib.Path(path)) != digest:
            raise ValueError('source changed since layout export: ' + path)
    for name, info in manifest['files'].items():
        if sha(layout / name) != info['sha256']:
            raise ValueError('layout changed: ' + name)
    binary = output / 'ours_diagnostic'
    shutil.copy2(ROOT / 'src/graph_core/target/release/qgraph05_shared_graph_port', binary)
    command = json.loads((base / 'single_test_w32_20260911/agnews/Ours-Disk/command.json').read_text())
    command[0] = str(binary)
    print(output, flush=True)
    traces = []
    modes = [('legacy', False), ('locality', True)]
    if options.reverse:
        modes.reverse()
    for label, locality in modes:
        folder = output / label
        folder.mkdir()
        args = command.copy()
        for key, value in {'--integration-widths': options.widths, '--native-binary-sha256': sha(binary),
                           '--result-json': str(folder / 'result.json'), '--query-trace': str(folder / 'queries.jsonl')}.items():
            args[args.index(key) + 1] = value
        if locality:
            args += ['--locality-layout-dir', str(layout),
                     '--locality-combined-sha256', manifest['files']['graph_compact.pages']['sha256'],
                     '--locality-residual-sha256', manifest['files']['residual.pages']['sha256'],
                     '--locality-mapping-sha256', manifest['files']['id_to_slot.u32']['sha256']]
        (folder / 'command.json').write_text(json.dumps(args, indent=2))
        env = os.environ.copy()
        if env.get('LD_PRELOAD') or env.get('QG05_DIAGNOSTIC_CACHE'):
            raise ValueError('unexpected diagnostic environment')
        with (folder / 'terminal.log').open('w') as log:
            subprocess.run(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200, check=True)
        data = json.loads((folder / 'result.json').read_text())
        print(label, json.dumps(data['summary_rows']), flush=True)
        traces.append({(r['search_width'], r['query_id']): r for r in map(json.loads, (folder / 'queries.jsonl').read_text().splitlines())})
    keys = ['result_ids', 'recall_at_10', 'db1_checks', 'db1_survivors', 'full4_candidates', 'distance_evaluations']
    matches = traces[0].keys() == traces[1].keys() and all(a[k] == traces[1][q][k] for q, a in traces[0].items() for k in keys)
    (output / 'verification.json').write_text(json.dumps(dict(exact_query_parity=matches, queries=len(traces[0]), compared_fields=keys, formal_ready=False), indent=2))
    if not matches:
        raise ValueError('layout query parity failed')
    print('PASS exact returned-ID and search-counter parity', flush=True)

if __name__ == '__main__':
    main()
