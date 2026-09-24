"""Read-only live checks; never changes benchmark parameters or retries bad evidence."""
import fcntl
import json
import os
from pathlib import Path
import time
import traceback
from protocol import OUT, REFERENCE

FIELDS = ['result_ids', 'recall_at_10', 'visited_nodes', 'distance_evaluations',
          'db1_checks', 'db1_survivors', 'full4_candidates']


def read_complete_rows(path):
    rows = {}
    if not path.exists():
        return rows
    with path.open() as stream:
        for line in stream:
            if not line.endswith('\n'):
                break
            row = json.loads(line)
            key = (row['search_width'], row['query_id'])
            if key in rows:
                raise ValueError(f'duplicate query: {key}')
            rows[key] = row
    return rows


def main():
    with (OUT / 'watch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {'pid': os.getpid(), 'status': 'watching', 'checked': [], 'errors': [],
                 'automatic_code_repair': False}
        checked = set()
        while True:
            try:
                queue = json.loads((OUT / 'queue_state.json').read_text())
                state['queue_status'] = queue['status']
                mode = queue.get('current')
                state['current'] = mode
                if queue['status'] == 'failed':
                    raise RuntimeError(queue.get('error', 'queue failed'))
                if queue['status'] == 'running':
                    os.kill(queue['pid'], 0)
                    if queue.get('stage_pid'):
                        # Stage exits can precede the coordinator's state update.
                        try:
                            os.kill(queue['stage_pid'], 0)
                        except ProcessLookupError:
                            time.sleep(2)
                            new = json.loads((OUT / 'queue_state.json').read_text())
                            if new == queue:
                                raise
                modes = list(queue['completed']) + ([mode] if mode else [])
                for name in dict.fromkeys(modes):
                    folder = OUT / name
                    pending = [p for p in sorted((folder / 'memory_stats').glob('L*.json'))
                               if (name, p.stem) not in checked]
                    if not pending:
                        continue
                    rows = read_complete_rows(folder / 'queries.jsonl')
                    target = REFERENCE if name == 'baseline' else OUT / 'baseline'
                    reference = read_complete_rows(target / 'queries.jsonl') if name != 'profile' and not name.startswith('nav') else None
                    expected = 200 if name == 'profile' else 800
                    for path in pending:
                        stats = json.loads(path.read_text())
                        width = stats['width']
                        part = {key: row for key, row in rows.items() if key[0] == width}
                        if len(part) < expected:
                            continue
                        if len(part) != expected:
                            raise ValueError(f'{name} L{width}: wrong query count')
                        if reference is not None:
                            for key, row in part.items():
                                for field in FIELDS:
                                    if row[field] != reference[key][field]:
                                        raise ValueError(f'{name} {key}: parity mismatch {field}')
                        for metric, trace in [('io_requests', 'io_requests'), ('read_pages', 'sectors_4k'), ('read_bytes', 'bytes_read')]:
                            if sum(v[metric] for v in stats['operations'].values()) != sum(row[trace] for row in part.values()):
                                raise ValueError(f'{name} L{width}: I/O mismatch {metric}')
                        checked.add((name, path.stem))
                        state['checked'].append({'mode': name, 'width': width, 'queries': expected})
                        print(f'{name} L{width}: live checks passed', flush=True)
                if queue['status'] == 'completed':
                    state['status'] = 'completed'
            except Exception as error:
                state['status'] = 'attention_required'
                state['errors'].append({'time': time.time(), 'error': str(error)})
                traceback.print_exc()
            state['updated_unix'] = time.time()
            temp = OUT / 'watch_state.json.tmp'
            temp.write_text(json.dumps(state, indent=2) + '\n')
            temp.replace(OUT / 'watch_state.json')
            if state['status'] != 'watching':
                break
            time.sleep(30)


if __name__ == '__main__':
    main()
