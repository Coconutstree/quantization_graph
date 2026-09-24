"""03 bridge to pinned official CLIs; no Python implementation of ANN search.

Reference and measured searches use the same audited official search call. The
reference has optional trace export disabled. It tests instrumentation parity,
not an independent proof of the upstream ANN algorithm.
"""
from __future__ import annotations
import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import numpy as np
from .native_contract import atomic_write_json, sha256_file, ContractError
from .system03 import PROTOCOL, WIDTHS, BUDGET_BYTES, verify_disjoint, freeze
from .memory_runner import run_measured_isolated
from .storage_precondition import prepare_search, PROTOCOL as STORAGE_PROTOCOL
from .official_sources import verify_source

ROOT = Path(__file__).resolve().parents[2]
METHODS = {'Starling-Disk':'starling', 'AiSAQ-Disk':'aisaq'}
KIND = 'official_cli_system03_v1'


def adapter():
    path=ROOT/'experiments/03_disk_system/adapters/run_official_disk_baseline.py'
    spec=importlib.util.spec_from_file_location('official03_adapter',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def configuration(method,dataset):
    # Fixed pinned examples; never use validation throughput to choose these.
    starling = method == 'Starling-Disk'
    source = ('baselines/starling/scripts/config_sample.sh' if starling else
              'baselines/aisaq/workflows/AiSAQ_index.md (all-inline example and default search)')
    return dict(degree=48 if starling else 64,
                build_width=128 if starling else 125,pq_bytes=32,build_threads=8 if starling else 3,
                build_memory_gib=32. if starling else 128.,
                beam=4,workers=32,k=10,node_cache=0,inline_pq=64,rearrange=False,
                vector_beam=1,pq_cache='0',pq_page_cache='0',nav_sample=.01,
                nav_degree=48,nav_build_width=128,nav_width=0,partition_iterations=16,
                page_ratio=1.,parameter_source=source,
                parameter_exceptions={'beam_workers_widths':'user fixed protocol',
                    'pq_bytes':'AiSAQ official example; Starling B=0.3 GiB / BIGANN10M implies 32 bytes',
                    'build_threads':'Starling sample=8; AiSAQ preparation limited to 3 threads'},
                compatibility_exception='GIST R48 page fit' if starling and dataset=='gist' else None)


def inventory(root):
    return {str(p.resolve()):sha256_file(p) for p in sorted(Path(root).iterdir())
            if p.is_file() and p.name.startswith(('index','nav_'))}


def check_files(files):
    if not files or any(not Path(p).is_file() or sha256_file(Path(p))!=digest for p,digest in files.items()):
        raise ContractError('official source/index/result evidence changed')


def deployed_files(files, method, cfg):
    """Runtime index files only; omit partition iterations and training samples."""
    prefixes=['index_pq_']
    prefixes += ['index_partition_tmp.index'] if method=='Starling-Disk' else ['index_disk.index']
    if method=='Starling-Disk' and cfg['nav_width']>0:
        prefixes.append('nav_index')
    return {path:digest for path,digest in files.items() if Path(path).name.startswith(tuple(prefixes))}


def read_bin(path,dtype):
    with Path(path).open('rb') as f:
        header=f.read(8)
        if len(header)!=8: raise ContractError('truncated official result header')
        n,d=struct.unpack('<II',header)
        a=np.fromfile(f,dtype=dtype)
    if not n or d!=10 or a.size!=n*d: raise ContractError('official result geometry mismatch')
    return a.reshape(n,d)


def summarize(raw,gt_path,base_path,reference_ids,order,width,workers):
    from .common import fvecs,ivecs
    ids=read_bin(raw/'result_ids.bin','<u4')
    distances=read_bin(raw/'result_distances.bin','<f4')
    ref=read_bin(reference_ids,'<u4')
    if not np.array_equal(ids,ref): raise ContractError('official CLI instrumentation changes ordered results')
    traces=[json.loads(line) for line in (raw/'stats.jsonl').read_text().splitlines() if line.strip()]
    truth=ivecs(gt_path)
    with Path(base_path).open('rb') as stream:
        dimension, = struct.unpack('<i', stream.read(4))
    base=np.memmap(base_path,dtype='<f4',mode='r').reshape(-1,dimension+1)[:,1:]
    query=fvecs(raw/'query.fvecs')
    if len(ids)!=len(order) or len(traces)!=len(ids) or len(truth)!=len(order):
        raise ContractError('official query/trace coverage differs')
    if sorted(map(int,order)) != list(range(len(ids))):
        raise ContractError('query order is not a permutation')
    walls=set(); recalls=[]; lat=[]; output=[]
    for i,(row,ds,t) in enumerate(zip(ids,distances,traces)):
        if t['query_id']!=i or t['search_width']!=width or len(set(map(int,row)))!=10 or np.any(row>=len(base)):
            raise ContractError('invalid official query IDs/width')
        exact=np.sum((base[row].astype(np.float64)-query[i].astype(np.float64))**2,axis=1)
        if not np.allclose(ds,exact,rtol=1e-4,atol=1e-4):
            raise ContractError('official distances differ from input squared L2')
        recall=len(set(map(int,row)) & set(map(int,truth[order[i],:10])))/10
        wall=float(t['search_wall_seconds']); latency=float(t['latency_us'])
        if not math.isfinite(wall) or wall<=0 or not math.isfinite(latency) or latency<0:
            raise ContractError('invalid official timing')
        walls.add(wall);recalls.append(recall);lat.append(latency)
        output.append(dict(t,query_id=int(order[i]),returned_ids=list(map(int,row)),recall_at_10=recall))
    if len(walls)!=1: raise ContractError('inconsistent official wall clock')
    wall=walls.pop()
    summary=dict(config_id='beam4',search_param=str(width),search_width=width,beam_width=4,
                 query_count=len(ids),recall=float(np.mean(recalls)),qps=len(ids)/wall,
                 latency_mean_us=float(np.mean(lat)),latency_p50_us=float(np.percentile(lat,50)),
                 latency_p95_us=float(np.percentile(lat,95)),latency_p99_us=float(np.percentile(lat,99)),
                 io_requests_per_query=float(np.mean([t['io_requests'] for t in traces])),
                 bytes_read_per_query=None,sectors_4k_per_query=None,
                 distance_evaluations=float(np.mean([t['distance_evaluations'] for t in traces])),
                 io_counter_scope='official QueryStats; complete device/PQ read bytes unavailable')
    return summary,output


def invoke(*,port,item,phase,dataset,run_id,data_root,disk_root,query_path,gt_path,
           split_sha256,artifact_path,input_manifest,input_manifest_sha256,seed,tuning_lock,
           measurement_width,warmup_query,cpu_affinity,numa_node,experiment,**unused):
    if item.spec.method not in METHODS: raise ContractError('unsupported official bridge method')
    method=item.spec.method; name=METHODS[method]
    dep=verify_source(ROOT,name)
    if port.get('source_snapshot', dep) != dep:
        raise ContractError('official source no longer matches registry build snapshot')
    build_root=Path(port['official_build_root']).resolve()
    native=build_root/name/('apps' if name=='aisaq' else 'tests')/'search_disk_index'
    if sha256_file(native)!=port['binary_sha256']: raise ContractError('official native binary changed')
    index=disk_root/'05_disk_system_fair'/'03_disk_system'/dataset/method/'hybrid_disk'
    cfg=configuration(method,dataset)
    module=adapter()
    a=module.parser().parse_args(['--method',method,'--base',str(data_root/dataset/f'{dataset}_base.fvecs'),
                                  '--query',str(query_path),'--work-dir',str(index)])
    for k,v in cfg.items():
        if hasattr(a,k): setattr(a,k,v)
    a.build_root=build_root
    with a.base.open('rb') as f: dim,=struct.unpack('<i',f.read(4))
    n=a.base.stat().st_size//(4+4*dim)
    from .system03 import unsupported_reason
    reason=unsupported_reason(method,dim,cfg['degree'])
    if reason: raise ContractError(reason)
    asset=index/'official_assets.json'
    artifact_path.parent.mkdir(parents=True,exist_ok=True)
    provenance=dict(source_commit=dep['commit'],source_patches=dep.get('patches',[]),
                    configuration=cfg,base_sha256=sha256_file(a.base))
    env=dict(os.environ,OMP_NUM_THREADS='32',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    env.pop('QG05_WARMUP_QUERIES',None)
    for key in ('QG03_WARMUP_BIN','QG03_WARMUP_COUNT','QG05_OFFICIAL_TRACE_PREFIX'):
        env.pop(key,None)
    if phase=='export':
        if asset.exists():
            saved=json.loads(asset.read_text())
            if any(saved.get(k)!=v for k,v in provenance.items()): raise ContractError('official index provenance changed')
            check_files(saved['files'])
        else:
            index.mkdir(parents=True,exist_ok=False)
            module.fvecs_to_bin(a.base,index/'base.bin')
            commands=module.commands(a,n)[:-1]
            build_binaries={c[0]:sha256_file(Path(c[0])) for c in commands}
            for i,cmd in enumerate(commands):
                bound=['taskset','-c',','.join(map(str,cpu_affinity)), 'numactl','--membind='+str(numa_node),*cmd]
                with (index/f'build_{i}.log').open('x') as log:
                    subprocess.run(bound,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,
                                   env=dict(env,OMP_NUM_THREADS=str(a.build_threads)),check=True)
            freeze(asset,dict(provenance,files=inventory(index),build_binaries=build_binaries))
    if not asset.is_file(): raise ContractError('missing official index export')
    saved=json.loads(asset.read_text());check_files(saved['files'])
    if any(saved.get(k)!=v for k,v in provenance.items()): raise ContractError('official configuration/index lock mismatch')
    art=dict(schema_version=2,artifact_kind=KIND,system03_protocol=PROTOCOL,status='done',
             layer='05c',method=method,dataset=dataset,phase=phase,run_id=run_id,repeat_id=item.repeat_id,
             workers=item.workers,search_dram_budget_gib=item.budget_gib,storage_mode='hybrid_disk',cache_mode='standard',
             source_suite=item.spec.source_suite,source_kernel=item.spec.source_kernel,port_kind=item.spec.port_kind,
             implementation_fingerprint=port['implementation_fingerprint'],native_binary_sha256=port['binary_sha256'],
             input_manifest_sha256=input_manifest_sha256,query_split_sha256=split_sha256,
             source_index_manifest_sha256=sha256_file(asset),official_index_manifest=str(asset.resolve()),
             index_path=str(index.resolve()),
             index_size_mb=sum(Path(p).stat().st_size for p in deployed_files(saved['files'],method,cfg))/2**20,
             deployed_index_files=deployed_files(saved['files'],method,cfg),
             index_size_scope='runtime index and quantization files; excludes build-only graph/samples/partitions',
             source_commit=dep['commit'],source_patches=dep.get('patches',[]),official_configuration=cfg,
             source_snapshot=dep,source_snapshot_sha256=hashlib.sha256(json.dumps(dep,sort_keys=True).encode()).hexdigest(),
             experiment=experiment,formal_ready=False,summary_rows=[])
    if phase=='export':
        art['formal_ready']=True # Source/index export only; no performance claim.
        atomic_write_json(artifact_path,art);return art
    preflight = phase in ('validate', 'validation')
    if preflight:
        measurement_width = max(WIDTHS)
        warmup_query = query_path
    if (phase!='test' and not preflight) or measurement_width not in WIDTHS or warmup_query is None:
        raise ContractError('official bridge requires isolated fixed test and independent warmup')
    if not preflight:
        lock=json.loads(tuning_lock.read_text())
        selected=lock['selected'][method+'::hybrid_disk']
        if (lock.get('system03_protocol')!=PROTOCOL or selected.get('config_id')!='beam4'
                or selected.get('official_configuration')!=cfg):
            raise ContractError('official frozen parameters changed')
    warm=verify_disjoint(warmup_query,query_path) if not preflight else None
    from .orchestrator import _query_order_file
    order_file,order_hash=_query_order_file(artifact_path,query_path,seed)
    order=np.fromfile(order_file,dtype='<u4')
    from .common import fvecs,write_fvecs
    raw=artifact_path.with_suffix('.official');raw.mkdir(exist_ok=False)
    write_fvecs(raw/'query.fvecs',fvecs(query_path)[order])
    module.fvecs_to_bin(raw/'query.fvecs',raw/'query.bin')
    module.fvecs_to_bin(warmup_query,raw/'warmup.bin')
    a.index_dir=index;a.work_dir=raw;a.widths=[measurement_width]
    cmd=module.commands(a,n)[-1]
    env.update(QG03_WARMUP_BIN=str((raw/'warmup.bin').resolve()),QG03_WARMUP_COUNT='100')
    ref=list(cmd);ref[ref.index('--result_path')+1]=str(raw/'reference')
    refstorage=raw/'reference.storage.json';prepare_search(ref,refstorage,method)
    reference=run_measured_isolated(ref,evidence_path=raw/'reference.resources.json',
                                   log_path=raw/'reference.log',reference=True,env=env,
                                   cpu_affinity=cpu_affinity,numa_node=numa_node)
    if reference['status']!='completed': raise ContractError('official reference process failed')
    storage=raw/'measurement.storage.json';prepare_search(cmd,storage,method)
    evidence=run_measured_isolated(cmd,evidence_path=raw/'resources.json',log_path=raw/'search.log',
                                  rss_budget=True,budget_bytes=BUDGET_BYTES,
                                  env=dict(env,QG05_OFFICIAL_TRACE_PREFIX=str(raw/'query_stats')),
                                  cpu_affinity=cpu_affinity,numa_node=numa_node)
    if evidence['status']!='completed': raise ContractError('official RSS/search admission failed')
    width=measurement_width
    # Keep native files untouched, with stable local aliases for validation.
    import shutil
    for source,target in [(raw/f'result_{width}_idx_uint32.bin',raw/'result_ids.bin'),
                          (raw/f'result_{width}_dists_float.bin',raw/'result_distances.bin'),
                          (raw/f'query_stats_{width}.jsonl',raw/'stats.jsonl')]: shutil.copyfile(source,target)
    ref_ids=raw/f'reference_{width}_idx_uint32.bin'
    summary,traces=summarize(raw,gt_path,a.base,ref_ids,order,width,item.workers)
    trace=artifact_path.with_suffix('.queries.jsonl')
    trace.write_text(''.join(json.dumps(t,allow_nan=False)+'\n' for t in traces))
    files={str(p.resolve()):sha256_file(p) for p in raw.iterdir() if p.is_file()}
    files.update({str(p.resolve()):sha256_file(p) for p in (trace,gt_path,query_path,warmup_query,tuning_lock,input_manifest,order_file) if p is not None})
    summary['peak_rss_bytes']=evidence['process_peak_rss_bytes']
    from .protocol import RESOURCE_FIELDS
    art.update({key:evidence[key] for key in RESOURCE_FIELDS if key in evidence})
    art.update(formal_ready=True,measurement_width=width,independent_warmup=warm,
               query_order_sha256=order_hash,query_order_seed=seed,warmup_queries=100,
               query_trace_path=str(trace.resolve()),query_trace_sha256=sha256_file(trace),
               peak_rss_bytes=evidence['process_peak_rss_bytes'],process_peak_rss_bytes=evidence['process_peak_rss_bytes'],
               planned_budget_bytes=BUDGET_BYTES,budget_admitted=True,memory_enforcement='none',
               resource_measurement_path=str((raw/'resources.json').resolve()),resource_measurement_sha256=sha256_file(raw/'resources.json'),
               storage_precondition_protocol=STORAGE_PROTOCOL,storage_precondition_path=str(storage.resolve()),
               storage_precondition_sha256=sha256_file(storage),summary_rows=[summary],
               evidence_files=files,groundtruth_path=str(gt_path.resolve()),base_path=str(a.base.resolve()),
               measured_query_path=str(query_path.resolve()),query_order_path=str(order_file.resolve()),
               official_raw_path=str(raw.resolve()),reference_ids_path=str(ref_ids.resolve()),
               tuning_lock_path=str(tuning_lock.resolve()) if tuning_lock else None,timing_semantics='search_wall_excludes_recall_evaluation',
               reference_scope='same audited official CLI, optional trace export disabled; not an independent ANN algorithm')
    atomic_write_json(artifact_path,art)
    validate(art,{},disk_root)
    return art


def validate(art,expected,disk_root):
    if (art.get('artifact_kind')!=KIND or art.get('system03_protocol')!=PROTOCOL
            or art.get('method') not in METHODS or art.get('formal_ready') is not True):
        raise ContractError('invalid official CLI 03 artifact')
    for k,v in expected.items():
        if k in ('artifact_path','protocol_id'): continue
        if art.get(k)!=v: raise ContractError('official artifact identity mismatch: '+k)
    index=Path(art['index_path']).resolve()
    if not index.is_relative_to(Path(disk_root).resolve()): raise ContractError('official index outside disk root')
    manifest=Path(art['official_index_manifest'])
    if sha256_file(manifest)!=art['source_index_manifest_sha256']: raise ContractError('official index manifest changed')
    saved=json.loads(manifest.read_text());check_files(saved['files'])
    if saved['configuration']!=art['official_configuration']: raise ContractError('official index/config mismatch')
    if saved['source_commit']!=art['source_commit'] or saved['source_patches']!=art['source_patches']:
        raise ContractError('official source/index provenance mismatch')
    if art.get('source_snapshot'):
        if hashlib.sha256(json.dumps(art['source_snapshot'],sort_keys=True).encode()).hexdigest()!=art['source_snapshot_sha256']:
            raise ContractError('official source snapshot changed')
    if art['phase']=='export': return art
    if art['phase'] not in ('test','validate','validation') or art['workers']!=32 or art['repeat_id']!=0 or art['search_dram_budget_gib']!=4:
        raise ContractError('official fixed configuration drift')
    check_files(art['evidence_files'])
    if sha256_file(Path(art['base_path']))!=saved['base_sha256']: raise ContractError('base changed')
    raw=Path(art['official_raw_path']);resource=json.loads((raw/'resources.json').read_text())
    if (resource.get('status')!='completed' or resource.get('exit_code')!=0
            or resource.get('binary_sha256')!=art['native_binary_sha256']
            or resource.get('rss_budget') is not True or resource.get('budget_admitted') is not True
            or resource.get('planned_budget_bytes')!=BUDGET_BYTES
            or not 0<resource['process_peak_rss_bytes']<=BUDGET_BYTES
            or resource.get('sampled_swap_bytes')!=0):
        raise ContractError('official resource admission failed')
    from .storage_precondition import option
    cmd=resource['command']
    if (option(cmd,'--result_path')!=str(raw/'result') or option(cmd,'-L')!=str(art['measurement_width'])
            or option(cmd,'-W')!='4' or option(cmd,'-T')!='32'):
        raise ContractError('official measured command mismatch')
    if art['phase']=='test':
        proof=verify_disjoint(art['independent_warmup']['warmup_query'],art['measured_query_path'])
        if proof!=art['independent_warmup']: raise ContractError('official warmup identity mismatch')
        lock=json.loads(Path(art['tuning_lock_path']).read_text())
        selected=lock['selected'][art['method']+'::hybrid_disk']
        if lock.get('system03_protocol')!=PROTOCOL or selected.get('official_configuration')!=art['official_configuration']:
            raise ContractError('official parameter lock changed')
        preflight=selected.get('resource_preflight')
        if not preflight or sha256_file(Path(preflight['path']))!=preflight['sha256']:
            raise ContractError('official validation resource preflight missing or changed')
        prior=json.loads(Path(preflight['path']).read_text())
        if prior.get('phase') not in ('validate','validation') or prior.get('official_configuration')!=art['official_configuration']:
            raise ContractError('official validation preflight configuration mismatch')
        validate(prior,dict(method=art['method'],dataset=art['dataset']),disk_root)
    expected_env={'QG03_WARMUP_BIN':str((raw/'warmup.bin').resolve()),'QG03_WARMUP_COUNT':'100',
                  'QG05_OFFICIAL_TRACE_PREFIX':str(raw/'query_stats')}
    if resource.get('protocol_environment')!=expected_env:
        raise ContractError('official independent native warmup not recorded')
    reference=json.loads((raw/'reference.resources.json').read_text())
    refcmd=list(cmd);refcmd[refcmd.index('--result_path')+1]=str(raw/'reference')
    if (reference.get('status')!='completed' or reference.get('exit_code')!=0
            or reference.get('binary_sha256')!=art['native_binary_sha256']
            or reference.get('command')!=refcmd
            or reference.get('protocol_environment')!={k:v for k,v in expected_env.items() if k!='QG05_OFFICIAL_TRACE_PREFIX'}):
        raise ContractError('official uninstrumented reference mismatch')
    if sha256_file(Path(resource['rss_samples_path']))!=resource['rss_samples_sha256']:
        raise ContractError('official RSS samples changed')
    for name in ('reference.storage.json','measurement.storage.json'):
        storage=json.loads((raw/name).read_text())
        if storage.get('status')!='completed' or storage.get('protocol')!=STORAGE_PROTOCOL:
            raise ContractError('official storage precondition failed')
    order=np.fromfile(art['query_order_path'],dtype='<u4')
    if sha256_file(Path(art['query_order_path']))!=art['query_order_sha256']:
        raise ContractError('official query order changed')
    from .common import fvecs
    original=fvecs(Path(art['measured_query_path']))
    if sorted(map(int,order))!=list(range(len(original))) or not np.array_equal(fvecs(raw/'query.fvecs'), original[order]):
        raise ContractError('official converted query does not match frozen input/order')
    for binary, values in ((raw/'query.bin', original[order]),
                            (raw/'warmup.bin', fvecs(Path(art['independent_warmup']['warmup_query'])) if art['phase']=='test' else original)):
        with binary.open('rb') as stream:
            shape=struct.unpack('<II',stream.read(8))
            converted=np.fromfile(stream,dtype='<f4')
        if shape!=values.shape or not np.array_equal(converted,values.ravel()):
            raise ContractError('official lossless input conversion mismatch')
    summary,traces=summarize(raw,Path(art['groundtruth_path']),Path(art['base_path']),
                            art['reference_ids_path'],order,art['measurement_width'],32)
    summary['peak_rss_bytes']=resource['process_peak_rss_bytes']
    if art['summary_rows']!=[summary]: raise ContractError('official summary differs from raw outputs')
    trace=[json.loads(line) for line in Path(art['query_trace_path']).read_text().splitlines()]
    if trace!=traces: raise ContractError('official normalized trace changed')
    return art
