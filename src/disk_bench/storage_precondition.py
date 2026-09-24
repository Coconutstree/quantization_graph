"""03 storage protocol: one recorded sequential O_DIRECT pass over search disk files.

This specifies an operation, not guaranteed device-cache residency or coldness.
Resident codebooks, queries, source vectors and build-only files are excluded.
"""
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import time

PROTOCOL = '03_direct_sequential_search_files_v1'
BLOCK = 4 * 1024 * 1024


def option(command, name, default=None):
    return command[command.index(name)+1] if name in command else default


def search_files(command, method=None):
    method = method or option(command, '--method')
    root = option(command, '--disk-index-dir')
    if root:
        root = Path(root)
        if method == 'Ours-Disk':
            layout = option(command, '--locality-layout-dir')
            paths = ([Path(layout)/n for n in ('graph_compact.pages','residual.pages')] if layout else
                     [root/n for n in ('shared_graph.pages','ours_full4_residual.pages')])
        elif method == 'SymphonyQG-DiskPort': paths = [root/'node_rows.pages']
        elif method == 'OG-LVQ-DiskPort': paths = [root/'graph.pages',root/'lvq4.pages']
        elif method == 'Glass-NSG-DiskPort': paths = [root/'graph.pages',root/'sq4u_codes.pages']
        elif method == 'DiskANN-PQ-Disk': paths = [root/'diskann_pq_R64_L400_A1.2_disk.index']
        else: raise ValueError('no search-file mapping for '+str(method))
    elif method == 'Starling-Disk':
        disk = option(command,'--disk_file_path')
        if not disk: raise ValueError('Starling requires explicit --disk_file_path')
        paths = [Path(disk)]
    elif method == 'AiSAQ-Disk':
        prefix = option(command,'--index_path_prefix')
        if not prefix or '--use_aisaq' not in command: raise ValueError('expected official AiSAQ search')
        disk = Path(prefix+'_disk.index')
        # Pinned AiSAQ metadata: 8 u64 fields, then optional 3 reorder fields,
        # followed by file size, max degree, and rearranged-PQ flag.
        with disk.open('rb') as stream:
            header = stream.read(8+14*8)
        if len(header)<96: raise ValueError('truncated AiSAQ disk header')
        reorder = struct.unpack_from('<Q',header,64)[0]
        offset = 88 + (24 if reorder else 0)
        if len(header)<offset+8: raise ValueError('truncated AiSAQ rearrangement metadata')
        rearranged = struct.unpack_from('<Q',header,offset)[0]
        paths = [disk,Path(prefix+('_pq_compressed_rearranged.bin' if rearranged else '_pq_compressed.bin'))]
    else: raise ValueError('no search-file mapping for '+str(method))
    resolved=[]
    for path in paths:
        path=path.resolve(strict=True)
        if not path.is_file() or path.stat().st_size<=0: raise ValueError('invalid search file: '+str(path))
        if path not in resolved: resolved.append(path)
    return resolved


def prepare_search(command, evidence_path, method=None):
    evidence_path=Path(evidence_path)
    if evidence_path.exists(): raise FileExistsError('refusing to overwrite '+str(evidence_path))
    evidence_path.parent.mkdir(parents=True,exist_ok=True)
    record={'protocol':PROTOCOL,'method':method or option(command,'--method'),
            'mode':'direct','scope':'all_bytes_of_disk_search_files_once_before_search_process',
            'included_in_query_timing':False,'device_cache_controlled':False,'cold_storage_claim':False,
            'started_unix':time.time(),'status':'running','files':[]}
    def save():
        tmp=evidence_path.with_suffix(evidence_path.suffix+'.tmp')
        tmp.write_text(json.dumps(record,indent=2)+'\n');tmp.replace(evidence_path)
    save()
    try:
        paths=search_files(command,method)
        record['planned_files']=[str(p) for p in paths];save()
        with mmap.mmap(-1,BLOCK) as buffer:
            for path in paths:
                fd=os.open(path,os.O_RDONLY|os.O_DIRECT)
                try:
                    before=os.fstat(fd);offset=0;digest=hashlib.sha256();started=time.time()
                    while offset<before.st_size:
                        count=os.preadv(fd,[buffer],offset)
                        if count<=0: raise OSError('unexpected EOF during direct pre-read')
                        digest.update(memoryview(buffer)[:count]);offset+=count
                    after=os.fstat(fd)
                    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
                        raise RuntimeError('index changed during preparation')
                    record['files'].append({'path':str(path),'bytes':offset,'sha256':digest.hexdigest(),
                        'inode':before.st_ino,'mtime_ns':before.st_mtime_ns,'device':before.st_dev,
                        'offset':0,'length':before.st_size,'io':'O_DIRECT','started_unix':started,
                        'elapsed_seconds':time.time()-started})
                    save()
                finally: os.close(fd)
        record['total_bytes']=sum(f['bytes']for f in record['files']);record['status']='completed'
    except BaseException as exc:
        record.update(status='failed',error=str(exc));raise
    finally:
        record['finished_unix']=time.time();record['elapsed_seconds']=record['finished_unix']-record['started_unix'];save()
    return record
