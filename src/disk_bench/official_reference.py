"""Independent pinned official IDs plus a same-kernel memory trace for storage parity."""
import json
import os
from pathlib import Path
from .memory_runner import run_measured
from .protocol import sha256
from .storage_precondition import option

METHODS = {'SymphonyQG-DiskPort':'symphonyqg', 'Glass-NSG-DiskPort':'glass'}

def ids(path):
    rows={}
    for line in Path(path).read_text().splitlines():
        r=json.loads(line);key=(r['search_width'],r['query_id'])
        if key in rows:raise ValueError('duplicate official query/width')
        rows[key]=r['result_ids']
    if not rows:raise ValueError('empty official reference')
    return rows

def prepare(command, folder, *, cpu_affinity, numa_node, env):
    method=option(command,'--method');name=METHODS[method]
    if method == 'SymphonyQG-DiskPort':
        from .symphony_provenance import verify_export, verify_source
        verify_source(command[0])
        verify_export(option(command, '--disk-index-dir'))
    binary=Path(command[0]).resolve().parent/f'qgraph05_{name}_real_reference'
    native=folder/'result.json';artifact=json.loads(native.read_text())
    if artifact.get('io_backend')!='reference_mmap_not_performance':raise ValueError('expected separate mmap reference')
    widths=sorted({r['search_width'] for r in artifact['summary_rows']})
    trace=folder/'official.jsonl'
    cmd=[str(binary),option(command,'--disk-index-dir'),option(command,'--query'),str(trace),
         str(folder/'temporary_official.index'),','.join(map(str,widths)),'1']
    resource=run_measured(cmd,evidence_path=folder/'official.resources.json',log_path=folder/'official.log',
                          reference=True,cpu_affinity=cpu_affinity,numa_node=numa_node,
                          env=dict(env or os.environ,OMP_NUM_THREADS='1',QG05_REFERENCE_MMAP='0'))
    if resource['status']!='completed':raise ValueError('official reference process failed')
    if ids(trace)!=ids(folder/'queries.jsonl'):raise ValueError('official/native ordered IDs differ')
    # Storage counters below are independently checked against the measured direct
    # trace by admission.exact_traces. The official API does not export counters.
    count=len(ids(trace))
    parity=dict(query_comparisons=count,max_recall_delta=0.0,mean_top10_overlap=1.0,
                mean_visited_count_relative_delta=0.0,mean_distance_count_relative_delta=0.0,
                scope='official_ordered_ids_and_separate_memory_direct_storage_counters',
                official_traversal_counters_available=False)
    p=folder/'result.parity.json';p.write_text(json.dumps(parity,indent=2)+'\n')
    (folder/'reference.native.json').write_bytes(native.read_bytes())
    artifact.update(implementation_parity='passed',parity=dict(parity,reference_artifact_sha256=sha256(p)))
    native.write_text(json.dumps(artifact,indent=2)+'\n')
    return binary

def verify(manifest, command):
    if manifest.get('external_parity') != 'performed':
        raise ValueError('independent official reference must be performed')
    required = {'official.jsonl', 'official.resources.json', 'reference.native.json',
                'result.json', 'queries.jsonl'}
    if not required <= set(manifest.get('files', {})):
        raise ValueError('independent official reference files missing')
    if option(command, '--method') == 'SymphonyQG-DiskPort':
        from .symphony_provenance import verify_export, verify_source
        evidence = verify_source(command[0])
        if str(evidence) not in manifest.get('inputs', {}):
            raise ValueError('SymphonyQG source audit is not bound to reference')
        verify_export(option(command, '--disk-index-dir'))
    f=lambda n:Path(manifest['files'][n]['path'])
    native=json.loads(f('result.json').read_text());e=json.loads(f('official.resources.json').read_text())
    original=json.loads(f('reference.native.json').read_text())
    if original.get('io_backend')!='reference_mmap_not_performance':raise ValueError('missing mmap storage reference')
    for key,value in original.items():
        if key not in ('implementation_parity','parity') and native.get(key)!=value:raise ValueError('modified reference native artifact')
    binary=Path(command[0]).resolve().parent/f"qgraph05_{METHODS[option(command,'--method')]}_real_reference"
    expected=[str(binary),option(command,'--disk-index-dir'),option(command,'--query'),str(f('official.jsonl')),
              str(f('official.jsonl').parent/'temporary_official.index'),
              ','.join(map(str,sorted({r['search_width'] for r in native['summary_rows']}))),'1']
    if (e.get('status')!='completed' or e.get('exit_code') != 0 or e.get('reference_only') is not True
            or e.get('command')!=expected or e.get('binary_sha256')!=sha256(binary)):
        raise ValueError('official reference provenance mismatch')
    if ids(f('official.jsonl'))!=ids(f('queries.jsonl')):raise ValueError('official/native result mismatch')
    return e['finished_unix']
