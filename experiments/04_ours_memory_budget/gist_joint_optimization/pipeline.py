"""Resume remaining stages only after the initial matrix is terminal/accepted."""
import json
from pathlib import Path
import subprocess
import sys
import time
from prepare import ROOT, HERE, WORK

OUT=ROOT/'results/04_ours_memory_budget/gist_joint_optimization'


def main():
    assert json.loads((OUT/'validation_completed.json').read_text())['passed']
    stages=[('allocation',['run.py','--allocation'])]
    if not (WORK/'scheduler_build.json').exists():stages.append(('build_scheduler',['prepare_scheduler.py']))
    stages += [('scheduler',['run.py','--scheduler']),('confirm',['run.py','--confirm']),('low_budget',['run.py','--low-budget'])]
    for name,args in stages:
        (OUT/'pipeline_state.json').write_text(json.dumps(dict(status='running',stage=name,pid=__import__('os').getpid(),updated=time.time()),indent=2))
        print(f'STAGE {name}',flush=True)
        result=subprocess.run([sys.executable,str(HERE/args[0]),*args[1:]],cwd=ROOT)
        if result.returncode:
            (OUT/'pipeline_state.json').write_text(json.dumps(dict(status='failed',stage=name,returncode=result.returncode,updated=time.time()),indent=2))
            raise SystemExit(result.returncode)
        subprocess.run([sys.executable,str(HERE/'report.py')],cwd=ROOT,check=True)
    cmd=['cargo','test','--release','--offline','--manifest-path',str(WORK/'native_scheduler/Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target'),'routing::tests','--','--test-threads=1']
    with (OUT/'routing_regression.log').open('w') as log:
        subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    subprocess.run([sys.executable,str(HERE/'finalize.py')],cwd=ROOT,check=True)
    (OUT/'pipeline_state.json').write_text(json.dumps(dict(status='completed_pending_final_audit',updated=time.time()),indent=2))


if __name__=='__main__':main()
