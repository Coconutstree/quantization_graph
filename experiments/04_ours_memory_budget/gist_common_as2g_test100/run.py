"""One 40-width test100 run per configuration, common 2 GiB RLIMIT_AS."""
import argparse
import resource
import subprocess
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'results/04_ours_memory_budget/gist_common_as2g_test100_inflight256'
PREVIOUS=ROOT/'results/04_ours_memory_budget/gist_width40_test100'
CAP=2*1024**3
spec=importlib.util.spec_from_file_location('previous_driver',HERE.parent/'gist_width40_test100/run.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
spec=importlib.util.spec_from_file_location('current_auto',HERE.parent/'gist_auto_inflight256.py')
adopt=importlib.util.module_from_spec(spec);spec.loader.exec_module(adopt)
s=driver.s=adopt.s
original_measure=driver.measurement.measure


def measure_common(command,folder,*,budget=None,**kwargs):
    # The native argument continues to select Ours cache capacity. The OS limit
    # is always CAP, including DiskANN, irrespective of the native argument.
    mem=original_measure(command,folder,budget=CAP,**kwargs)
    assert mem['rlimit_as_bytes']==CAP and mem['hard_limit_verified']
    assert mem['user_address_space_budget_passed']
    flags=s.flags(command)
    mem['configured_planning_budget_bytes']=round(float(flags['--search-dram-budget-gib'])*1024**3)
    mem['comparison_scope']='common address-space ceiling, not equal RSS budget'
    s.dump(Path(folder)/'memory_measurement.json',mem)
    return mem


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--preflight','--prepare-only',action='store_true')
    args=parser.parse_args()
    adopt.setup()
    OUT.mkdir(parents=True,exist_ok=True)
    lock=(OUT/'run.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    previous=json.loads((PREVIOUS/'experiment.json').read_text())
    assert s.sha(s.BIN)==json.loads((adopt.WORK/'build.json').read_text())['binary_sha256']
    disk=ROOT/'baselines/diskann/target/release/qgraph05_diskann_port'
    assert s.sha(disk)==previous['diskann_binary_sha256']
    base=s.base('pilot')
    s.base=lambda split:dict(base)
    driver.OUT=s.OUT=OUT
    driver.measurement.measure=measure_common
    sys.path.insert(0,str(HERE))
    import report
    report.OUT=OUT
    cases=[('diskann_c0','disk',2048,'c0'),('ours_resident','ours',2048,'resident')]
    cases += [(f'ours_{b}','ours',b,'auto') for b in [1536,1024,768,616,538]]
    definition=dict(queries=100,widths=s.WIDTHS,repeats=1,workers=32,beam=1,warmup_per_width=100,
        rlimit_as_soft_bytes=CAP,rlimit_as_hard_bytes=CAP,physical_rss_cap=False,
        ours_planning_mib=[538,616,768,1024,1536],resident_reference=True,
        diskann_cache='c0',native_binary_hashes={str(s.BIN):s.sha(s.BIN),str(disk):s.sha(disk)},
        inputs={k:dict(path=base[k],sha256=s.sha(base[k])) for k in ['--query','--groundtruth','--query-order']},
        storage='sequential O_DIRECT pre-read of search files before every process; uncontrolled device cache',
        case_names=[name+'_r1' for name,_,_,_ in cases],full800_enabled=False)
    if (OUT/'experiment.json').exists():assert json.loads((OUT/'experiment.json').read_text())==definition
    s.dump(OUT/'experiment.json',definition)
    s.dump(OUT/'implementation.json',{str(p):s.sha(p) for p in [*HERE.glob('*.py'),HERE.parent/'gist_width40_test100/run.py',HERE.parent/'gist_width40_test100/measurement.py',HERE.parent/'gist_width40_test100/plot.py']})
    if not args.run:
        checks=[]
        for name,budget,mode,expected in [('paged',538,'auto',256),('resident',2048,'resident',0)]:
            folder=OUT/'preflight'/name;folder.mkdir(parents=True,exist_ok=True)
            command=s.command(folder,'pilot',budget*2**20,mode,0,[100],preflight=True)
            def limits(): resource.setrlimit(resource.RLIMIT_AS,(CAP,CAP))
            proc=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                preexec_fn=limits,env=dict(os.environ,MALLOC_ARENA_MAX='2'),timeout=60)
            (folder/'terminal.log').write_text(proc.stdout);s.dump(folder/'command.json',command)
            assert proc.returncode==0,proc.stdout
            plan=json.loads((folder/'memory_plan.json').read_text());assert plan['max_inflight_pages']==expected
            checks.append(dict(case=name,passed=True,max_inflight_pages=expected,rlimit_as_bytes=CAP))
        s.dump(OUT/'preflight/results.json',checks)
        print(json.dumps(checks,indent=2));return
    ref=OUT/'runs/ours_resident_r1'
    try:
        for name,method,budget,mode in cases:
            folder=OUT/'runs'/f'{name}_r1'
            s.dump(OUT/'state.json',dict(status='running',run=folder.name,pid=os.getpid(),updated=time.time()))
            report.main()
            print(time.strftime('%F %T'),folder.name,flush=True)
            if method=='ours':
                s.execute(folder,'pilot',budget*2**20,mode,1,s.WIDTHS,reference=ref if mode=='auto' else None)
            else:
                driver.disk_run(folder,budget,mode,1)
            report.main()
        s.dump(OUT/'state.json',dict(status='completed',updated=time.time()))
        report.main()
    except BaseException as exc:
        s.dump(OUT/'state.json',dict(status='failed',error=repr(exc),updated=time.time()))
        report.main();raise


if __name__=='__main__':main()
