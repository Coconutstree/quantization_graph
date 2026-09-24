"""Ours-only four-budget experiment; preserve the already attempted 0.5 GiB point."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/diagnostics/gist_05_ours_only_20260921'


def main():
    OUT.mkdir(exist_ok=False)
    prior = ROOT / 'results/05_memory_budget/gist/gist_w9_beam4_ram0p5_20260921/raw/Ours-Disk/test/Ours-Disk__hybrid_disk__B0.5__standard__w32__r0.resources.json'
    evidence = json.loads(prior.read_text())
    if evidence['status'] != 'budget_exceeded':
        raise RuntimeError('unexpected prior 0.5 GiB outcome')
    state = dict(methods=['Ours-Disk'], budgets_gib=[.5,2,4,8], fixed_beam=4,
                 widths=[10,20,40,60,100,160,240,400,580], jobs=[dict(
                     budget_gib=.5, method='Ours-Disk', status='budget_exceeded', formal_ready=False,
                     resource_evidence=str(prior),
                     note='Preserved Ours attempt from the previous multi-method plan; other methods did not run.')],
                 figure_status='pending nature-figure rendering and QA')
    def save(): (OUT/'status.json').write_text(json.dumps(state,indent=2)+'\n')
    save()
    env = dict(os.environ, PATH=str(ROOT/'work/tools/numactl/root/usr/bin')+os.pathsep+os.environ['PATH'])
    cpus = [i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
    if len(cpus)!=32: raise RuntimeError('need 32 node-0 CPUs')
    paused = ROOT/'results/diagnostics/03_other_methods_admission_20260921/aisaq_suspended_for_performance.json'
    def signal_build(sig):
        for r in json.loads(paused.read_text()) if paused.exists() else []:
            try:
                c=Path(f'/proc/{r["pid"]}/cmdline').read_bytes().decode().strip('\0').split('\0')
                if c==r['command']:os.kill(r['pid'],sig)
            except OSError:pass
    signal_build(signal.SIGSTOP)
    try:
        for budget in [2,4,8]:
            run_id=f'gist_ours_beam4_ram{budget}_20260921'
            folder=OUT/run_id;folder.mkdir()
            row=dict(budget_gib=budget,method='Ours-Disk',run_id=run_id,status='running',phase='run')
            state['jobs'].append(row);save()
            cmd=[sys.executable,str(ROOT/'experiments/05_memory_budget/run.py'),'--datasets','gist',
                 '--methods','Ours-Disk','--run-id',run_id,'--workers','32','--repeats','1',
                 '--cpu-affinity',','.join(map(str,cpus)),'--numa-node','0','--search-dram-budget-gib',str(budget),
                 '--fixed-beam','4','--phase','run','--method-pipeline']
            (folder/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
            print('START Ours',budget,'GiB',flush=True)
            with (folder/'run.log').open('x') as log:
                result=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            row['exit_code']=result.returncode
            if result.returncode:
                raw=ROOT/'results/05_memory_budget/gist'/run_id/'raw/Ours-Disk/test'
                failures=[p for p in raw.glob('*.resources.json') if json.loads(p.read_text()).get('status')=='budget_exceeded']
                row.update(status='budget_exceeded' if failures else 'failed',formal_ready=False,
                           resource_evidence=[str(p) for p in failures]);save()
                if not failures:raise RuntimeError(f'non-budget failure: {folder}')
            else:
                row.update(status='completed',formal_ready=True);save()
        state['status']='budget_scan_finished_figures_pending';save()
    finally:
        signal_build(signal.SIGCONT)


if __name__=='__main__': main()
