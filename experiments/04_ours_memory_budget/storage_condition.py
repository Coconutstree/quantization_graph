"""Explicit, recorded storage preparation; never changes file contents."""
import json
import subprocess
import time
from pathlib import Path


def prepare_storage(layout, mode, record_path):
    if mode not in ('none', 'direct'):
        raise ValueError('unknown pre-read mode')
    layout, record_path = Path(layout), Path(record_path)
    files = [layout / name for name in ('graph_compact.pages', 'residual.pages')]
    def identity():
        return {str(p): {'size': p.stat().st_size, 'inode': p.stat().st_ino,
                         'mtime_ns': p.stat().st_mtime_ns} for p in files}
    before = identity()
    if mode == 'direct' and any(v['size'] % 4096 for v in before.values()):
        raise ValueError('direct pre-read requires page-aligned file sizes')
    record = {'mode': mode, 'scope': 'once_before_search_process',
              'included_in_query_timing': False, 'cold_cache_guaranteed': False,
              'started_unix': time.time(), 'status': 'running',
              'files_before': before, 'reads': []}
    def save():
        record_path.write_text(json.dumps(record, indent=2) + '\n')
    save()
    try:
        if mode == 'direct':
            for path in files:
                cmd = ['dd', 'if='+str(path), 'of=/dev/null', 'bs=4M', 'iflag=direct']
                started = time.time()
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                record['reads'].append({'command': cmd, 'started_unix': started,
                    'finished_unix': time.time(), 'returncode': result.returncode,
                    'stderr': result.stderr})
                save()
                if result.returncode:
                    raise RuntimeError('direct pre-read failed: '+result.stderr)
        record['files_after'] = identity()
        if record['files_after'] != before:
            raise RuntimeError('layout identity changed during preparation')
        record['status'] = 'completed'
    except BaseException as exc:
        record.update(status='failed', error=str(exc))
        raise
    finally:
        record['finished_unix'] = time.time()
        record['elapsed_seconds'] = record['finished_unix'] - record['started_unix']
        save()
    return record
