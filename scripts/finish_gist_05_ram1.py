"""Preserve the running 2/4/8 GiB queue, then append the requested 1 GiB run."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'results/diagnostics/gist_05_ours_only_20260921/status.json'
OUT = ROOT / 'results/diagnostics/gist_05_ours_1_2_4_8_20260921'


def main():
    OUT.mkdir(exist_ok=False)
    run_id = 'gist_ours_beam4_ram1_20260921'
    one = dict(budget_gib=1, method='Ours-Disk', run_id=run_id, status='queued')
    state = dict(methods=['Ours-Disk'], budgets_gib=[1,2,4,8], fixed_beam=4,
                 widths=[10,20,40,60,100,160,240,400,580],
                 execution_order_gib=[2,4,8,1], prior_queue=str(SOURCE),
                 excluded_diagnostic_budgets_gib=[0.5], figure_status='pending nature-figure rendering and QA')
    def save():
        source = json.loads(SOURCE.read_text())
        state['jobs'] = [one] + [r for r in source['jobs'] if r['budget_gib'] in [2,4,8]]
        tmp=OUT/'status.tmp';tmp.write_text(json.dumps(state,indent=2)+'\n');tmp.replace(OUT/'status.json')
        return source
    while True:
        source = save()
        if any(r['status']=='failed' for r in source['jobs']):
            state['status']='prior_queue_failed';save();raise RuntimeError('prior queue failed; inspect its evidence')
        jobs = [r for r in source['jobs'] if r['budget_gib'] in [2,4,8]]
        if len(jobs)==3 and all(r['status'] in ['completed','budget_exceeded'] for r in jobs):break
        time.sleep(30)
    paused=ROOT/'results/diagnostics/03_other_methods_admission_20260921/aisaq_suspended_for_performance.json'
    def signal_build(sig):
        for r in json.loads(paused.read_text()) if paused.exists() else []:
            try:
                if Path(f'/proc/{r["pid"]}/cmdline').read_bytes().decode().strip('\0').split('\0')==r['command']:
                    os.kill(r['pid'],sig)
            except OSError:pass
    # Wait for the prior scheduler's finally block to restore its diagnostic build.
    time.sleep(5)
    signal_build(signal.SIGSTOP)
    try:
        cpus=[i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
        if len(cpus)!=32:raise RuntimeError('need 32 node-0 CPUs')
        cmd=[sys.executable,str(ROOT/'experiments/05_memory_budget/run.py'),'--datasets','gist',
             '--methods','Ours-Disk','--run-id',run_id,'--workers','32','--repeats','1',
             '--cpu-affinity',','.join(map(str,cpus)),'--numa-node','0','--search-dram-budget-gib','1',
             '--fixed-beam','4','--phase','run','--method-pipeline']
        (OUT/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
        one.update(status='running',phase='run');save()
        env=dict(os.environ,PATH=str(ROOT/'work/tools/numactl/root/usr/bin')+os.pathsep+os.environ['PATH'])
        with (OUT/'run.log').open('x') as log:
            result=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        raw=ROOT/'results/05_memory_budget/gist'/run_id/'raw/Ours-Disk/test'
        failures=[str(p) for p in raw.glob('*.resources.json') if json.loads(p.read_text()).get('status')=='budget_exceeded']
        one.update(status=('completed' if result.returncode==0 else 'budget_exceeded' if failures else 'failed'),
                   exit_code=result.returncode,formal_ready=result.returncode==0,resource_evidence=failures)
        state['status']='measurements_finished_figures_pending' if one['status']!='failed' else 'failed';save()
    finally:
        signal_build(signal.SIGCONT)


if __name__=='__main__':main()
