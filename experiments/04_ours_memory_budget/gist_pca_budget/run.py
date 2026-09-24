"""Budget-matched validation selection, followed by untouched test confirmation."""
import argparse, importlib.util, json, mmap, os, resource, subprocess, time
from pathlib import Path
from prepare import ROOT, WORK, OUT, dump, sha
spec=importlib.util.spec_from_file_location('pca_joint',Path(__file__).resolve().parent.parent/'gist_joint_optimization/run.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
r.OUT=OUT;r.WORK=WORK;r.s.OUT=OUT;r.s.WORK=WORK;r.s.BIN=WORK/'ours_gist_pca_budget'
r.s.PROFILE=ROOT/'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'
original_inputs=r.inputs;original_precondition=r.s.pilot.precondition
CURRENT={}
def inputs(split):
 f=original_inputs(split);f['--implementation-fingerprint']='pca-route-independent-native-with-full4-recompute'
 if CURRENT['dim']!=960:
  f['--pca-route-dir']=str(WORK/f"d{CURRENT['dim']}");f['--pca-tail-mode']=CURRENT.get('tail','norm')
 elif CURRENT['mode']=='paged':
  f['--disk-index-dir']=str(ROOT/'work/ours_memory_budget/gist_route_locality/index');f['--routing-code-layout']='bfs'
 return f
r.inputs=inputs
def precondition(cmd,folder):
 original_precondition(cmd,folder)
 flags=dict(zip(cmd[1::2],cmd[2::2]));directory=flags.get('--pca-route-dir')
 if not directory:return
 records=[]
 for name in ('sidecar.bin','sidecar_bfs.bin'):
  path=Path(directory)/name;size=path.stat().st_size;offset=0
  fd=os.open(path,os.O_RDONLY|os.O_DIRECT)
  try:
   with mmap.mmap(-1,4*2**20) as buffer:
    while offset<size:
     n=os.preadv(fd,[buffer],offset);assert n>0;offset+=n
  finally:os.close(fd)
  records.append(dict(path=str(path),bytes=offset,io='O_DIRECT'))
 dump(folder/'pca_storage_precondition.json',dict(files=records,included_in_query_timing=False,device_cache_controlled=False))
r.s.pilot.precondition=precondition

def setup():
 build=json.loads((OUT/'build.json').read_text());assert sha(r.s.BIN)==build['binary_sha256']
 audit=json.loads((OUT/'bound_audit_v3/audit.json').read_text())
 assert audit['binary_sha256']==build['binary_sha256']
 assert all(x['self_violations']==0 and x['norm_tail_only_violations']==0 for x in audit['rows']), 'lower-bound diagnostic failed'
 assert all(x['deterministic_full_violations']==0 for x in audit['rows'] if x['dim']!=960)
 for p,h in build['sources'].items():assert sha(p)==h,p
 for k in (128,256,512):
  folder=WORK/f'd{k}';info=json.loads((folder/'encoded.json').read_text())
  for name,h in info['files'].items():assert sha(folder/name)==h
 dump(OUT/'calibration/lock.json',json.loads((ROOT/'results/04_ours_memory_budget/gist_joint_optimization/calibration/lock.json').read_text()))
 protocol=OUT/'protocol.json'
 value=dict(budget_mib=538,workers=32,beam=1,target_recall=0.95,dimensions=[128,256,512,960],widths=[40,60,80,100,140,180],fixed_diagnostic_page_quota_mib=4,auto_page_quota_cap_mib=16,warmup_queries_per_width=100,tail='norm',selection='validation only; fastest measured configuration with Recall >= 0.95',test_queries=800,test_repetitions=2,full4_short_ip_reuse=False,pca_train_rows=32768,notes='Original960 retains INT8-query statistical epsilon=1.9 and full4 reuse. PCA uses full-query Cauchy projected bound (epsilon=sqrt(code_dim-1)), residual norm lower bound, numerical margin and full4 recomputation. Thus differences include conservative gate strength, not pure dimension alone. Fixed calibration inherited and checked against measured VmPeak.')
 if protocol.exists():assert json.loads(protocol.read_text())==value
 else:dump(protocol,value)

def execute(dim,mode='paged',widths=(100,),split='tune',rep=1,budget=538,tag='',tail='norm',reference=None,pages=16,record=0):
 global CURRENT
 CURRENT=dict(dim=dim,mode=mode,tail=tail)
 return r.execute(f'd{dim}_{mode}_{tail}{tag}',dict(mode=mode,record=record,pages=pages,inflight=256),split,rep,reference=reference,widths=widths,budget=budget)

def policy(dim,budget=538):
 lock=json.loads((OUT/'calibration/lock.json').read_text());fixed=lock['fixed_bytes'];reserve=lock['reserve_bytes']
 if dim==960:codes=128000000;factors=20000000;extra=0
 else:
  m=json.loads((WORK/f'd{dim}/pca.json').read_text());codes=m['count']*m['code_stride'];factors=m['count']*(m['factor_stride']+4)
  extra=4*(960*960+2*960)+16*dim*dim+32*16*960
 available=budget*2**20-fixed-reserve-factors-extra
 resident=available>=codes
 record=max(0,available-codes) if resident else 0
 # A record cache requires a u32 per node plus at least one record.
 if record<4002000:record=0
 return dict(mode='resident' if resident else 'paged',record=record//2**20,
             codes_bytes=codes,factors_and_norms_bytes=factors,pca_extra_bytes=extra,
             resident_threshold_mib=(fixed+reserve+factors+extra+codes)/2**20)

def smoke():
 for k in (128,256,512):
  paged=execute(k,tag='_parity',split='train',budget=640)
  execute(k,mode='resident',tag='_parity',split='train',budget=640,reference=paged)
 # The unmodified 960 route must match its previously accepted results exactly.
 ref=ROOT/'results/04_ours_memory_budget/gist_route_locality/tune/bfs_i256_r1'
 execute(960,tag='_baseline',reference=ref)
 dump(OUT/'smoke.json',dict(passed=True,checks='all lowdim resident/paged per-query parity; full960 parity with immutable baseline'))

def validation():
 # Fixed-width/cache diagnosis before reallocating the same process budget.
 for k in (960,512,256,128):execute(k,tag='_fixed',pages=4)
 for k in (128,256,512,960):
  p=policy(k);execute(k,mode=p['mode'],record=p['record'],widths=(40,60,80,100,140,180),tag='_grid')
 candidates=[]
 for k in (128,256,512,960):
  p=policy(k);folder=OUT/'tune'/f"d{k}_{p['mode']}_norm_grid_r1"
  rows=json.loads((folder/'result.json').read_text())['summary_rows']
  eligible=[x for x in rows if x['recall']>=0.95]
  if not eligible:
   more=execute(k,mode=p['mode'],record=p['record'],widths=(260,380,580),tag='_extend')
   rows+=json.loads((more/'result.json').read_text())['summary_rows'];eligible=[x for x in rows if x['recall']>=0.95]
  if not eligible:candidates.append(dict(dim=k,eligible=False,policy=p));continue
  best=max(eligible,key=lambda x:x['qps']);width=best['search_width']
  candidates.append(dict(dim=k,eligible=True,policy=p,width=width,validation_recall=best['recall'],validation_qps=best['qps']))
 dump(OUT/'selection_lock.json',dict(target=0.95,candidates=candidates,selected_before_test=True,binary_sha256=sha(r.s.BIN)))

def confirm():
 lock=json.loads((OUT/'selection_lock.json').read_text());assert lock['binary_sha256']==sha(r.s.BIN)
 for rep in (1,2):
  items=lock['candidates'] if rep==1 else list(reversed(lock['candidates']))
  for x in items:
   if x['eligible']:execute(x['dim'],mode=x['policy']['mode'],record=x['policy']['record'],widths=(x['width'],),split='test',rep=rep,tag='_locked')
 dump(OUT/'state.json',dict(status='completed',updated=time.time()))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('stage',choices=['smoke','validation','test','all']);args=a.parse_args();setup()
 if args.stage in ('smoke','all'):smoke()
 if args.stage in ('validation','all'):validation()
 if args.stage in ('test','all'):confirm()
