"""GIST formal pilot: admitted reference paths first; stop on any failed phase."""
import json,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'results/diagnostics/gist_nine_width_preflight_20260921'
env=dict(os.environ);env['PATH']=str(root/'work/tools/numactl/root/usr/bin')+os.pathsep+env['PATH']
cpus=[i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
if len(cpus)!=32:raise RuntimeError('need 32 allowed node-0 CPUs')
base=[sys.executable,str(root/'experiments/03_disk_system/run.py'),'--datasets','gist','--methods','Ours-Disk,DiskANN-PQ-Disk','--run-id','gist_w9_4g_20260921','--workers','32','--repeats','1','--cpu-affinity',','.join(map(str,cpus)),'--numa-node','0','--search-dram-budget-gib','4']
for phase in ['export','validate','tune','run','plot']:
 if (out/'hold.json').exists():
  raise SystemExit('Paused: method admission review must finish before resuming experiments.')
 cmd=base+['--phase',phase]
 (out/f'{phase}_command.json').write_text(json.dumps(cmd,indent=2)+'\n')
 print('START',phase,flush=True)
 with (out/f'{phase}.log').open('x') as log:
  result=subprocess.run(cmd,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
 (out/'status.json').write_text(json.dumps({'phase':phase,'exit_code':result.returncode,'widths':[10,20,40,60,100,160,240,400,580],'methods':['Ours-Disk','DiskANN-PQ-Disk']},indent=2)+'\n')
 if result.returncode:raise SystemExit(result.returncode)
 print('DONE',phase,flush=True)
