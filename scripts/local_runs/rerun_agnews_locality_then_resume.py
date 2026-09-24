"""Rerun AGNews Ours, publish only verified output, plot, then resume queue."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/single_test_w32_20260911'

def read(path):
    return json.loads(path.read_text())

def write(path, data):
    temp = path.with_suffix(path.suffix + '.locality.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    lock = (RUN / 'resume.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    stage = RUN / ('agnews_locality_rerun_' + stamp)
    stage.mkdir()
    state_path = RUN / 'locality_workflow.json'
    state = dict(pid=os.getpid(), status='verifying_inputs', stage=str(stage), formal_ready=False)
    write(state_path, state)
    try:
        layout = ROOT / 'work/05_disk_system_fair/experimental_layouts/agnews_bfs_split_v1'
        manifest = read(layout / 'manifest.json')
        for path, digest in manifest['source_hashes'].items():
            if sha(Path(path)) != digest:
                raise ValueError('source hash mismatch: ' + path)
        for name, info in manifest['files'].items():
            if sha(layout / name) != info['sha256']:
                raise ValueError('layout hash mismatch: ' + name)
        validated = RUN.parent / 'locality_query_20260911_234136'
        if not read(validated / 'verification.json')['exact_query_parity']:
            raise ValueError('missing locality parity acceptance')
        binary = stage / 'ours_locality'
        shutil.copy2(validated / 'ours_diagnostic', binary)
        args = read(validated / 'locality/command.json')
        if sha(binary) != args[args.index('--native-binary-sha256') + 1]:
            raise ValueError('validated binary mismatch')
        args[0] = str(binary)
        widths = read(RUN / 'run.json')['widths']
        for flag, value in {'--integration-widths': ','.join(map(str, widths)),
                            '--result-json': str(stage / 'result.json'),
                            '--query-trace': str(stage / 'queries.jsonl')}.items():
            args[args.index(flag) + 1] = value
        write(stage / 'command.json', args)
        state['status'] = 'running_agnews_ours'
        write(state_path, state)
        with (stage / 'terminal.log').open('w') as log:
            subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=86400)
        result = read(stage / 'result.json')
        rows = result['summary_rows']
        if result['status'] != 'done' or {r['search_width'] for r in rows} != set(widths) or len(rows) != len(widths):
            raise ValueError('incomplete width sweep')
        counts = dict.fromkeys(widths, 0)
        with (stage / 'queries.jsonl').open() as stream:
            for line in stream:
                row = json.loads(line)
                counts[row['search_width']] += 1
                if len(row['result_ids']) != 10 or row['bytes_read'] != row['sectors_4k'] * 4096:
                    raise ValueError('invalid query output')
                if not 0 < row['query_cache_allocated_bytes'] <= 4 * 1024 * 1024:
                    raise ValueError('cache budget violation')
        if any(n != 800 for n in counts.values()):
            raise ValueError('incomplete test query count')
        state['status'] = 'publishing_and_plotting'
        write(state_path, state)
        archive = RUN / 'archives' / ('agnews_before_locality_' + stamp)
        archive.mkdir(parents=True)
        shutil.copy2(RUN / 'agnews/report.json', archive / 'report.json')
        if (RUN / 'exports').exists():
            shutil.copytree(RUN / 'exports', archive / 'exports')
        (RUN / 'agnews/Ours-Disk').rename(archive / 'Ours-Disk')
        destination = RUN / 'agnews/Ours-Disk'
        destination.mkdir()
        # Keep command paths valid: the binary and original run remain in stage.
        for name in ['command.json', 'result.json', 'queries.jsonl', 'terminal.log']:
            shutil.copy2(stage / name, destination / name)
        write(destination / 'layout_revision.json', dict(source_stage=str(stage), archive=str(archive),
              layout='bfs_graph_compact_separate_residual', formal_ready=False,
              timing_scope='test_only_after_all_worker_warmup', cold_storage_verified=False,
              effective_width_rule='max(requested_width,10)', binary_sha256=sha(binary)))
        report = [r for r in read(RUN / 'agnews/report.json') if r['method'] != 'Ours-Disk']
        report.append(dict(method='Ours-Disk', diagnostic_only=True, index_unchanged=True,
                           query_rows=sum(counts.values()), summary_rows=rows, locality_layout=str(layout)))
        write(RUN / 'agnews/report.json', report)
        for script in ['export_05c_pending_figures.py', 'plot_05c_recall_qps.py']:
            subprocess.run([sys.executable, str(ROOT / 'scripts/local_runs' / script), str(RUN)], cwd=ROOT, check=True)
        write(RUN / 'exports/agnews_layout_revision.json', read(destination / 'layout_revision.json'))
        state['status'] = 'resuming_remaining_queue'
        write(state_path, state)
    except BaseException as exc:
        state.update(status='failed', error=str(exc))
        write(state_path, state)
        raise
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/local_runs/resume_05c_single_test.py'), str(RUN)], cwd=ROOT)
    state.update(status='complete' if result.returncode == 0 else 'remaining_queue_failed', exit_code=result.returncode)
    write(state_path, state)
    raise SystemExit(result.returncode)

if __name__ == '__main__':
    main()
