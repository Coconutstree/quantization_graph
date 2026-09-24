"""Select route shortlist and graph width on validation, never on test."""
import importlib.util,json,mmap,os,sys,time
from pathlib import Path
from prepare import ROOT,WORK,OUT,ASSETS,dump,sha
spec=importlib.util.spec_from_file_location('routing_joint',Path(__file__).resolve().parent.parent/'gist_joint_optimization/run.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
r.OUT=OUT;r.WORK=WORK;r.s.OUT=OUT;r.s.WORK=WORK;r.s.BIN=WORK/'ours_gist_pca_routing'
r.s.PROFILE=ROOT/'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'
original_inputs=r.inputs;old_precondition=r.s.pilot.precondition
CURRENT={}
def inputs(split):
 f=original_inputs(split);f['--implementation-fingerprint']='pca-ranking-full4-verification-no-lowdim-hard-prune'
 if CURRENT['dim']!=960:
  f['--pca-route-dir']=str(ASSETS/f"d{CURRENT['dim']}");f['--pca-tail-mode']='route';f['--pca-route-keep']=str(CURRENT['keep'])
 elif CURRENT['mode']=='paged':
  f['--disk-index-dir']=str(ROOT/'work/ours_memory_budget/gist_route_locality/index');f['--routing-code-layout']='bfs'
 return f
r.inputs=inputs
def precondition(cmd,folder):
 old_precondition(cmd,folder);f=dict(zip(cmd[1::2],cmd[2::2]));d=f.get('--pca-route-dir')
 if not d:return
 records=[]
 for name in ('sidecar.bin','sidecar_bfs.bin'):
  path=Path(d)/name;size=path.stat().st_size;offset=0;fd=os.open(path,os.O_RDONLY|os.O_DIRECT)
  try:
   with mmap.mmap(-1,4*2**20)as buffer:
    while offset<size:
     n=os.preadv(fd,[buffer],offset);assert n>0;offset+=n
  finally:os.close(fd)
  records.append(dict(path=str(path),bytes=offset,io='O_DIRECT'))
 dump(folder/'pca_storage_precondition.json',dict(files=records,device_cache_controlled=False,included_in_query_timing=False))
r.s.pilot.precondition=precondition
def policy(dim,budget=538):
 lock=json.loads((OUT/'calibration/lock.json').read_text());fixed=lock['fixed_bytes'];reserve=lock['reserve_bytes']
 if dim==960:codes=128000000;factors=20000000;extra=0
 else:
  m=json.loads((ASSETS/f'd{dim}/pca.json').read_text());codes=m['count']*m['code_stride'];factors=m['count']*m['factor_stride'];extra=4*(960*dim+960)+16*dim*dim+32*16*960
 available=budget*2**20-fixed-reserve-factors-extra;resident=available>=codes
 record=max(0,available-codes)if resident else 0
 if record<4002000:record=0
 return dict(mode='resident'if resident else'paged',record=record//2**20,codes_bytes=codes,factors_bytes=factors,pca_extra_bytes=extra,resident_threshold_mib=(fixed+reserve+factors+extra+codes)/2**20)
def execute(dim,keep=0,widths=(100,),split='tune',rep=1,tag='',reference=None,control=False):
 global CURRENT
 p=policy(dim);budget=538
 if control:p=dict(mode='resident',record=0);budget=640
 CURRENT=dict(dim=dim,keep=keep,mode=p['mode'])
 return r.execute(f'd{dim}_k{keep}{tag}',dict(mode=p['mode'],record=p['record'],pages=16,inflight=256),split,rep,reference=reference,widths=widths,budget=budget)
def setup():
 build=json.loads((OUT/'build.json').read_text());assert sha(r.s.BIN)==build['binary_sha256']
 for p,h in build['sources'].items():assert sha(p)==h,p
 for k in (128,256,512):
  folder=ASSETS/f'd{k}';a=json.loads((folder/'encoded.json').read_text())
  for p,h in a['files'].items():assert sha(folder/p)==h
 dump(OUT/'calibration/lock.json',json.loads((ROOT/'results/04_ours_memory_budget/gist_joint_optimization/calibration/lock.json').read_text()))
 protocol=dict(budget_mib=538,dimensions=[128,256,512,960],shortlists=[0,16,32,48],widths=[60,100,180],target_recall=0.95,workers=32,beam=1,warmup=100,validation_queries=100,test_queries=800,test_repeats=2,selection='fastest validation configuration satisfying target, separately per dimension',routing='PCA+native 1-bit approximate score; top-M fresh neighbors enter full4 verification; rejected nodes can be reconsidered from future expansions',hard_pruning='disabled for all lowdim route scores; original960 retains its production statistical gate',pca_tail_statistics_loaded=False,graph_and_full4_payload='original960 unchanged',reference_control='keep=0 verifies every fresh neighbor and must agree across dimensions',source_assets=str(ASSETS),script_hashes={p.name:sha(p)for p in Path(__file__).parent.iterdir()if p.is_file()})
 if (OUT/'protocol.json').exists():
  prior=json.loads((OUT/'protocol.json').read_text());assert all(prior[k]==v for k,v in protocol.items() if k!='script_hashes')
 else:dump(OUT/'protocol.json',protocol)
 dump(OUT/'memory_ledger.json',{str(k):policy(k)for k in (128,256,512,960)})
def main():
 setup();ref=None
 for k in (128,256,512):
  folder=execute(k,split='train',tag='_all_control',control=True,reference=ref)
  if ref is None:ref=folder
 execute(960,tag='_baseline',reference=ROOT/'results/04_ours_memory_budget/gist_route_locality/tune/bfs_i256_r1')
 dump(OUT/'smoke.json',dict(passed=True,lowdim_all_candidate_parity=True,original960_parity=True))
 candidates=[]
 for k in (960,128,256,512):
  options=[]
  for keep in ([0]if k==960 else[0,16,32,48]):
   folder=execute(k,keep,widths=(60,100,180),tag='_grid')
   for row in json.loads((folder/'result.json').read_text())['summary_rows']:options.append(dict(dim=k,keep=keep,width=row['search_width'],recall=row['recall'],qps=row['qps'],source=str(folder)))
  good=[x for x in options if x['recall']>=.95]
  if not good:
   best=max(options,key=lambda x:x['recall']);folder=execute(k,best['keep'],widths=(260,380,580),tag='_extended')
   for row in json.loads((folder/'result.json').read_text())['summary_rows']:
    if row['recall']>=.95:good.append(dict(dim=k,keep=best['keep'],width=row['search_width'],recall=row['recall'],qps=row['qps'],source=str(folder)))
  candidates.append(dict(eligible=True,**max(good,key=lambda x:x['qps']))if good else dict(dim=k,eligible=False))
 dump(OUT/'selection_lock.json',dict(target_recall=.95,candidates=candidates,binary_sha256=sha(r.s.BIN),selected_before_test=True,locked_at=time.time()))
 for rep in (1,2):
  for x in candidates if rep==1 else list(reversed(candidates)):
   if x['eligible']:execute(x['dim'],x['keep'],widths=(x['width'],),split='test',rep=rep,tag='_locked')
 dump(OUT/'state.json',dict(status='completed',updated=time.time()))
if __name__=='__main__':main()
