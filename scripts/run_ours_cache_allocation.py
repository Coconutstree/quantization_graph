"""Paired GIST cache allocation experiment. No historical result is overwritten."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/disk_bench'))
import orchestrator  # establish the diskfair package used by the shared entrypoint
from diskfair.admission import exact_traces, replace, write
from diskfair.memory_runner import run_measured
from diskfair.native_contract import validate_artifact, SPECS_BY_KEY
from diskfair.protocol import PROTOCOL_ID, sha256
from diskfair.storage_precondition import prepare_search


def audit_result(path):
    a = json.loads(path.read_text())
    expected = {key:a[key] for key in ('dataset','phase','run_id','repeat_id','workers','storage_mode',
        'cache_mode','implementation_fingerprint','native_binary_sha256','input_manifest_sha256',
        'query_split_sha256','query_order_sha256','query_order_seed','warmup_queries','search_dram_budget_gib')}
    expected['protocol_id'] = PROTOCOL_ID
    validate_artifact(path, spec=SPECS_BY_KEY['05c:Ours-Disk'], expected=expected, disk_root=ROOT/'work')
    return a


def compare_paths(left, right):
    left_files = {p.name:p for p in left.glob('*.tsv')}
    right_files = {p.name:p for p in right.glob('*.tsv')}
    if not left_files or left_files.keys() != right_files.keys():
        raise ValueError('search path query/width coverage differs')
    digests = {}
    for name, p in left_files.items():
        h = sha256(p)
        if h != sha256(right_files[name]):
            raise ValueError(f'expansion sequence differs: {name}')
        digests[name] = h
    return digests


def diagnostic(path, dest, env, cpus):
    a = audit_result(path)
    command = json.loads(path.with_suffix('.resources.json').read_text())['command']
    dest.mkdir(exist_ok=False)
    replace(command, '--result-json', dest/'result.json')
    replace(command, '--query-trace', dest/'queries.jsonl')
    # Disk-only, untimed diagnostic; correctness is supplied by the admitted run.
    replace(command, '--parity-mode', 'external')
    command += ['--ours-search-path-dir', str(dest/'paths')]
    prepare_search(command, dest/'storage.json')
    e = run_measured(command, evidence_path=dest/'resources.json', log_path=dest/'terminal.log',
                     reference=True, env=env, cpu_affinity=cpus, numa_node=0)
    if e['status'] != 'completed':
        raise ValueError(f'search path diagnostic failed: {dest}')
    if json.loads((dest/'result.json').read_text()).get('throughput_comparable') is not False:
        raise ValueError('diagnostic must not claim comparable performance')
    count = exact_traces(dest/'queries.jsonl', a['query_trace_path'])
    write(dest/'evidence.json', dict(performance_sample=False, comparisons=count,
          admitted_artifact=str(path), admitted_artifact_sha256=sha256(path),
          queries_sha256=sha256(dest/'queries.jsonl'), resources_sha256=sha256(dest/'resources.json')))
    return dest/'paths'


def report(out, jobs, pairs):
    rows = []
    for job in jobs:
        if job['status'] != 'admitted': continue
        path = Path(job['artifact']); a = audit_result(path)
        if sha256(path) != job['artifact_sha256']: raise ValueError('source artifact changed')
        caches = {(r['width'],r['beam']):r['cache'] for r in a['ours_record_cache_stats']}
        for source in a['summary_rows']:
            c = caches[(source['search_width'],source['beam_width'])]
            rows.append(dict(budget_gib=job['budget_gib'],strategy=job['strategy'],round=job['round'],
                mode=a['ours_route_plan']['mode'],dimension=a['ours_route_plan']['dimension'],
                graph_cache_bytes=a['ours_graph_cache_bytes'],record_cache_bytes=a['ours_record_cache_reserved_bytes'],
                hot_nodes=c['static_nodes'],dynamic_capacity_nodes=c['dynamic']['capacity_nodes'],
                hot_compact_hits=c['static_hits'][0],hot_residual_hits=c['static_hits'][1],
                dynamic_compact_hits=c['dynamic']['hits'][0],dynamic_residual_hits=c['dynamic']['hits'][1],
                artifact=str(path),artifact_sha256=sha256(path),**source))
    if rows:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (out/'measured_results.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    write(out/'provenance.json', dict(jobs=jobs,path_comparisons=pairs,
        io_note='Packed compact+residual records: physical reads are classified by graph/full4/rerank request stage; do not interpret stages as separate payload files.',
        comparison='Compare measured Recall-QPS curves; do not extrapolate or equate differing Recall. Repetitions are recorded per row and in status.json; no automatic default-policy change.'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument("--rounds", type=int, choices=(1,2), default=1)
    parser.add_argument("--widths", type=orchestrator.parse_search_widths, default=(60,100,180))
    parser.add_argument("--runtime-source-amendment", type=Path)
    parser.add_argument("--resume", action="store_true")
    args=parser.parse_args()
    binary=args.binary.resolve()
    if not binary.is_file(): raise ValueError('build the isolated binary first')
    out=ROOT/'results/diagnostics'/args.tag;out.mkdir(exist_ok=args.resume)
    env=dict(os.environ,PATH=str(ROOT/'work/tools/numactl/root/usr/bin')+os.pathsep+os.environ['PATH'],
             QG05_FAST='0',QG05_REFERENCE_MMAP='0',PYTHONUNBUFFERED='1')
    for key in ('QG05_FAST_WIDTH','QG05_FAST_WIDTHS','QG05_SKIP_EXTERNAL_PARITY'): env.pop(key,None)
    cpus=[i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
    if len(cpus)!=32: raise ValueError('need 32 node-0 CPUs')
    registry=json.loads((ROOT/'src/disk_bench/ports.local.json').read_text())
    port=registry['ports']['05c:Ours-Disk']
    port.update(command=[str(binary)],binary_sha256=sha256(binary),implementation_fingerprint='05c-ours-cache-allocation-v6')
    if args.resume:
        state=json.loads((out/'status.json').read_text())
        if state['binary_sha256'] != sha256(binary): raise ValueError('resume binary changed')
        pinned=json.loads((out/'source_hashes.json').read_text())
        if args.runtime_source_amendment:
            amendment=json.loads(args.runtime_source_amendment.read_text())
            if amendment['previous_source_sha256']!=sha256(out/'source_hashes.json') or amendment['runner_sha256']!=sha256(Path(__file__)):
                raise ValueError('invalid runtime source amendment')
            pinned=amendment['sources']
            state['runtime_source_amendment']=dict(path=str(args.runtime_source_amendment),sha256=sha256(args.runtime_source_amendment))
        if any(sha256(ROOT/name)!=digest for name,digest in pinned.items()):
            raise ValueError('runtime sources changed since queue started')
        state['status']='running'
    else:
        write(out/'ports.json',registry)
        state=dict(status='running',dataset='gist',budgets_gib=[0.5,4],workers=32,beam=4,
            widths=list(args.widths),jobs=[],path_comparisons=[],started_unix=time.time(),
            binary_sha256=sha256(binary),representation_selection='full_else_pca512_256_128_v1')
        sources={str(p.relative_to(ROOT)):sha256(p) for folder in ('experiments/02_disk_shared_graph/native/src','src/disk_bench','src/graph_core/src')
                 for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.rs')}
        write(out/'source_hashes.json',sources)
        write(out/'build_environment.json', dict(binary=str(binary),binary_sha256=sha256(binary),
            build_command=['cargo','build','--release','--manifest-path','src/graph_core/Cargo.toml','--bin','qgraph05_shared_graph_port','--target-dir','work/cache_allocation_build'],
            rustc=subprocess.check_output(['rustc','--version'],text=True).strip(),
            cargo=subprocess.check_output(['cargo','--version'],text=True).strip(),
            git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            cargo_lock_sha256=sha256(ROOT/'src/graph_core/Cargo.lock'),
            cpu_affinity=cpus,numa_node=0,source_hashes_sha256=sha256(out/'source_hashes.json')))
    state['rounds']=args.rounds
    state['path_check']='skipped_by_user_no_path_claim'
    def save(): write(out/'status.json',state)
    save()
    try:
        for budget in state['budgets_gib']:
            shared_profile=frozen=None;first={};representation=None
            for round_id, strategies in enumerate((('graph_first','records_only'),('records_only','graph_first'))[:args.rounds]):
                for strategy in strategies:
                    run_id=f'gist_{args.tag}_b{budget}_{strategy}_r{round_id}'
                    if strategy=='records_only': run_id += '_results_only'
                    folder=out/run_id;folder.mkdir(exist_ok=args.resume)
                    cmd=[sys.executable,str(ROOT/'experiments/05_memory_budget/run.py'),'--datasets','gist',
                         '--methods','Ours-Disk','--run-id',run_id,'--ports',str(out/'ports.json'),
                         '--workers','32','--repeats','1','--cpu-affinity',','.join(map(str,cpus)),
                         '--numa-node','0','--search-dram-budget-gib',str(budget),'--fixed-beam','4',
                         '--ours-cache-allocation',strategy,'--search-widths',','.join(map(str,state['widths'])),'--phase','run','--method-pipeline']
                    if shared_profile: cmd += ['--ours-hot-profile',str(shared_profile),'--ours-frozen-route',str(frozen)]
                    if strategy=='records_only':
                        cmd += ['--ours-reference-source',str(first['graph_first'])]
                    previous=next((j for j in state['jobs'] if j['run_id']==run_id),None)
                    if previous is not None:
                        job=previous
                        expected=json.loads((folder/'command.json').read_text())
                        if cmd!=expected: raise ValueError('resume command changed')
                        if job['status']=='running':
                            # Adopt only the already launched exact command; never signal it.
                            proc=Path(f"/proc/{job['pid']}")
                            while proc.exists():
                                try:
                                    fields=(proc/'stat').read_text().split(') ',1)[1].split()
                                    if fields[0]=='Z': break
                                    actual=(proc/'cmdline').read_bytes().decode().rstrip('\0').split('\0')
                                    if actual!=expected: raise ValueError('adopted PID command changed')
                                except FileNotFoundError: break
                                time.sleep(1)
                            if f'formal phase complete: run_id={run_id}' not in (folder/'run.log').read_text():
                                job['status']='failed';save();raise ValueError('adopted pipeline did not finish admission')
                            job['adopted_completion_verified']=True
                        elif job['status']!='admitted': raise ValueError('cannot resume failed attempt')
                    else:
                        job=dict(run_id=run_id,budget_gib=budget,strategy=strategy,round=round_id,status='running')
                        state['jobs'].append(job);save();write(folder/'command.json',cmd)
                        with (folder/'run.log').open('x') as log:
                            p=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                            job['pid']=p.pid;save();code=p.wait()
                        job['exit_code']=code
                        if code: job['status']='failed';save();raise ValueError(f'pipeline failed: {folder}')
                    raw=ROOT/'results/05_memory_budget/gist'/run_id/'raw/Ours-Disk/test'
                    artifacts=list(raw.glob('*__r0.json'))
                    if len(artifacts)!=1: raise ValueError('missing/ambiguous test artifact')
                    artifact=artifacts[0];a=audit_result(artifact)
                    identity=(a['ours_route_plan'],a['pca_assets_sha256'],a['ours_hot_ranks_sha256'],a['source_graph_sha256'])
                    if representation is None:
                        representation=identity;shared_profile=Path(a['ours_hot_profile_manifest'])
                        frozen=out/f'budget_{budget}.route_plan.json';write(frozen,a['ours_route_plan'])
                    elif identity != representation: raise ValueError('paired representation/assets/hot ranking changed')
                    job.update(status='admitted',artifact=str(artifact),artifact_sha256=sha256(artifact));save()
                    if round_id==0: first[strategy]=artifact
                    else: exact_traces(a['query_trace_path'],audit_result(first[strategy])['query_trace_path'])
                if round_id==0:
                    state['phase']='result_comparison';save()
                    left=audit_result(first['graph_first']);right=audit_result(first['records_only'])
                    count=exact_traces(left['query_trace_path'],right['query_trace_path'])
                    evidence=out/f'results_b{budget}.json'
                    write(evidence,dict(status='passed',comparisons=count,expansion_paths_checked=False,
                        left=str(first['graph_first']),right=str(first['records_only']),
                        left_sha256=sha256(first['graph_first']),right_sha256=sha256(first['records_only'])))
                    state.setdefault('result_comparisons',[]).append(dict(budget_gib=budget,status='passed',path=str(evidence),sha256=sha256(evidence)))
                    state['path_check']='skipped_by_user_no_path_claim';save()
                report(out,state['jobs'],state['path_comparisons'])
                if budget == 0.5:
                    from plot_ours_cache_allocation import draw
                    draw(out, budget)
                    state['figure_0_5'] = str(out/'figures/recall_qps_b0.5.svg');save()
        state['status']='completed'
    except Exception as exc:
        state.update(status='failed',error=repr(exc));raise
    finally:
        state['updated_unix']=time.time();save()

if __name__=='__main__': main()
