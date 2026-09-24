"""Read-only integrity inventory; never equate export completion with query parity."""
import hashlib
import json
import mmap
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/graphs/audit_03c_all.json'
METHODS = ['Ours-Disk', 'SymphonyQG-DiskPort', 'OG-LVQ-DiskPort', 'Glass-NSG-DiskPort', 'DiskANN-PQ-Disk']

def graph_check(root, method, m):
    if method == 'DiskANN-PQ-Disk':
        return 'requires_native_reader'
    n = int(m.get('n', m.get('node_count', 0)))
    if method == 'SymphonyQG-DiskPort':
        path, per, stride, offset, width = 'node_rows.pages', 1, int(m['pages_per_row'])*4096, int(m['neighbor_offset'])*4, int(m['degree'])
    elif method == 'Glass-NSG-DiskPort':
        path, per, stride, offset, width = 'graph.pages', int(m['graph_records_per_page']), int(m['graph_record_bytes']), 0, int(m['graph_k'])
    elif method == 'OG-LVQ-DiskPort':
        path, per, stride, offset, width = 'graph.pages', int(m['graph_records_per_page']), int(m['graph_record_bytes']), 4, int(m['degree'])
    else:
        return 'requires_native_reader'
    page_bytes = stride if per == 1 else 4096
    pages = (n+per-1)//per
    with (root/path).open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        if len(buf) != pages*page_bytes:
            raise ValueError('graph file length mismatch')
        for start in range(0, pages, 4096):
            count = min(4096,pages-start)
            a = np.ndarray((count,per,width), dtype='<u4', buffer=buf, offset=start*page_bytes+offset, strides=(page_bytes,stride,4))
            valid_rows = np.arange(count*per).reshape(count,per)+start*per < n
            valid = np.broadcast_to(valid_rows[:,:,None],a.shape)
            if method == 'OG-LVQ-DiskPort':
                degree = np.ndarray((count,per),dtype='<u4',buffer=buf,offset=start*page_bytes,strides=(page_bytes,stride))
                if np.any((degree>width)&valid_rows):
                    raise ValueError('degree exceeds row capacity')
                valid = valid & (np.arange(width)[None,None,:]<degree[:,:,None])
            invalid = (a>=n)&valid
            if method == 'Glass-NSG-DiskPort':
                invalid &= a != 0xffffffff
            if np.any(invalid):
                raise ValueError('out-of-range neighbor ID')
    return 'all_neighbor_ids_checked'

def digest(path):
    h = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('file changed during audit')
    return h.hexdigest()

def main():
    report = []
    for ds in ['agnews', 'gist', 'dbpedia', 'sift10m']:
        for method in METHODS:
            root = ROOT / 'work/05_disk_system_fair/disk_root/05_disk_system_fair/05C_disk_system_fair' / ds / method / 'hybrid_disk'
            row = dict(dataset=ds, method=method, files={}, errors=[], query_parity='not_verified_by_this_audit')
            try:
                meta = root / ('index.meta.json' if method == 'DiskANN-PQ-Disk' else 'index.meta')
                if not meta.exists():
                    raise ValueError('no completion metadata; incomplete or still building')
                m = json.loads(meta.read_text()) if meta.suffix == '.json' else dict(line.split('=', 1) for line in meta.read_text().splitlines() if '=' in line)
                row['metadata'] = m
                row['graph_structure'] = graph_check(root, method, m)
                expected = {}
                if method == 'DiskANN-PQ-Disk':
                    expected = {f['path']: f for f in json.loads((root/'index.manifest.json').read_text())['files']}
                elif method == 'OG-LVQ-DiskPort' and (root/'reuse_audit.json').exists():
                    expected = json.loads((root/'reuse_audit.json').read_text())['index_files']
                for p in sorted(root.iterdir()):
                    if not p.is_file() or p.name.endswith('.reuse-tmp'):
                        continue
                    info = dict(bytes=p.stat().st_size, sha256=digest(p))
                    row['files'][p.name] = info
                    if p.name in expected and any(info[k] != expected[p.name][k] for k in ('bytes','sha256')):
                        row['errors'].append('manifest mismatch: '+p.name)
                for name in expected:
                    if name not in row['files']:
                        row['errors'].append('missing: '+name)
                if method in ['SymphonyQG-DiskPort','Glass-NSG-DiskPort'] and str(m.get('build_l')) != '400':
                    row['errors'].append('build_l not 400')
                row['status'] = 'inventory_complete_validation_pending' if not row['errors'] else 'failed'
            except Exception as exc:
                row['errors'].append(str(exc))
                row['status'] = 'incomplete_or_failed'
            report.append(row)
            OUT.write_text(json.dumps(report, indent=2)+'\n')
            print(ds, method, row['status'], row['errors'], flush=True)

if __name__ == '__main__':
    main()
