"""Ours records_only admission via an admitted graph_first source and exact paths.

This is an explicit chained proof, never a fabricated internal-reference run.
"""
import json
from pathlib import Path
from .protocol import sha256, PROTOCOL_ID
from .storage_precondition import option, prepare_search
from .memory_runner import run_measured_isolated

KIND='ours_cache_pair_exact_paths_v1'
RESULT_KIND='ours_cache_pair_results_only_v1'
IGNORED={'--result-json','--query-trace','--parity-mode','--run-id','--ours-cache-allocation',
         '--tuning-lock','--input-manifest','--query-order'}


def signature(command):
    result=[];i=0
    while i<len(command):
        if command[i] in IGNORED:i+=2
        else:result.append(command[i]);i+=1
    return result


def source_artifact(path):
    from .native_contract import validate_artifact,SPECS_BY_KEY
    a=json.loads(Path(path).read_text())
    manifest=json.loads(Path(a['separate_reference_path']).read_text())
    if (a.get('method')!='Ours-Disk' or a.get('ours_cache_allocation')!='graph_first'
            or manifest.get('reference_kind') in (KIND,RESULT_KIND)):
        raise ValueError('paired source must be independently admitted graph_first')
    expected={k:a[k] for k in ('dataset','phase','run_id','repeat_id','workers','storage_mode','cache_mode',
        'implementation_fingerprint','native_binary_sha256','input_manifest_sha256','query_split_sha256',
        'query_order_sha256','query_order_seed','warmup_queries','search_dram_budget_gib')}
    expected['protocol_id']=PROTOCOL_ID
    resource=json.loads(Path(a['resource_measurement_path']).read_text())
    index=Path(option(resource['command'],'--disk-index-dir'))
    validate_artifact(Path(path),spec=SPECS_BY_KEY['05c:Ours-Disk'],expected=expected,disk_root=index.parent)
    return a,resource


def check_commands(source,target):
    if (option(target,'--method')!='Ours-Disk' or option(target,'--ours-cache-allocation')!='records_only'
            or option(source,'--ours-cache-allocation')!='graph_first' or signature(source)!=signature(target)):
        raise ValueError('cache-pair command changes more than allocation/run outputs')
    # Parameter lock paths may differ, but only the cache allocation may change.
    if bool(option(source,'--tuning-lock'))!=bool(option(target,'--tuning-lock')):
        raise ValueError('cache-pair tuning lock missing')
    if option(source,'--tuning-lock'):
        locks=[]
        for c in (source,target):
            lock=json.loads(Path(option(c,'--tuning-lock')).read_text())['selected']['Ours-Disk::hybrid_disk']
            lock.pop('ours_cache_allocation',None);locks.append(lock)
        if locks[0]!=locks[1]:raise ValueError('cache-pair frozen parameters changed')


def paths(folder):
    return {p.name:sha256(p) for p in sorted(Path(folder).glob('*.tsv'))}


def prepare(command,artifact,source_path,*,cpu_affinity=None,numa_node=None,env=None):
    from .admission import PROTOCOL,canonical,replace,input_snapshot,unchanged,exact_traces,write
    source_path=Path(source_path).resolve();a,sr=source_artifact(source_path)
    check_commands(sr['command'],command)
    folder=Path(artifact).with_suffix('.reference');folder.mkdir(exist_ok=False)
    preparation=prepare_search(command,folder/'storage.json')
    inputs=input_snapshot(command,preparation)
    files={'source_artifact':{'path':str(source_path),'sha256':sha256(source_path)}}
    counts=[];path_sets=[]
    for name,original in [('graph_first',sr['command']),('records_only',command)]:
        dest=folder/name;dest.mkdir()
        cmd=list(original)
        replace(cmd,'--result-json',dest/'result.json');replace(cmd,'--query-trace',dest/'queries.jsonl')
        replace(cmd,'--parity-mode','external');cmd+=['--ours-search-path-dir',str((dest/'paths').resolve())]
        prepare_search(cmd,dest/'storage.json')
        e=run_measured_isolated(cmd,evidence_path=dest/'resources.json',log_path=dest/'terminal.log',
             reference=True,cpu_affinity=cpu_affinity,numa_node=numa_node,env=env)
        if e['status']!='completed':raise ValueError(f'paired diagnostic failed: {name}')
        counts.append(exact_traces(dest/'queries.jsonl',a['query_trace_path']))
        path_sets.append(paths(dest/'paths'))
        for filename in ('result.json','queries.jsonl','resources.json','storage.json'):
            f=dest/filename;files[name+'/'+filename]={'path':str(f.resolve()),'sha256':sha256(f)}
    if not path_sets[0] or path_sets[0]!=path_sets[1] or len(path_sets[0])!=counts[0] or counts[0]!=counts[1]:
        raise ValueError('paired expansion paths or coverage differ')
    unchanged(inputs)
    m=folder/'manifest.json'
    write(m,dict(protocol=PROTOCOL,reference_kind=KIND,preparation_only=True,performance_sample=False,
        external_parity='not_applicable',command=canonical(command),inputs=inputs,files=files,
        search_paths=path_sets[0],comparisons=counts[0]))
    measured=list(command);replace(measured,'--parity-mode','external')
    return measured,m


def prepare_results(command,artifact,source_path,*,cpu_affinity=None,numa_node=None,env=None):
    """Reuse independent graph_first evidence; launch no diagnostic searches."""
    from .admission import PROTOCOL,canonical,replace,input_snapshot,write
    source_path=Path(source_path).resolve();a,sr=source_artifact(source_path)
    check_commands(sr['command'],command)
    folder=Path(artifact).with_suffix('.reference');folder.mkdir(exist_ok=False)
    storage=json.loads(Path(a['storage_precondition_path']).read_text())
    inputs=input_snapshot(command,storage)
    m=folder/'manifest.json'
    write(m,dict(protocol=PROTOCOL,reference_kind=RESULT_KIND,preparation_only=True,performance_sample=False,
        external_parity='not_applicable',command=canonical(command),inputs=inputs,
        files={'source_artifact':{'path':str(source_path),'sha256':sha256(source_path)}},
        comparisons=sum(r['query_count'] for r in a['summary_rows']),expansion_paths_checked=False))
    measured=list(command);replace(measured,'--parity-mode','external')
    return measured,m


def verify(artifact,resource,manifest_path,m):
    from .admission import canonical,unchanged,exact_traces,audit_trace,validate_storage
    if (m.get('preparation_only') is not True or m.get('performance_sample') is not False
            or m['command']!=canonical(resource['command']) or option(resource['command'],'--parity-mode')!='external'):
        raise ValueError('invalid cache-pair manifest/measurement command')
    unchanged(m['inputs'])
    for item in m['files'].values():
        if sha256(item['path'])!=item['sha256']:raise ValueError('cache-pair evidence changed')
    a,sr=source_artifact(m['files']['source_artifact']['path'])
    check_commands(sr['command'],resource['command'])
    for key in ('ours_route_plan','pca_assets_sha256','ours_hot_ranks_sha256','source_graph_sha256'):
        if a.get(key)!=artifact.get(key):raise ValueError(f'cache-pair representation changed: {key}')
    prep=validate_storage(artifact,resource)
    for f in prep['files']:
        if m['inputs'][f['path']]['sha256']!=f['sha256']:raise ValueError('paired measured index changed')
    if m.get('reference_kind')==RESULT_KIND:
        if (sr['finished_unix']>prep['started_unix'] or sr['launch_policy']!=resource['launch_policy']):
            raise ValueError('source must precede measurement with the same launch policy')
        if sha256(artifact['query_trace_path'])!=artifact['query_trace_sha256']:
            raise ValueError('measured trace changed')
        n=exact_traces(a['query_trace_path'],artifact['query_trace_path'])
        audit_trace(artifact,resource['command'])
        if n!=m['comparisons'] or n!=sum(r['query_count'] for r in artifact['summary_rows']):
            raise ValueError('paired result coverage differs')
        return dict(a['parity'],query_comparisons=n,reference_artifact_sha256=sha256(manifest_path),
                    proof_chain=RESULT_KIND,source_artifact_sha256=m['files']['source_artifact']['sha256'],
                    expansion_paths_checked=False)
    for name,original in [('graph_first',sr['command']),('records_only',resource['command'])]:
        def file(n):return Path(m['files'][name+'/'+n]['path'])
        e=json.loads(file('resources.json').read_text());r=json.loads(file('result.json').read_text())
        cmd=e['command'];clean=list(cmd);i=clean.index('--ours-search-path-dir');del clean[i:i+2]
        if (e.get('status')!='completed' or e.get('exit_code')!=0 or not e.get('reference_only')
                or e['binary_sha256']!=resource['binary_sha256'] or e['launch_policy']!=resource['launch_policy']
                or canonical(clean)!=canonical(original) or e['finished_unix']>prep['started_unix']
                or r.get('throughput_comparable') is not False):
            raise ValueError('invalid paired diagnostic resource evidence')
        if paths(option(cmd,'--ours-search-path-dir'))!=m['search_paths']:
            raise ValueError('paired search paths changed')
        if exact_traces(file('queries.jsonl'),a['query_trace_path'])!=m['comparisons']:
            raise ValueError('paired trace coverage changed')
    if sha256(artifact['query_trace_path'])!=artifact['query_trace_sha256']:
        raise ValueError('measured trace changed')
    n=exact_traces(a['query_trace_path'],artifact['query_trace_path']);audit_trace(artifact,resource['command'])
    if n!=m['comparisons'] or n!=len(m['search_paths']) or n!=sum(r['query_count'] for r in artifact['summary_rows']):
        raise ValueError('paired proof does not cover measured queries')
    return dict(a['parity'],query_comparisons=n,reference_artifact_sha256=sha256(manifest_path),
                proof_chain=KIND,source_artifact_sha256=m['files']['source_artifact']['sha256'])
