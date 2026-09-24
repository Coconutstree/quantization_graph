"""Sequential admitted experiments; preserve previous tables/figures and source runs."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from summarize_gist_formal_tables import load, render
from plot_gist_formal_recall_qps import draw
ROOT=Path(__file__).resolve().parents[1]
TAG='aligned_20260921'
OUT=ROOT/'results/diagnostics'/TAG
METHODS=['Ours-Disk','DiskANN-PQ-Disk','Glass-NSG-DiskPort','SymphonyQG-DiskPort']
SKILL=Path('/home/kai3/.agents/skills/nature-figure/scripts')

def verify_registry():
    """Fail before any run starts if a ready port binary is missing or has changed."""
    ports=json.loads((ROOT/'src/disk_bench/ports.local.json').read_text())['ports']
    problems=[]
    for key,port in ports.items():
        if port.get('status')!='ready': continue
        binary=ROOT/port['command'][0]
        if not binary.is_file():
            problems.append(f'{key}: missing binary {port["command"][0]}');continue
        with binary.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=port.get('binary_sha256'):
            problems.append(f'{key}: binary sha256 differs from the registry '
                            f'(registry={str(port.get("binary_sha256"))[:12]} disk={digest[:12]})')
    if problems:
        raise SystemExit('port registry does not match the built binaries:\n  - '+'\n  - '.join(problems))
    print(f'port registry verified: {sum(1 for p in ports.values() if p.get("status")=="ready")} ready binaries',flush=True)

def repair_glass(dataset,experiment,run_id,folder,env):
    raw=ROOT/'results'/experiment/dataset/run_id/'raw/Glass-NSG-DiskPort/test'
    refs=list(raw.glob('*.reference'))
    for ref in refs:
        log=ref/'terminal.log'
        if not log.exists() or 'ERROR: Glass index provenance/geometry mismatch' not in log.read_text(): continue
        cmd=json.loads((ref/'resources.json').read_text())['command']
        def get(flag): return cmd[cmd.index(flag)+1]
        def put(flag,value): cmd[cmd.index(flag)+1]=str(value)
        index=Path(get('--disk-index-dir'))
        backup=folder/'glass_repair';backup.mkdir(exist_ok=True)
        old=index.with_name(index.name+'_before_aligned_repair')
        if old.exists(): raise RuntimeError(f'prior repair exists; inspect before retry: {old}')
        # Preserve original pages and provenance. Never relabel an old index as compatible.
        index.rename(old)
        put('--phase','export');put('--result-json',backup/'export.json');put('--query-trace',backup/'export.queries.jsonl')
        (backup/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
        print('REBUILD incompatible Glass index',dataset,flush=True)
        with (backup/'export.log').open('a') as f:
            subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        shutil.move(str(ref),str(backup/'failed_reference'))
        return True
    return False

def main():
    global TAG,OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag',default=TAG,help='run-id tag, e.g. aligned_20260922')
    parser.add_argument('--datasets',default='gist,agnews,dbpedia')
    parser.add_argument('--experiments',default='03_disk_system,05_memory_budget')
    args=parser.parse_args()
    TAG=args.tag
    OUT=ROOT/'results/diagnostics'/TAG
    datasets=[d for d in args.datasets.split(',') if d]
    experiments=[e for e in args.experiments.split(',') if e]
    verify_registry()
    print(f'queue tag={TAG} datasets={datasets} experiments={experiments}',flush=True)
    OUT.mkdir(parents=True,exist_ok=True)
    state={'status':'running','tag':TAG,'datasets':datasets,'order':'dataset: 03 then 05',
           'experiments':experiments,
           'widths':[10,20,40,60,100,160,240,400,580], 'beam':4,'budgets_05':[1,2,4,8],
           'methods_03':METHODS,'methods_05':['Ours-Disk'],'jobs':[],
           'protocol':'no validation performance sweep; hot profile only; reference and RSS admission; width-only DiskANN without traversal cap'}
    if (OUT/"status.json").exists():
        state=json.loads((OUT/"status.json").read_text())
        state["status"]="running"
        state.pop("error",None)
    def save():
        tmp=OUT/'status.tmp';tmp.write_text(json.dumps(state,indent=2)+'\n');tmp.replace(OUT/'status.json')
    save()
    env=dict(os.environ,PATH=str(ROOT/'work/tools/numactl/root/usr/bin')+os.pathsep+os.environ['PATH'])
    cpus=[i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
    if len(cpus)!=32:raise RuntimeError('need 32 node-0 CPUs')
    record=ROOT/'results/diagnostics/03_other_methods_admission_20260921/aisaq_suspended_for_performance.json'
    paused=[]
    for r in json.loads(record.read_text()) if record.exists() else []:
        try:
            if Path(f'/proc/{r["pid"]}/cmdline').read_bytes().decode().strip('\0').split('\0')==r['command']:
                os.kill(r['pid'],signal.SIGSTOP);paused.append(r)
        except OSError:pass
    def command(cmd,log):
        with log.open('a') as f:subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    try:
        for dataset in state['datasets']:
            for experiment in experiments:
                sources=[]
                for budget in ([4] if experiment.startswith('03') else [1,2,4,8]):
                    run_id=f'{dataset}_{TAG}_{experiment[:2]}_ram{budget}'
                    previous=next((j for j in state['jobs'] if j['run_id']==run_id),None)
                    if previous and previous['status']=='completed':
                        sources.append(load(f'results/{experiment}/{dataset}/{run_id}'))
                        job=previous
                        continue
                    job={'dataset':dataset,'experiment':experiment,'budget_gib':budget,'run_id':run_id,'status':'running'}
                    if previous: state['jobs'].remove(previous)
                    state['jobs'].append(job);save()
                    folder=OUT/run_id;folder.mkdir(exist_ok=True)
                    cmd=[sys.executable,str(ROOT/'experiments'/experiment/'run.py'),'--datasets',dataset,
                         '--methods',','.join(METHODS if experiment.startswith('03') else ['Ours-Disk']),
                         '--run-id',run_id,'--workers','32','--repeats','1','--cpu-affinity',','.join(map(str,cpus)),
                         '--numa-node','0','--search-dram-budget-gib',str(budget),'--fixed-beam','4',
                         '--phase','run','--method-pipeline']
                    (folder/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
                    repair_glass(dataset,experiment,run_id,folder,env)
                    print('START',run_id,flush=True)
                    try:
                        command(cmd,folder/'run.log')
                    except subprocess.CalledProcessError:
                        if not repair_glass(dataset,experiment,run_id,folder,env): raise
                        command(cmd,folder/'run.log')
                    source=load(f'results/{experiment}/{dataset}/{run_id}')
                    assert len(source[1])==(36 if experiment.startswith('03') else 9)
                    sources.append(source);job.update(status='completed',formal_ready=True);save()
                if all(j.get('status')=='completed' for j in state['jobs'] if j['dataset']==dataset and j['experiment']==experiment) and job.get('figure_status','').startswith('exported'):
                    continue
                job['figure_status']='rendering';save()
                dest=ROOT/'results'/experiment/dataset
                for name in ['tables','figures']:
                    old=dest/name
                    backup=OUT/'previous_outputs'/experiment/dataset/name
                    if old.exists() and not backup.exists():shutil.copytree(old,backup)
                render(experiment,sources,'method' if experiment.startswith('03') else 'search_dram_budget_gib',dataset)
                figures=dest/'figures';figures.mkdir(exist_ok=True)
                (figures/'figure_contract.md').write_text(f'# Figure contract\n\n{dataset.upper()} Recall@10 versus QPS, all 36 admitted points, nine widths per curve. Quantitative comparison panel; Python/matplotlib; 89 × 78 mm; minimum font 7 pt; editable PDF/SVG, 600 dpi TIFF and 300 dpi PNG. 800 queries per point, one measurement per configuration, no inferred uncertainty. No smoothing or Pareto filtering. Routing modes recorded in source CSV. Log QPS for system comparison; linear QPS for budget comparison.\n')
                draw(experiment,dataset)
                stem='system_qps_recall' if experiment.startswith('03') else 'memory_qps_recall'
                command([sys.executable,str(SKILL/'validate_figure.py'),str(ROOT/'scripts/plot_gist_formal_recall_qps.py')],figures/'source_preflight.txt')
                command([sys.executable,str(SKILL/'audit_pdf_text.py'),str(figures/f'{stem}.pdf'),'--min-pt','5'],figures/'pdf_text_audit.txt')
                (figures/'QA.md').write_text('Automated checks: all 36 observations retained; editable vector and high-resolution raster exports; PDF font audit completed. Static width warning is the 89/25.4-inch expression; positive QPS assertion guards logarithmic scale. Human visual inspection of the new dataset render remains pending.\n')
                # Each new run retains an immutable copy of its final experiment-level figure bundle.
                shutil.copytree(figures,dest/sources[-1][0].parent.parent.name/'figures',dirs_exist_ok=True)
                job['figure_status']='exported; automated QA passed; visual inspection pending';save()
        state['status']='completed_visual_review_pending';save()
    except Exception as exc:
        state['status']='failed';state['error']=repr(exc)
        if state['jobs']:state['jobs'][-1]['status']='failed'
        save();raise
    finally:
        for r in paused:
            try:
                if Path(f'/proc/{r["pid"]}/cmdline').read_bytes().decode().strip('\0').split('\0')==r['command']:os.kill(r['pid'],signal.SIGCONT)
            except OSError:pass

if __name__=='__main__':main()
