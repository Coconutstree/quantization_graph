"""Fixed true 4 GiB, all-resident dimension ablation using a frozen cache choice."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from run_cache_selection import ROOT,run_job,export_csv,plot
from run_ours_cache_allocation import audit_result,sha256,write
from remeasure_cache_serial import active_io
from cache_selection_policy import cache_audit
from diskfair.admission import replace,exact_traces,audit_trace
from diskfair.storage_precondition import option,prepare_search
from diskfair.memory_runner import run_measured_isolated
from diskfair.ours_pca import ensure_assets,POLICY
from diskfair.ours_records import prepare_record_args

def remove(cmd,flag):
    if flag in cmd:
        i=cmd.index(flag);del cmd[i:i+2]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--selection-queue',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if json.loads((args.selection_queue/'status.json').read_text())['status']!='completed':raise ValueError('cache selection audit not complete')
    selection=args.selection_queue/'ram4/cache_selection.lock.json';chosen=json.loads(selection.read_text());strategy=chosen['decision']['selected'];lockhash=sha256(selection)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    state=dict(status='running',budget_gib=4,widths=[60,100,180],dimensions=[960,512,256,128],strategy=strategy,cache_selection_lock=str(selection),cache_selection_lock_sha256=lockhash,jobs=[])
    def save():state['updated_unix']=time.time();write(out/'status.json',state)
    save();binary=args.binary.resolve();bh=sha256(binary)
    sources=json.loads((ROOT/'results/diagnostics/gist_cache_three_widths_20260923/status.json').read_text())['jobs']
    source=Path(next(j['artifact'] for j in sources if j.get('budget_gib')==4 and j['strategy']==strategy and j['status']=='admitted'))
    original=audit_result(source);resources=json.loads(source.with_suffix('.resources.json').read_text());basecmd=resources['command'];policy=resources['launch_policy']
    profile=Path(original['ours_hot_profile_manifest']).parent;valsource=json.loads((profile/'resources.json').read_text())['command']
    env=dict(os.environ,QG05_FAST='0',QG05_REFERENCE_MMAP='0',OMP_NUM_THREADS='32',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    for key in ('QG05_SKIP_EXTERNAL_PARITY','QG05_FAST_WIDTH','QG05_FAST_WIDTHS'):env.pop(key,None)
    index=Path(option(basecmd,'--disk-index-dir'))
    manifest=json.loads(Path(option(basecmd,'--input-manifest')).read_text())
    # Derive the original base path from the manifest, never substitute a reduced dataset.
    def find_base(v):
        if isinstance(v,dict):
            for k,x in v.items():
                if k in ('base','base_path','base_file'):
                    if isinstance(x,str) and Path(x).is_file():return Path(x)
                    if isinstance(x,dict):
                        for kk in ('path','source_path'):
                            if kk in x and Path(x[kk]).is_file():return Path(x[kk])
            for x in v.values():
                f=find_base(x)
                if f:return f
        elif isinstance(v,list):
            for x in v:
                f=find_base(x)
                if f:return f
    base=find_base(manifest)
    if not base:raise ValueError('base path not found in frozen input manifest')
    if sha256(base)!=manifest['files']['base']['sha256']:raise ValueError('original base hash changed')
    allrows=[];plots=[]
    try:
        for dim in state['dimensions']:
            while active_io():time.sleep(3)
            d=out/f'd{dim}';d.mkdir();job=dict(dimension=dim,status='preparing');state['jobs'].append(job);save()
            plan=json.loads(subprocess.check_output([str(binary),'--ours-route-plan',str(index),'--ours-route-policy',POLICY,'--workers','32','--search-dram-budget-gib','4','--ours-query-reservation-bytes',option(basecmd,'--ours-query-reservation-bytes'),'--ours-fixed-route-dimension',str(dim)],text=True))
            asset=ensure_assets(base,binary,index/'pca_routes',plan)
            cmd=list(basecmd);cmd[0]=str(binary);replace(cmd,'--native-binary-sha256',bh);replace(cmd,'--ours-fixed-route-dimension',dim)
            for flag in ('--ours-record-cache-policy','--ours-hot-profile-manifest','--ours-hot-profile-sha256','--ours-hot-ranks','--ours-hot-ranks-sha256','--pca-route-dir','--pca-assets-sha256'):remove(cmd,flag)
            if asset:cmd+=['--pca-route-dir',str(asset),'--pca-assets-sha256',sha256(asset/'assets.json')]
            val=list(cmd)
            for key in ('--phase','--query','--groundtruth','--query-split-sha256','--query-order','--query-order-sha256','--candidate-row-offset'):replace(val,key,option(valsource,key))
            artifact=d/'validation.json';write(artifact.with_suffix('.route_plan.json'),plan)
            record_args=prepare_record_args(val,artifact,phase='validation',tuning_lock=None,cpu_affinity=policy['cpu_affinity'],numa_node=0)
            cmd+=record_args
            tuning=json.loads(Path(option(basecmd,'--tuning-lock')).read_text());selected=tuning['selected']['Ours-Disk::hybrid_disk'];selected.update(ours_route_plan=plan,ours_fixed_route_dimension=dim,ours_hot_profile_manifest=option(cmd,'--ours-hot-profile-manifest'),ours_hot_profile_sha256=option(cmd,'--ours-hot-profile-sha256'))
            write(d/'parameters.lock.json',tuning);replace(cmd,'--tuning-lock',d/'parameters.lock.json')
            frozen=dict(dimension=dim,budget=4,route_plan=plan,cache_strategy=strategy,binary_sha256=bh,cache_selection_lock_sha256=lockhash,command=cmd,files={str(d/'parameters.lock.json'):sha256(d/'parameters.lock.json'),option(cmd,'--ours-hot-profile-manifest'):option(cmd,'--ours-hot-profile-sha256'),option(cmd,'--ours-hot-ranks'):option(cmd,'--ours-hot-ranks-sha256')})
            if asset:frozen['files'][str(asset/'assets.json')]=sha256(asset/'assets.json')
            write(d/'experiment.lock.json',frozen)
            job['status']='reference';save();refdir=d/'reference';refdir.mkdir();ref=list(cmd)
            for k,v in [('--run-id',f'gist_06_d{dim}_reference'),('--result-json',refdir/'result.json'),('--query-trace',refdir/'queries.jsonl'),('--parity-mode','internal')]:replace(ref,k,v)
            while active_io():time.sleep(3)
            prepare_search(ref,refdir/'storage.json')
            e=run_measured_isolated(ref,evidence_path=refdir/'resources.json',log_path=refdir/'terminal.log',reference=True,env=env,cpu_affinity=policy['cpu_affinity'],numa_node=0)
            if e['status']!='completed':raise ValueError('06 independent reference failed')
            parity=json.loads((refdir/'result.parity.json').read_text())
            if parity['query_comparisons']!=2400 or parity['mean_top10_overlap']!=1 or parity['max_recall_delta']!=0 or parity['mean_distance_count_relative_delta']!=0 or parity['mean_visited_count_relative_delta']!=0:raise ValueError('06 parity mismatch')
            job['status']='measuring';save();result,e,error=run_job(list(cmd),d/'test',env,policy,4*2**30)
            if error:raise ValueError('06 measurement rejected: '+error)
            if sha256(selection)!=lockhash or sha256(binary)!=bh:raise ValueError('frozen configuration changed')
            for f,h in frozen['files'].items():
                if sha256(Path(f))!=h:raise ValueError('06 frozen asset changed')
            if result['ours_route_plan']!=plan or result['ours_cache_allocation']!=strategy:raise ValueError('06 representation/cache differs')
            cache_audit(result);count=exact_traces(refdir/'queries.jsonl',d/'test/queries.jsonl')
            write(d/'acceptance.json',dict(status='passed',comparisons=count,result_sha256=sha256(d/'test/result.json'),resources_sha256=sha256(d/'test/resources.json'),lock_sha256=sha256(d/'experiment.lock.json'),reference_sha256=sha256(refdir/'result.json'),peak_rss_bytes=e['observed_peak_rss_bytes']))
            job['status']='passed';save()
            for r in result['summary_rows']:allrows.append(dict(dimension=dim,strategy=strategy,measured_peak_rss_bytes=e['observed_peak_rss_bytes'],**r))
            export_csv(out/'test_results.csv',allrows)
        draw_dimensions(out,allrows,strategy)
        state['status']='completed';save()
    except Exception as exc:state.update(status='failed',error=repr(exc));save();raise

def draw_dimensions(out,rows,strategy):
    os.environ.setdefault('MPLCONFIGDIR','/tmp/qgraph-cache-figure-mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'svg.fonttype':'none','pdf.fonttype':42})
    fig,ax=plt.subplots(figsize=(6,4),layout='constrained')
    for dim,marker in zip((960,512,256,128),('o','s','^','D')):
        pts=sorted([r for r in rows if r['dimension']==dim],key=lambda r:r['search_width'])
        ax.plot([r['recall'] for r in pts],[r['qps'] for r in pts],marker=marker,label=f'{dim}D resident')
        for r in pts:ax.annotate(str(r['search_width']),(r['recall'],r['qps']),xytext=(3,5),textcoords='offset points',fontsize=8)
    ax.set(xlabel='Recall@10',ylabel='QPS',title=f'GIST · 4 GiB · {strategy}',ylim=(0,None));ax.legend(frameon=False);ax.grid(alpha=.2)
    for ext in ('svg','pdf','png'):fig.savefig(out/f'recall_qps.{ext}',dpi=300)
    plt.close(fig)
    write(out/'figure_provenance.json',dict(data_sha256=sha256(out/'test_results.csv'),cache_strategy=strategy,widths=[60,100,180],all_representations_resident=True,repeat_count=1,visual_review='pending',publication_ready=False))

if __name__=='__main__':main()
