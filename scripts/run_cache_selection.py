"""Select Ours cache allocation on validation, freeze, then replay selected test only."""
import argparse
import csv
import json
import os
from pathlib import Path
import sys
import threading
import time

from run_ours_cache_allocation import ROOT,audit_result,sha256,write
from remeasure_cache_serial import active_io
from cache_selection_policy import WIDTHS,STRATEGIES,RULE,choose,verify_lock,cache_audit
from diskfair.admission import exact_traces,audit_trace,replace
from diskfair.memory_runner import run_measured_isolated
from diskfair.storage_precondition import prepare_search,option,search_files

IDENTITY_KEYS=('dataset','native_binary_sha256','ours_route_plan','pca_assets_sha256','ours_hot_ranks_sha256','source_graph_sha256','workers','query_cache_budget_per_worker_bytes')

def identity(a):return {k:a[k] for k in IDENTITY_KEYS}

def run_job(cmd,dest,env,policy,budget,reference=False):
    dest.mkdir(parents=True,exist_ok=False)
    rid=dest.name+'_'+str(time.time_ns())
    for k,v in [('--run-id',rid),('--result-json',dest/'result.json'),('--query-trace',dest/'queries.jsonl'),('--parity-mode','internal' if reference else 'external')]:replace(cmd,k,v)
    write(dest/'command.json',cmd)
    while active_io():time.sleep(3)
    prepare_search(cmd,dest/'storage.json')
    if active_io():raise ValueError('I/O competitor appeared before measurement')
    stopped=threading.Event();overlap=[]
    def watch():
        while not stopped.is_set():overlap.extend(active_io(rid));stopped.wait(1)
    t=threading.Thread(target=watch,daemon=True);t.start()
    try:
        kw=dict(reference=True) if reference else dict(budget_bytes=budget,rss_budget=True)
        e=run_measured_isolated(cmd,evidence_path=dest/'resources.json',log_path=dest/'terminal.log',env=env,cwd=ROOT,
                              cpu_affinity=policy['cpu_affinity'],numa_node=policy['numa_node'],**kw)
    finally:stopped.set();t.join()
    write(dest/'concurrency.json',dict(overlap=overlap,scope='known ANN/fio process-name monitoring; no exclusive-host claim'))
    if e['status']!='completed':return None,e,e['status']
    a=json.loads((dest/'result.json').read_text());audit_trace(a,cmd)
    if overlap:return a,e,'concurrent_io'
    if reference:
        parity=json.loads((dest/'result.parity.json').read_text())
        if parity['query_comparisons']!=600 or parity['mean_top10_overlap']!=1 or parity['max_recall_delta']!=0 or parity['mean_distance_count_relative_delta']!=0 or parity['mean_visited_count_relative_delta']!=0:raise ValueError('independent validation reference mismatch')
    return a,e,None

def export_csv(path,rows):
    with path.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)

def plot(out,rows):
    os.environ.setdefault('MPLCONFIGDIR','/tmp/qgraph-cache-figure-mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'svg.fonttype':'none','pdf.fonttype':42})
    fig,axes=plt.subplots(1,len(rows),figsize=(5*len(rows),3.6),squeeze=False,layout='constrained')
    for ax,item in zip(axes[0],rows):
        pts=item['points'];ax.plot([r['recall'] for r in pts],[r['qps'] for r in pts],'o-')
        for r in pts:ax.annotate(str(r['search_width']),(r['recall'],r['qps']),xytext=(3,5),textcoords='offset points')
        ax.set(xlabel='Recall@10',ylabel='QPS',title=f"{item['budget']} GiB · {item['strategy']}",ylim=(0,None));ax.grid(alpha=.2)
    for ext in ('svg','pdf','png'):fig.savefig(out/f'selected_recall_qps.{ext}',dpi=300)
    plt.close(fig)
    write(out/'figure_provenance.json',dict(source='test_results.csv',sha256=sha256(out/'test_results.csv'),
        selection='validation-only frozen rule',test_scope='previously observed test800; not a new unseen test set',uncertainty='one selected test repeat',visual_review='pending',publication_ready=False))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-queue',type=Path,default=ROOT/'results/diagnostics/gist_cache_three_widths_20260923')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budgets',default='0.5,4')
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    state=dict(status='running',jobs=[],budgets=[float(b) for b in args.budgets.split(',')],rule=RULE,started_unix=time.time())
    def save():state['updated_unix']=time.time();write(out/'status.json',state)
    save();write(out/'rule.json',RULE)
    env=dict(os.environ,QG05_FAST='0',QG05_REFERENCE_MMAP='0')
    for k in ('QG05_SKIP_EXTERNAL_PARITY','QG05_FAST_WIDTH','QG05_FAST_WIDTHS'):env.pop(k,None)
    jobs=json.loads((args.source_queue/'status.json').read_text())['jobs'];allval=[];alltest=[];plots=[];audits=[]
    try:
        for budget in state['budgets']:
            bdir=out/f'ram{budget:g}';bdir.mkdir();source={};commands={};pinned={};common=None
            for strategy in STRATEGIES:
                j=next(j for j in jobs if j.get('budget_gib')==budget and j['strategy']==strategy and j['status']=='admitted')
                path=Path(j['artifact']);a=audit_result(path);source[strategy]=a
                if sha256(path)!=j['artifact_sha256']:raise ValueError('source changed')
                resources=path.with_suffix('.resources.json');e=json.loads(resources.read_text());cmd=e['command'];commands[strategy]=cmd
                if common is None:common=identity(a);policy=e['launch_policy']
                elif common!=identity(a) or policy!=e['launch_policy']:raise ValueError('source representation or launch policy differs')
                audits.append(dict(budget=budget,strategy=strategy,source=str(path),**cache_audit(a)))
                for f in [path,resources,Path(a['ours_hot_profile_manifest']),Path(a['query_trace_path'])]:pinned[str(f)]=sha256(f)
            base=commands['graph_first'];profile=Path(source['graph_first']['ours_hot_profile_manifest']).parent
            prof=json.loads((profile/'resources.json').read_text())['command']
            val=list(base)
            for key in ('--phase','--query','--groundtruth','--query-split-sha256','--query-order','--query-order-sha256','--candidate-row-offset'):replace(val,key,option(prof,key))
            # Pin all directly named input files plus the actual disk-search files.
            for command in (base,val,commands['records_only']):
                for i,arg in enumerate(command):
                    if i and command[i-1] in ('--result-json','--query-trace'):continue
                    f=Path(arg)
                    if f.is_file():pinned[str(f.resolve())]=sha256(f)
            for f in search_files(base):pinned[str(f)]=sha256(f)
            for f in [Path(__file__),ROOT/'scripts/cache_selection_policy.py']:pinned[str(f)]=sha256(f)
            write(bdir/'input_hashes.json',pinned);write(out/'implementation_audit.json',audits)
            def check_inputs():
                for f,h in pinned.items():
                    if sha256(Path(f))!=h:raise ValueError('input changed: '+f)
            refdir=bdir/'validation_reference';job=dict(budget=budget,phase='reference',status='running');state['jobs'].append(job);save()
            ref,e,err=run_job(list(val),refdir,env,policy,int(budget*2**30),True)
            if err:raise ValueError('reference failed: '+err)
            if identity(ref)!=common:raise ValueError('reference representation differs')
            job['status']='passed';save();reference_trace=refdir/'queries.jsonl'
            attempts=[]
            for rnd,order in enumerate((STRATEGIES,tuple(reversed(STRATEGIES)))):
                for strategy in order:
                    d=bdir/f'validation_r{rnd}_{strategy}';cmd=list(val);replace(cmd,'--ours-cache-allocation',strategy);replace(cmd,'--repeat-id',rnd)
                    job=dict(budget=budget,phase='validation',strategy=strategy,round=rnd,status='running',directory=str(d));state['jobs'].append(job);save()
                    check_inputs();a,e,err=run_job(cmd,d,env,policy,int(budget*2**30))
                    if a is not None:
                        if identity(a)!=common:raise ValueError('measurement representation differs')
                        cache_audit(a);n=exact_traces(reference_trace,d/'queries.jsonl')
                        if n!=600:raise ValueError('validation coverage mismatch')
                    job.update(status='failed' if err else 'passed',reason=err);save()
                    attempt=dict(strategy=strategy,round=rnd,status=job['status'],points=a['summary_rows'] if a else [],directory=str(d));attempts.append(attempt)
                    write(d/'acceptance.json',dict(status=job['status'],reason=err,reference_trace_sha256=sha256(reference_trace),expansion_paths_checked=False,resources_sha256=sha256(d/'resources.json')))
                    if not err:
                        for r in a['summary_rows']:allval.append(dict(budget=budget,strategy=strategy,round=rnd,measured_peak_rss_bytes=e['observed_peak_rss_bytes'],**r))
            check_inputs();decision=choose(attempts)
            evidence_files=dict(pinned)
            for f in bdir.rglob('*.json*'):evidence_files[str(f)]=sha256(f)
            lock=dict(identity=common,decision=decision,files=evidence_files,launch_policy=policy,widths=list(WIDTHS),workers=32,beam=4,validation_attempts=attempts,test_previously_observed=True)
            lockpath=bdir/'cache_selection.lock.json';write(lockpath,lock);lockhash=sha256(lockpath)
            # Test consumes the on-disk lock. No test results participate in selection.
            frozen=json.loads(lockpath.read_text());selected=frozen['decision']['selected'];verify_lock(frozen,common,selected)
            d=bdir/'selected_test';job=dict(budget=budget,phase='test',strategy=selected,status='running',lock=str(lockpath));state['jobs'].append(job);save()
            a,e,err=run_job(list(commands[selected]),d,env,policy,int(budget*2**30))
            if err:raise ValueError('selected test failed: '+err)
            if sha256(lockpath)!=lockhash:raise ValueError('lock changed during test')
            verify_lock(frozen,identity(a),a['ours_cache_allocation']);cache_audit(a)
            n=exact_traces(source[selected]['query_trace_path'],d/'queries.jsonl')
            if n!=2400:raise ValueError('test coverage mismatch')
            write(d/'acceptance.json',dict(status='passed',lock_sha256=lockhash,comparisons=n,result_sha256=sha256(d/'result.json'),resources_sha256=sha256(d/'resources.json'),reference_reused=True,expansion_paths_checked=False,test_previously_observed=True))
            job['status']='passed';save()
            for r in a['summary_rows']:alltest.append(dict(budget=budget,strategy=selected,measured_peak_rss_bytes=e['observed_peak_rss_bytes'],**r))
            plots.append(dict(budget=budget,strategy=selected,points=a['summary_rows']))
            export_csv(out/'validation_results.csv',allval);export_csv(out/'test_results.csv',alltest);plot(out,plots)
        state['status']='completed';save()
    except Exception as exc:
        state.update(status='failed',error=repr(exc));save();raise

if __name__=='__main__':main()
