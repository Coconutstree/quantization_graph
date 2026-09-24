"""Adopt validated shared-I/O concurrency in the GIST auto policy.

Build/preflight do not launch performance sweeps. --run explicitly runs test100.
Frozen 64-page artifacts are never overwritten.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
WORK=ROOT/'work/ours_memory_budget/gist_auto_inflight256'
OUT=ROOT/'results/04_ours_memory_budget/gist_auto_inflight256'
OLD=ROOT/'work/ours_memory_budget/gist_fixed_factors_budget'
spec=importlib.util.spec_from_file_location('adopted_auto',HERE/'gist_fixed_factors_budget/run.py')
auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)
s=auto.s
s.WORK=WORK;s.OUT=OUT;s.BIN=WORK/'ours_gist_auto_inflight256'


def build():
    assert not WORK.exists(), 'Preserve existing build; use --preflight or --run'
    native=WORK/'native';shutil.copytree(OLD/'native',native)
    p=native/'Cargo.toml'
    p.write_text(p.read_text().replace('ours-gist-fixed-factors-budget','ours-gist-auto-inflight256').replace('ours_gist_fixed_factors_budget','ours_gist_auto_inflight256'))
    shutil.copy2(HERE/'gist_fixed_factors_budget/auto_memory.rs',native/'auto_memory.rs')
    cargo=['cargo','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')]
    with (WORK/'build.log').open('w') as log:
        subprocess.run([cargo[0],'build','--release',*cargo[1:]],stdout=log,stderr=subprocess.STDOUT,check=True)
    shutil.copy2(ROOT/'src/graph_core/target/release'/s.BIN.name,s.BIN)
    with (WORK/'tests.log').open('w') as log:
        for group in ['auto_memory::tests','disk_port::routing::tests']:
            subprocess.run([cargo[0],'test','--release',*cargo[1:],group,'--','--test-threads=1'],stdout=log,stderr=subprocess.STDOUT,check=True)
    s.dump(WORK/'build.json',dict(binary_sha256=s.sha(s.BIN),parent_build_sha256=s.sha(OLD/'build.json'),
        default_shared_inflight=256,worker_candidate_window=64,
        sources={str(p):s.sha(p) for p in native.iterdir() if p.is_file()}))


def setup():
    b=json.loads((WORK/'build.json').read_text())
    assert s.sha(s.BIN)==b['binary_sha256']
    for p,h in b['sources'].items():assert s.sha(p)==h,p
    s.dump(OUT/'build.json',b)
    old=ROOT/'results/04_ours_memory_budget/gist_memory_auto_policy/calibration/lock.json'
    if not (OUT/'calibration/lock.json').exists():
        s.dump(OUT/'calibration/lock.json',json.loads(old.read_text()))
    assert s.sha(OUT/'calibration/lock.json')==s.sha(old)
    s.dump(OUT/'policy.json',dict(default_shared_inflight=256,override_flag='--routing-max-inflight-pages',
        allowed_range=[1,256],clamp_to_page_capacity=True,worker_candidate_window=64,
        calibration='Inherited fixed/reserve; every measured run must pass actual VmPeak gate',
        calibration_source=str(old),evidence='results/04_ours_memory_budget/gist_joint_optimization/report.md'))


def preflight():
    cases=[('default538',538,'auto',None,None,256),('override64',538,'auto',None,64,64),
           ('small522',522,'auto',None,None,83),('one_page',538,'paged',4352,None,1),
           ('resident640',640,'auto',None,None,0)]
    accepted=[]
    for name,mib,mode,cap,override,expected in cases:
        folder=OUT/'preflight'/name;folder.mkdir(parents=True,exist_ok=True)
        cmd=s.command(folder,'pilot',mib*s.MIB,mode,0,[100],cap=cap,preflight=True)
        if override is not None:cmd+=['--routing-max-inflight-pages',str(override)]
        def limits():resource.setrlimit(resource.RLIMIT_AS,(mib*s.MIB,mib*s.MIB))
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,preexec_fn=limits,env=dict(os.environ,MALLOC_ARENA_MAX='2'),timeout=60)
        (folder/'terminal.log').write_text(p.stdout);s.dump(folder/'command.json',cmd)
        assert p.returncode==0,p.stdout
        plan=json.loads((folder/'memory_plan.json').read_text())
        assert plan['max_inflight_pages']==expected,(name,plan)
        auto.account(folder)
        accepted.append(dict(case=name,passed=True,max_inflight_pages=expected))
    s.dump(OUT/'preflight/results.json',accepted)
    print(json.dumps(accepted,indent=2))


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--build',action='store_true');p.add_argument('--preflight','--preflight-only','--prepare-only',dest='preflight',action='store_true');p.add_argument('--run',action='store_true')
    p.add_argument('--budgets',type=int,nargs='+',default=[538,616,768,1024,1536]);p.add_argument('--widths',type=int,nargs='+',default=s.WIDTHS)
    a=p.parse_args(argv)
    if a.build:build()
    setup()
    if a.preflight or not a.run:preflight()
    if a.run:
        definition=dict(binary_sha256=s.sha(s.BIN),budgets_mib=a.budgets,widths=a.widths,queries=100,repeats=1,default_shared_inflight=256,worker_candidate_window=64)
        lock=OUT/'execution.json'
        if lock.exists():assert json.loads(lock.read_text())==definition, 'Existing sweep configuration differs; preserve its results'
        s.dump(lock,definition)
        for rep in [1]:
            ref=OUT/'pilot'/f'resident_reference_r{rep}'
            s.execute(ref,'pilot',2048*s.MIB,'resident',rep,a.widths)
            for budget in a.budgets if rep==1 else reversed(a.budgets):
                s.execute(OUT/'pilot'/f'budget_{budget}_r{rep}','pilot',budget*s.MIB,'auto',rep,a.widths,reference=ref)


if __name__=='__main__':main()


def compatibility_main(argv=None):
    """Old CLI names adopt the new policy; legacy module APIs stay importable."""
    main(argv)
