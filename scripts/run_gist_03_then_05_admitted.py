"""User-authorized sequential GIST runs, excluding OG-LVQ and pending ports."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/diagnostics/gist_03_then_05_beam4_20260921'
METHODS = ['Ours-Disk', 'DiskANN-PQ-Disk', 'Glass-NSG-DiskPort', 'SymphonyQG-DiskPort']
WIDTHS = [10,20,40,60,100,160,240,400,580]

def main():
    OUT.mkdir(exist_ok=True)
    env = dict(os.environ, PATH=str(ROOT/'work/tools/numactl/root/usr/bin')+os.pathsep+os.environ['PATH'])
    cpus = [i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
    if len(cpus)!=32: raise RuntimeError('need 32 node-0 CPUs')
    jobs = [('03_disk_system',4,'gist_w9_beam4_4g_20260921')]
    jobs += [('05_memory_budget',b,f'gist_w9_beam4_ram{str(b).replace(".","p")}_20260921') for b in [.5,2,4,8]]
    state = dict(methods=METHODS,widths=WIDTHS,jobs=[],index_policy='reuse existing admitted disk indexes; no export; validation rechecks compatibility',figure_status='pending nature-figure rendering and QA')
    def save(): (OUT/'status.json').write_text(json.dumps(state,indent=2)+'\n')
    save()
    try:
        for experiment,budget,run_id in jobs:
            folder=OUT/run_id;folder.mkdir(exist_ok=True)
            row=dict(experiment=experiment,budget_gib=budget,run_id=run_id,status='running');state['jobs'].append(row);save()
            base=[sys.executable,str(ROOT/'experiments'/experiment/'run.py'),'--datasets','gist',
                  '--methods',','.join(METHODS),'--run-id',run_id,'--workers','32','--repeats','1',
                  '--cpu-affinity',','.join(map(str,cpus)),'--numa-node','0','--search-dram-budget-gib',str(budget),'--fixed-beam','4']
            for phase in ['run']:
                row['phase']=phase;row['schedule']='validation-only hot profile; fixed parameter lock; direct test with reference/resource admission';save();cmd=base+['--phase',phase,'--method-pipeline']
                (folder/f'{phase}_command.json').write_text(json.dumps(cmd,indent=2)+'\n')
                print('START',experiment,budget,phase,flush=True)
                with (folder/f'{phase}.log').open('a') as log:
                    result=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                row['exit_code']=result.returncode;save()
                if result.returncode:
                    row['status']='failed';save()
                    raise RuntimeError(f'{experiment} {budget} {phase} failed; see {folder}')
            row['status']='completed';save()
        state['status']='measurements_completed_figures_pending';save()
    finally:
        p=ROOT/'results/diagnostics/03_other_methods_admission_20260921/aisaq_suspended_for_performance.json'
        if p.exists():
            for r in json.loads(p.read_text()):
                try:
                    cmd=Path(f'/proc/{r["pid"]}/cmdline').read_bytes().decode().strip('\0').split('\0')
                    if cmd==r['command']:os.kill(r['pid'],signal.SIGCONT)
                except (OSError,UnicodeError):pass

if __name__=='__main__': main()
