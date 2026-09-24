"""Replay admitted native commands serially; reuse correctness, preserve native artifacts."""
import csv
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_ours_cache_allocation import audit_result, sha256, write
from diskfair.admission import exact_traces, audit_trace, replace
from diskfair.memory_runner import run_measured_isolated
from diskfair.storage_precondition import prepare_search

OLD=ROOT/'results/diagnostics/gist_cache_three_widths_20260923'
OUT=ROOT/'results/diagnostics/gist_cache_serial_remeasure_20260923'

def active_io(exclude_run=None):
    found=[]
    for p in Path('/proc').glob('[0-9]*'):
        try:
            state=(p/'stat').read_text().split(') ',1)[1].split()[0]
            if state in ('T','t','Z'): continue
            comm=(p/'comm').read_text().strip()
            if comm != 'fio' and not any(x in comm.lower() for x in ('qgraph','diskann','starling','symphony')):continue
            cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if exclude_run and exclude_run in cmd:continue
            found.append(dict(pid=int(p.name),comm=comm,command=cmd))
        except (OSError,IndexError):continue
    return found

def main():
    OUT.mkdir(exist_ok=False)
    state=dict(status='waiting_for_current_queue',jobs=[],widths=[60,100,180],budget_gib=0.5,
               reference_policy='reuse_admitted_same_strategy_results_no_new_reference_search',
               concurrency_scope='observed known ANN and fio process names; not a machine exclusivity guarantee')
    def save():
        state['updated_unix']=time.time();write(OUT/'status.json',state)
    save()
    try:
        while json.loads((OLD/'status.json').read_text())['status']=='running':time.sleep(5)
        sources=json.loads((OLD/'status.json').read_text())['jobs']
        rows=[]
        for strategy in ('graph_first','records_only'):
            while active_io():
                state.update(status='waiting_for_io_idle',active_io=active_io());save();time.sleep(5)
            source=Path(next(j['artifact'] for j in sources if j['status']=='admitted' and j['budget_gib']==0.5 and j['strategy']==strategy))
            a=audit_result(source);source_hash=sha256(source)
            resources=json.loads(source.with_suffix('.resources.json').read_text())
            cmd=list(resources['command']);run_id=f'gist_cache_serial_remeasure_20260923_{strategy}'
            dest=OUT/strategy;dest.mkdir()
            for key,value in (('--run-id',run_id),('--result-json',dest/'result.json'),('--query-trace',dest/'queries.jsonl')):replace(cmd,key,value)
            job=dict(strategy=strategy,run_id=run_id,status='preparing',source=str(source),source_sha256=source_hash)
            state['jobs'].append(job);state['status']='running';save()
            if sha256(Path(cmd[0]))!=a['native_binary_sha256']:raise ValueError('binary changed')
            write(dest/'command.json',cmd)
            prepare_search(cmd,dest/'storage.json')
            if active_io():raise ValueError('competing I/O process appeared before measurement')
            stop=threading.Event();overlap=[]
            def watch():
                while not stop.is_set():
                    overlap.extend(active_io(run_id));stop.wait(1)
            watcher=threading.Thread(target=watch,daemon=True);watcher.start()
            env=dict(os.environ,QG05_FAST='0',QG05_REFERENCE_MMAP='0')
            for key in ('QG05_SKIP_EXTERNAL_PARITY','QG05_FAST_WIDTH','QG05_FAST_WIDTHS'):env.pop(key,None)
            job['status']='measuring';save()
            try:
                e=run_measured_isolated(cmd,evidence_path=dest/'resources.json',log_path=dest/'terminal.log',
                    budget_bytes=536870912,rss_budget=True,env=env,cwd=ROOT,
                    cpu_affinity=resources['launch_policy']['cpu_affinity'],numa_node=0)
            finally:stop.set();watcher.join()
            write(dest/'concurrency.json',dict(overlap=overlap,scope=state['concurrency_scope']))
            if not e['budget_admitted']:raise ValueError(f'resource rejection: {e["status"]}')
            if overlap:raise ValueError('concurrent known I/O task observed; performance excluded')
            if sha256(source)!=source_hash:raise ValueError('reference artifact changed')
            audit_result(source)
            result=json.loads((dest/'result.json').read_text())
            count=exact_traces(a['query_trace_path'],dest/'queries.jsonl')
            audit_trace(result,cmd)
            for key in ('ours_route_plan','pca_assets_sha256','ours_hot_ranks_sha256','source_graph_sha256','ours_cache_allocation'):
                if result[key]!=a[key]:raise ValueError('replay identity changed: '+key)
            points=result['summary_rows']
            if sorted(r['search_width'] for r in points)!=[60,100,180] or any(r['query_count']!=800 for r in points):raise ValueError('incomplete points')
            proof=dict(status='passed',kind='same_strategy_measurement_replay_v1',source=str(source),source_sha256=source_hash,
                result_sha256=sha256(dest/'result.json'),trace_sha256=sha256(dest/'queries.jsonl'),
                resources_sha256=sha256(dest/'resources.json'),comparisons=count,expansion_paths_checked=False,
                peak_rss_bytes=e['observed_peak_rss_bytes'],budget_admitted=True,concurrency_sha256=sha256(dest/'concurrency.json'))
            write(dest/'replay_evidence.json',proof)
            job.update(status='passed',evidence=str(dest/'replay_evidence.json'));save()
            for r in points:rows.append(dict(strategy=strategy,width=r['search_width'],recall=r['recall'],qps=r['qps'],peak_rss_bytes=e['observed_peak_rss_bytes']))
        with (OUT/'measured_results.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        plot(rows)
        state['status']='completed';save()
    except Exception as exc:
        state.update(status='failed',error=repr(exc));save();raise

def plot(rows):
    os.environ.setdefault('MPLCONFIGDIR','/tmp/qgraph-cache-figure-mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'svg.fonttype':'none','pdf.fonttype':42,'font.size':10})
    fig,axes=plt.subplots(1,2,figsize=(8,3.2),layout='constrained')
    for strategy,color,marker in [('graph_first','#0072B2','o'),('records_only','#D55E00','s')]:
        pts=sorted((r for r in rows if r['strategy']==strategy),key=lambda r:r['width'])
        for ax,key,label in zip(axes,['recall','qps'],['Recall@10','QPS']):
            ax.plot([r['width'] for r in pts],[r[key] for r in pts],marker=marker,color=color,label=strategy)
            ax.set(xlabel='Search width',ylabel=label,xticks=[60,100,180]);ax.grid(axis='y',alpha=.2)
    axes[0].set_ylim(0.89,1);axes[1].set_ylim(bottom=0);axes[1].legend(frameon=False)
    fig.suptitle('GIST · 0.5 GiB · resident PCA128 · test800')
    for ext in ('svg','pdf','png'):fig.savefig(OUT/f'recall_qps_by_width.{ext}',dpi=300)
    plt.close(fig)
    write(OUT/'figure_provenance.json',dict(data_sha256=sha256(OUT/'measured_results.csv'),
        script_sha256=sha256(Path(__file__)),caption='Serial measurement replay. Each point: 800 test queries, 32 workers, beam 4. Correctness evidence reused; no path tracing. One run per point; no uncertainty estimate. Known ANN/fio concurrency monitored, not proof of exclusive host access.',visual_review='pending',publication_ready=False))

if __name__=='__main__':main()
