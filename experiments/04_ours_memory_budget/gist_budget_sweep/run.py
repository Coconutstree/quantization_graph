"""Calibrate once, lock admission, preflight all budgets, pilot then full sweep."""
import argparse,hashlib,importlib.util,json,math,os,struct,sys,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
WORK=ROOT/'work/ours_memory_budget/gist_budget_sweep';OUT=ROOT/'results/04_ours_memory_budget/gist_budget_sweep';BIN=WORK/'ours_gist_budget_sweep'
sys.path.insert(0,str(HERE.parent/'routing_paged'))
spec=importlib.util.spec_from_file_location('paging_pilot',HERE.parent/'routing_paged/run.py');pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)
MIB=2**20;BUDGETS=[256,384,512,640,768,1024,1536,2048];WIDTHS=list(range(10,31))+list(range(40,101,10))+list(range(140,581,40))
PROFILE=ROOT/'results/04_ours_memory_budget/dynamic_records/gist/profile'
FIELDS=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates']
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def progress(s,**extra):
 print(time.strftime('%F %T'),s,flush=True);dump(OUT/'state.json',dict(status='running',stage=s,pid=os.getpid(),updated=time.time(),**extra))
def flags(c):return dict(zip(c[1::2],c[2::2]))
def base(split):
 f=flags(json.loads((ROOT/'results/04_ours_memory_budget/dynamic_records/gist/runs/hot_dynamic_r1/command.json').read_text()))
 for k in list(f):
  if k.startswith('--memory-') or k.startswith('--routing-'):del f[k]
 f.update({'--run-id':'native_integration_gist_budget_sweep','--native-binary-sha256':sha(BIN),'--implementation-fingerprint':'gist-budget-auto','--workers':'32','--warmup-queries':'100','--integration-beams':'1','--parity-mode':'external',
 '--memory-profile-dir':str(PROFILE),'--auto-calibration-file':str(OUT/'calibration/lock.json')})
 if split=='full':return f
 folder=OUT/'inputs'/split;folder.mkdir(parents=True,exist_ok=True)
 ids=list(range(100)) if split=='calibration' else list(struct.unpack('<800I',Path(f['--query-order']).read_bytes())[:100])
 source=ROOT/'artifacts/query_splits/gist/shared'
 for flag,name in [('--query','query.fvecs'),('--groundtruth','gt.ivecs')]:
  src=source/('validation_'+name) if split=='calibration' else Path(f[flag]);raw=src.read_bytes();stride=4*(struct.unpack_from('<I',raw)[0]+1)
  data=b''.join(raw[i*stride:(i+1)*stride] for i in ids);target=folder/name
  if target.exists():assert target.read_bytes()==data
  else:target.write_bytes(data)
  f[flag]=str(target)
 target=folder/'order.u32';data=struct.pack('<100I',*range(100))
 if target.exists():assert target.read_bytes()==data
 else:target.write_bytes(data)
 f['--query-order']=str(target);f['--query-order-sha256']=sha(target);f['--query-split-sha256']=sha(f['--query'])
 dump(folder/'selection.json',dict(split=split,original_ids=ids,files={p.name:sha(p) for p in folder.iterdir() if p.suffix in ['.fvecs','.ivecs','.u32']}))
 return f

def command(folder,split,budget,mode,rep,widths,cap=None,calibration=False,preflight=False):
 f=base(split);f.update({'--memory-policy':mode,'--search-dram-budget-gib':repr(budget/1024**3),'--result-json':str(folder/'result.json'),
 '--query-trace':str(folder/'queries.jsonl'),'--memory-stats-dir':str(folder/'memory_stats'),'--auto-plan-output':str(folder/'memory_plan.json'),
 '--repeat-id':str(rep),'--integration-widths':','.join(map(str,widths)),'--memory-cache-bytes':str(cap) if cap is not None else 'auto'})
 if calibration:f['--auto-calibration']='1'
 if preflight:f['--preflight-only']='1'
 return [str(BIN)]+[x for k,v in f.items() for x in (k,v)]
def stats(folder):
 raw={}
 for line in (folder/'terminal.log').read_text().splitlines():
  if line.startswith('routing_stats '):
   x=dict(p.split('=',1) for p in line.split()[1:]);width=int(x.pop('L'));phase=x.pop('phase');raw[width,phase]={k:int(v) for k,v in x.items()}
 result={}
 for (w,phase),r in raw.items():
  if phase!='measurement_end':continue
  a=raw[w,'measurement_start'];fixed=['capacity','inflight','reserved_bytes','peak_active','peak_pages']
  result[w]={k:v if k in fixed else v-a[k] for k,v in r.items()}
 return result

def verify(folder,split,widths,reference,calibration,mem):
 assert mem['user_address_space_budget_passed']
 plan=json.loads((folder/'memory_plan.json').read_text())
 if not calibration:assert mem['sampled_high_water_bytes']['VmPeak']<=plan['expected_peak_bytes'],('peak exceeded plan',mem,plan)
 data=pilot.traces(folder);assert len(data)==(800 if split=='full' else 100)*len(widths)
 st=stats(folder)
 for x in st.values():assert x['peak_active']<=x['inflight']<=64 and x['peak_pages']<=x['capacity']
 if plan['mode'] in ['resident','hot_dynamic']:assert not st
 for p in (folder/'memory_stats').glob('L*.json'):
  x=json.loads(p.read_text());assert x['cache_reserved_bytes']<=plan['optional_cache_bytes']
  if plan['optional_cache_bytes']==0:assert x['cache_reserved_bytes']==0
 if reference:
  ref={(r['search_width'],r['query_id']):r for r in pilot.traces(reference)}
  for r in data:
   for key in FIELDS:assert r[key]==ref[r['search_width'],r['query_id']][key],(key,r['query_id'])
  if st:
   for w,x in st.items():
    for key,counter in [('sectors_4k','reads'),('bytes_read','bytes'),('io_requests','reads')]:
     assert sum(r[key]-ref[r['search_width'],r['query_id']][key] for r in data if r['search_width']==w)==x[counter]
 dump(folder/'routing_stats.json',st)
 dump(folder/'acceptance.json',dict(passed=True,binary_sha256=sha(BIN),queries=len(data),reference=str(reference) if reference else None,calibration=calibration,
  files={str(p.relative_to(folder)):sha(p) for p in folder.rglob('*') if p.is_file() and p.name!='acceptance.json'}))

def execute(folder,split,budget,mode,rep,widths,reference=None,cap=None,calibration=False):
 if (folder/'acceptance.json').exists():
  a=json.loads((folder/'acceptance.json').read_text());assert a['binary_sha256']==sha(BIN)
  for name,h in a['files'].items():assert sha(folder/name)==h,(folder,name)
  return
 if folder.exists():raise RuntimeError('Unaccepted run exists; preserve and inspect: '+str(folder))
 folder.mkdir(parents=True);(folder/"memory_stats").mkdir();cmd=command(folder,split,budget,mode,rep,widths,cap,calibration)
 progress(str(folder.relative_to(OUT)))
 try:
  pilot.precondition(cmd,folder);mem=pilot.measurement.measure(cmd,folder,budget=budget,timeout=24*3600)
  verify(folder,split,widths,reference,calibration,mem)
 except Exception as e:
  log=(folder/'terminal.log').read_text() if (folder/'terminal.log').exists() else ''
  kind='memory_plan_exceeded' if 'peak exceeded plan' in str(e) else 'runtime_oom' if any(x in log for x in ['std::bad_alloc','memory allocation','Cannot allocate memory','os error 12']) else 'io_error' if any(x in log for x in ['routing read failed','io_submit failed','io_getevents failed']) else 'validation_or_runtime_error'
  if (folder/'memory_measurement.json').exists() and json.loads((folder/'memory_measurement.json').read_text()).get('timed_out'):kind='timeout'
  dump(folder/'failure.json',dict(status='failed',classification=kind,error=str(e),no_retry=True));raise

def calibrate():
 lock=OUT/'calibration/lock.json'
 if lock.exists():
  x=json.loads(lock.read_text());assert x['binary_sha256']==sha(BIN);return x
 calibration=[]
 for mode,cap in [('resident',0),('factors',MIB),('paged',MIB),('hot_dynamic',64*MIB)]:
  folder=OUT/'calibration'/mode
  execute(folder,'calibration',2*1024**3,mode,1,[100,300,580],cap=cap,calibration=True)
  mem=json.loads((folder/'memory_measurement.json').read_text());p=json.loads((folder/'memory_plan.json').read_text())
  cache=max(json.loads(x.read_text())['cache_reserved_bytes'] for x in (folder/'memory_stats').glob('L*.json'))
  persistent=(p['codes_bytes']+p['factors_bytes']+cache if mode in ['resident','hot_dynamic'] else (p['factors_bytes'] if mode=='factors' else 0)+p['routing_capacity_pages']*4352+10*MIB+p['paging_scratch_bytes'])
  calibration.append(dict(mode=mode,peak=mem['sampled_high_water_bytes']['VmPeak'],known_routing_cache_bytes=persistent,nonrouting_peak=max(0,mem['sampled_high_water_bytes']['VmPeak']-persistent),run=str(folder)))
 # Full 800-row input/results allowance plus 64 MiB process-wide safety margin.
 unmodeled=max(x['nonrouting_peak'] for x in calibration);fixed=math.ceil((unmodeled+8*MIB)/MIB)*MIB+64*MIB
 threshold=fixed+148000000
 x=dict(locked=True,binary_sha256=sha(BIN),observations=calibration,fixed_bytes=fixed,full_query_extra_bytes=8*MIB,safety_bytes=64*MIB,
   resident_threshold_bytes=threshold,budgets_mib=sorted(set(BUDGETS+[math.floor(threshold/MIB)-16,math.ceil(threshold/MIB)+16])),
   scope='whole process virtual address space; observed allocator/thread peaks included once; no worker-count reduction',index='existing GIST BFS',workers=32)
 dump(lock,x);return x

def low_budget_probes():
 # Diagnostic attempts, not QPS evidence; keep failures and never retry them.
 for mib in [256,384,512]:
  folder=OUT/'diagnostics'/f'low_budget_{mib}'
  if (folder/'probe.json').exists():continue
  if folder.exists():raise RuntimeError('unclassified low-budget probe exists')
  folder.mkdir(parents=True);(folder/'memory_stats').mkdir()
  cmd=command(folder,'calibration',mib*MIB,'paged',0,[10],cap=64*4352,calibration=True)
  cmd[cmd.index('--warmup-queries')+1]='0'
  progress(f'low_budget_probe_{mib}')
  try:
   pilot.precondition(cmd,folder);mem=pilot.measurement.measure(cmd,folder,budget=mib*MIB,timeout=180)
   assert mem['user_address_space_budget_passed'];status='diagnostic_run_passed'
  except Exception:
   log=(folder/'terminal.log').read_text()
   if any(x in log for x in ['std::bad_alloc','memory allocation','Cannot allocate memory','os error 12','Resource temporarily unavailable']):status='runtime_memory_or_thread_resource_failure'
   elif 'worker failed during setup/warmup' in log:status='runtime_setup_failure_cause_not_exposed'
   else:raise
  dump(folder/'probe.json',dict(status=status,budget_mib=mib,width=10,queries=100,workers=32,cache_pages=64,calibration_only=True,not_full_width_feasibility=True,binary_sha256=sha(BIN)))

def preflight(config):
 import subprocess,resource
 accepted=[]
 for mib in config['budgets_mib']:
  folder=OUT/'preflight'/str(mib);folder.mkdir(parents=True,exist_ok=True)
  cmd=command(folder,'pilot',mib*MIB,'auto',0,[100],preflight=True)
  def limit():resource.setrlimit(resource.RLIMIT_AS,(mib*MIB,mib*MIB));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,preexec_fn=limit,env=dict(os.environ,MALLOC_ARENA_MAX='2'),timeout=60)
  (folder/'terminal.log').write_text(result.stdout);dump(folder/'command.json',cmd)
  if result.returncode==0:accepted.append(mib)
  else:
   # Only explicit admission refusal is an expected unavailable point.
   if 'deficit' not in result.stdout:raise RuntimeError('unexpected preflight failure: '+result.stdout[-2000:])
  dump(folder/'status.json',dict(status='admitted' if result.returncode==0 else 'admission_rejected',returncode=result.returncode,qps=None))
 dump(OUT/'budgets.json',dict(requested=config['budgets_mib'],admitted=accepted));return accepted

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--calibrate',action='store_true');parser.add_argument('--pilot',action='store_true');parser.add_argument('--full',action='store_true');parser.add_argument('--preflight-only',action='store_true');a=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
 build=json.loads((WORK/'build.json').read_text());assert sha(BIN)==build['binary_sha256']
 for p,h in build['generated_sources'].items():assert sha(p)==h,p
 dump(OUT/'build.json',build)
 try:
  if a.preflight_only and not (OUT/'calibration/lock.json').exists():raise RuntimeError('Run --calibrate before --preflight-only')
  config=calibrate()
  if not a.preflight_only:low_budget_probes()
  budgets=preflight(config)
  if a.preflight_only or not(a.pilot or a.full):
   dump(OUT/'state.json',dict(status='completed',stage='calibration_and_preflight',updated=time.time()));return
  provenance=dict(binary_sha256=sha(BIN),driver_sha256=sha(Path(__file__)),calibration_sha256=sha(OUT/'calibration/lock.json'),profile={p.name:sha(p) for p in PROFILE.iterdir() if p.is_file()})
  freeze=OUT/'performance_provenance.json'
  if freeze.exists():assert json.loads(freeze.read_text())==provenance,'performance provenance changed'
  else:dump(freeze,provenance)
  for split,widths,enabled in [('pilot',[100,300,580],a.pilot or a.full),('full',WIDTHS,a.full)]:
   if not enabled:continue
   for rep in [1,2]:
    ref=OUT/split/f'resident_reference_r{rep}';execute(ref,split,2*1024**3,'resident',rep,widths)
    order=budgets if rep==1 else list(reversed(budgets))
    for mib in order:execute(OUT/split/f'budget_{mib}_r{rep}',split,mib*MIB,'auto',rep,widths,reference=ref)
   dump(OUT/split/'completed.json',dict(passed=True,budgets=budgets,repeats=2,widths=widths))
  dump(OUT/'state.json',dict(status='completed',updated=time.time(),full=a.full,pilot=True))
 except BaseException as e:
  dump(OUT/'state.json',dict(status='failed',error=str(e),traceback=traceback.format_exc(),updated=time.time()));raise
if __name__=='__main__':main()
