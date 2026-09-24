"""Independent test queries; data capacity and process protection are separate."""
import importlib.util,json,struct,time,subprocess,traceback,os
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('sweep',HERE.parent/'gist_budget_sweep/run.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
s.WORK=ROOT/'work/ours_memory_budget/gist_data_budget';s.OUT=ROOT/'results/04_ours_memory_budget/gist_data_budget';s.BIN=s.WORK/'ours_gist_data_budget'
MIB=2**20;PROCESS=2*1024**3;WIDTHS=[100,300,580]
original_command=s.command
ACTIVE_DATA=None
def command(*args,**kwargs):
 return original_command(*args,**kwargs)+['--data-cache-budget-bytes',str(ACTIVE_DATA)]
s.command=command

def account(folder):
 p=json.loads((folder/'memory_plan.json').read_text());data=p['data_cache_budget_bytes']
 routing=p['codes_bytes']+p['factors_bytes']
 if p['mode'] in ['resident','hot_dynamic']:
  spent=routing+p['optional_cache_bytes'];assert data>=routing
  assert p['routing_capacity_pages']==0 and p['max_inflight_pages']==0
 else:
  spent=(p['factors_bytes'] if p['mode']=='factors' else 0)+p['routing_capacity_pages']*4352
  assert p['optional_cache_bytes']==0
 assert spent<=data,(spent,data)
 return dict(data_budget_bytes=data,allocated_plan_bytes=spent,unused_plan_bytes=data-spent,mode=p['mode'],passed=True)

def main():
 global ACTIVE_DATA
 out=s.OUT;out.mkdir(parents=True,exist_ok=True)
 old=ROOT/'results/04_ours_memory_budget/gist_budget_cache_reuse'
 build=json.loads((s.WORK/'build.json').read_text());assert s.sha(s.BIN)==build['binary_sha256']
 for p,h in build['generated_sources'].items():assert s.sha(p)==h
 config=json.loads((old/'calibration/lock.json').read_text())
 # This is inherited non-data headroom, not a data-residency threshold.
 config.update(binary_sha256=s.sha(s.BIN),scope='Non-data safety envelope inherited from same cache/query implementation; never used to choose data residency',inherited_calibration=str(old/'calibration/lock.json'))
 lock=out/'calibration/lock.json'
 if lock.exists():assert json.loads(lock.read_text())==config
 else:s.dump(lock,config)
 flags=s.base('pilot');index=Path(flags['--disk-index-dir'])
 with (index/'ours_db1_sidecar.bin').open('rb') as f:magic,codes,factors=struct.unpack('<8sQQ',f.read(24))
 assert magic==b'QG05OSC1' and (index/'ours_db1_sidecar.bin').stat().st_size==24+codes+factors
 total=codes+factors
 cases=[('64MiB',64*MIB),('128MiB',128*MIB),('Tminus1byte',total-1),('Texact',total),('Tplus16MiB',total+16*MIB),('256MiB',256*MIB),('512MiB',512*MIB)]
 definition=dict(data_scope='Resident codes/factors + routing page slots including metadata + optional fine-record cache including metadata; centroid, scheduler, scratch, worker caches/stacks/visited and other process overhead excluded',codes_bytes=codes,factors_bytes=factors,resident_data_threshold_bytes=total,process_cap_bytes=PROCESS,cases=cases,queries='independent test first100; original query-order selection',widths=WIDTHS,warmup=100,repeats=2,workers=32,binary_sha256=s.sha(s.BIN),driver_sha256=s.sha(Path(__file__)))
 specpath=out/'experiment.json'
 if specpath.exists():
  saved=json.loads(specpath.read_text())
  canonical=json.loads(json.dumps(definition))
  assert {k:v for k,v in saved.items() if k!='driver_sha256'}=={k:v for k,v in canonical.items() if k!='driver_sha256'}
  if saved['driver_sha256']!=canonical['driver_sha256']:
   revision=json.loads((out/'validation/driver_revision.json').read_text())
   assert revision['before_sha256']==saved['driver_sha256'] and revision['after_sha256']==canonical['driver_sha256']
 else:s.dump(specpath,definition)
 s.dump(out/'build.json',build)
 try:
  for label,data in cases:
   ACTIVE_DATA=data;folder=out/'preflight'/label;folder.mkdir(parents=True,exist_ok=True)
   cmd=s.command(folder,'pilot',PROCESS,'auto',0,WIDTHS,preflight=True)
   # Actual executions below verify hard=soft RLIMIT_AS; preflight uses the same declared cap.
   r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=dict(os.environ,MALLOC_ARENA_MAX='2'),check=True)
   (folder/'terminal.log').write_text(r.stdout);s.dump(folder/'data_accounting.json',account(folder))
  for rep in [1,2]:
   ACTIVE_DATA=total;ref=out/'runs'/f'resident_reference_r{rep}'
   s.execute(ref,'pilot',PROCESS,'resident',rep,WIDTHS)
   order=cases if rep==1 else list(reversed(cases))
   for label,data in order:
    ACTIVE_DATA=data;folder=out/'runs'/f'{label}_r{rep}'
    s.execute(folder,'pilot',PROCESS,'auto',rep,WIDTHS,reference=ref)
    # Keep added evidence outside the acceptance-hashed run directory.
    s.dump(out/'validation'/f'{folder.name}_data_accounting.json',account(folder))
  s.dump(out/'state.json',dict(status='completed',groups=16,queries=4800,scope='data budget sweep, independent test100, two rounds',updated=time.time()))
 except BaseException as e:
  s.dump(out/'state.json',dict(status='failed',error=str(e),traceback=traceback.format_exc(),updated=time.time()));raise
if __name__=='__main__':main()
