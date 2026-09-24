"""Calibrate and lock whole-process accounting; pilot gate precedes full runs."""
import argparse,importlib.util,json,math,os,resource,subprocess,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];MIB=2**20
spec=importlib.util.spec_from_file_location('auto_driver',HERE/'driver_base.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
s.WORK=ROOT/'work/ours_memory_budget/gist_fixed_factors_budget';s.OUT=ROOT/'results/04_ours_memory_budget/gist_memory_auto_policy';s.BIN=s.WORK/'ours_gist_fixed_factors_budget'
OUT=s.OUT;WIDTHS=[100,300,580]

def account(folder):
 p=json.loads((folder/'memory_plan.json').read_text());assert p['status']=='admitted' and p['factors_resident']
 u=p['budget_bytes']-p['fixed_bytes']-p['reserve_bytes']-p['factors_bytes'];assert p['usable_bytes']==u
 if p['mode']=='paged':
  assert p['optional_cache_bytes']==0
  known=p['factors_bytes']+p['paging_service_bytes']+p['paging_scratch_bytes']+p['routing_page_data_bytes']+p['routing_page_metadata_bytes']
 else:
  assert p['mode'] in ['resident','hot_dynamic'];assert p['routing_capacity_pages']==p['max_inflight_pages']==0
  known=p['codes_bytes']+p['factors_bytes']+p['optional_cache_bytes']
 assert p['expected_peak_bytes']==p['fixed_bytes']+known
 assert p['admission_bytes']==p['expected_peak_bytes']+p['reserve_bytes']<=p['budget_bytes']
 return dict(passed=True,known_bytes=known,fixed_bytes=p['fixed_bytes'],reserve_bytes=p['reserve_bytes'],unused_bytes=p['budget_bytes']-p['admission_bytes'])

original_verify=s.verify
def verify(folder,split,widths,reference,calibration,mem):
 original_verify(folder,split,widths,reference,calibration,mem)
 assert mem['hard_limit_verified']
 account(folder)
 # Physical routing reads are shared, charged once, and added to fine-record I/O.
 traces=s.pilot.traces(folder);rs=s.stats(folder)
 for width in widths:
  cache=json.loads((folder/'memory_stats'/f'L{width}.json').read_text());ops=cache['operations']
  for key,op,rk in [('io_requests','io_requests','reads'),('sectors_4k','read_pages','reads'),('bytes_read','read_bytes','bytes')]:
   assert sum(r[key] for r in traces if r['search_width']==width)==sum(v[op] for v in ops.values())+rs.get(width,{}).get(rk,0),(folder,width,key)
 # Rewrite acceptance only after all additional checks succeeded.
 s.dump(folder/'acceptance.json',dict(passed=True,binary_sha256=s.sha(s.BIN),queries=len(traces),reference=str(reference) if reference else None,calibration=calibration,whole_process_accounting=True,physical_io=True,
  files={str(p.relative_to(folder)):s.sha(p) for p in folder.rglob('*') if p.is_file() and p.name!='acceptance.json'}))
s.verify=verify

def calibrate():
 lock=OUT/'calibration/lock.json'
 if lock.exists():
  v=json.loads(lock.read_text());assert v['binary_sha256']==s.sha(s.BIN);return v
 observations=[]
 for mode,cap in [('resident',0),('paged',MIB),('hot_dynamic',64*MIB)]:
  folder=OUT/'calibration'/mode;s.execute(folder,'calibration',2*1024**3,mode,1,WIDTHS,cap=cap,calibration=True)
  p=json.loads((folder/'memory_plan.json').read_text());m=json.loads((folder/'memory_measurement.json').read_text())
  allocated=max(json.loads(x.read_text())['cache_reserved_bytes'] for x in (folder/'memory_stats').glob('L*.json'))
  known=account(folder)['known_bytes']-p['optional_cache_bytes']+allocated
  stages=[line for line in (folder/'terminal.log').read_text().splitlines() if line.startswith('memory_stage ')]
  observations.append(dict(mode=mode,VmPeak_bytes=m['sampled_high_water_bytes']['VmPeak'],RSS_bytes=m['kernel_wait4_peak_rss_bytes'],known_bytes=known,nonrouting_peak_bytes=max(0,m['sampled_high_water_bytes']['VmPeak']-known),stages=stages))
 # Never reduce the inherited 357 MiB merely to admit smaller budgets.
 failed=OUT/'archive/v1/validation/resident_exact'
 m=json.loads((failed/'memory_measurement.json').read_text());p=json.loads((failed/'memory_plan.json').read_text())
 observations.append(dict(mode='resident_exact_previous_failure',VmPeak_bytes=m['sampled_high_water_bytes']['VmPeak'],known_bytes=p['codes_bytes']+p['factors_bytes'],nonrouting_peak_bytes=m['sampled_high_water_bytes']['VmPeak']-p['codes_bytes']-p['factors_bytes'],source=str(failed)))
 fixed=max(357*MIB,math.ceil((max(x['nonrouting_peak_bytes'] for x in observations)+8*MIB)/MIB)*MIB)
 config=dict(locked=True,version='v2',binary_sha256=s.sha(s.BIN),fixed_bytes=fixed,reserve_bytes=64*MIB,full_query_extra_bytes=8*MIB,inherited_fixed_floor_bytes=357*MIB,observations=observations,workers=32,scope='whole-process virtual address space; fixed excludes factors and reserve')
 s.dump(lock,config);return config

def preflight(budget,label):
 folder=OUT/'preflight'/label
 if (folder/'status.json').exists():return json.loads((folder/'status.json').read_text())['status']=='admitted'
 folder.mkdir(parents=True,exist_ok=True)
 cmd=s.command(folder,'pilot',budget,'auto',0,[100],preflight=True)
 def limit():resource.setrlimit(resource.RLIMIT_AS,(budget,budget));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,preexec_fn=limit,env=dict(os.environ,MALLOC_ARENA_MAX='2'),timeout=60)
 (folder/'terminal.log').write_text(r.stdout);s.dump(folder/'command.json',cmd)
 p=json.loads((folder/'memory_plan.json').read_text());assert (r.returncode==0)==(p['status']=='admitted')
 if r.returncode:assert 'deficit' in r.stdout
 else:account(folder)
 s.dump(folder/'status.json',dict(status=p['status'],budget_bytes=budget,returncode=r.returncode,qps=None));return r.returncode==0

def refresh():
 spec=importlib.util.spec_from_file_location("auto_policy_report",HERE/"report.py");report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)
 report.main(render=False)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--full',action='store_true');parser.add_argument('--preflight-only',action='store_true');a=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
 control=json.loads((OUT/'execution_control.json').read_text()) if (OUT/'execution_control.json').exists() else {}
 if not control.get('full_enabled',True):a.full=False
 build=json.loads((s.WORK/'build.json').read_text());assert s.sha(s.BIN)==build['binary_sha256']
 for p,h in build['generated_sources'].items():assert s.sha(p)==h,p
 s.dump(OUT/'build.json',build)
 try:
  config=calibrate();preflight(2*1024**3,'threshold_probe')
  p=json.loads((OUT/'preflight/threshold_probe/memory_plan.json').read_text());t={k:p[k] for k in ['threshold_paged_bytes','threshold_resident_bytes','threshold_record_cache_bytes']}
  budgets=sorted({math.ceil(x/MIB)+d for x in t.values() for d in [-16,0,16]}|{512,640,768,1024,1536,2048})
  admitted=[b for b in budgets if preflight(b*MIB,str(b))]
  for b in [256,384]:assert not preflight(b*MIB,str(b))
  for name,value in t.items():
   for d in [-1,0,1]:preflight(value+d,f'{name}_{d:+d}')
  definition=dict(version='v2',thresholds=t,requested_mib=budgets,admitted_mib=admitted,expected_rejections_mib=[256,384],calibration_sha256=s.sha(OUT/'calibration/lock.json'),binary_sha256=s.sha(s.BIN),sources={p.name:s.sha(p) for p in HERE.iterdir() if p.suffix in ['.py','.rs']},profile={p.name:s.sha(p) for p in s.PROFILE.iterdir() if p.is_file()},pilot_widths=WIDTHS,full_widths=s.WIDTHS,repeats=2)
  frozen=OUT/'experiment.json'
  if frozen.exists():assert json.loads(frozen.read_text())==definition,'experiment source or calibration changed'
  else:s.dump(frozen,definition)
  refresh()
  if a.preflight_only:s.dump(OUT/'state.json',dict(status='preflight_completed'));return
  # Byte-exact runnable resident boundary, validation data only.
  s.execute(OUT/'validation/resident_exact','calibration',t['threshold_resident_bytes'],'auto',1,WIDTHS)
  for split,widths in [('pilot',WIDTHS)]+([('full',s.WIDTHS)] if a.full else []):
   if split=='full':assert json.loads((OUT/'pilot/completed.json').read_text())['passed']
   for rep in [1,2]:
    ref=OUT/split/f'resident_reference_r{rep}';s.execute(ref,split,2*1024**3,'resident',rep,widths);refresh()
    for b in (admitted if rep==1 else list(reversed(admitted))):
     s.execute(OUT/split/f'budget_{b}_r{rep}',split,b*MIB,'auto',rep,widths,reference=ref);refresh()
   s.dump(OUT/split/'completed.json',dict(passed=True,budgets=admitted,repeats=2,widths=widths));refresh()
  s.dump(OUT/'state.json',dict(status='completed',full=a.full,pilot=True,updated=time.time()));refresh()
 except BaseException as e:
  s.dump(OUT/'state.json',dict(status='failed',error=str(e),traceback=traceback.format_exc(),updated=time.time()));refresh();raise
if __name__ == '__main__':
    # Historical implementation above remains importable for forensic replay.
    # Executing this established CLI now uses the validated 256-page policy.
    _spec = importlib.util.spec_from_file_location('current_auto_entry', HERE.parent/'gist_auto_inflight256.py')
    _current = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_current)
    _current.compatibility_main()

