"""Three methods, fixed M=32; validation locks width before independent test."""
import argparse
import importlib.util
import json
import mmap
import os
import time
from pathlib import Path
from prepare import ROOT,HERE,WORK,OUT,ASSETS,BIN,build,dump,sha

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
r=module('residual_joint',HERE.parent/'gist_joint_optimization/run.py')
policy=module('residual_memory',HERE.parent/'gist_adaptive_resident/policy.py')
r.OUT=OUT;r.WORK=WORK;r.s.OUT=OUT;r.s.WORK=WORK;r.s.BIN=BIN
r.s.PROFILE=ROOT/'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'
base_inputs=r.inputs;base_precondition=r.s.pilot.precondition
MIB=2**20;CURRENT={};VARIANTS=('pca','pca_residual','full1bit')

def inputs(split):
 f=base_inputs(split)
 f['--implementation-fingerprint']='pca-residual-three-way-M32-empirical-gate-common-full4'
 f['--pca-route-keep']='32'
 f['--comparison-gate']='1'if CURRENT['gate']else'0'
 if CURRENT['variant']!='full1bit':
  f['--pca-route-dir']=str(ASSETS/f'd{CURRENT["dimension"]}')
  f['--pca-tail-mode']='norm'if CURRENT['variant']=='pca_residual'else'none'
 else:
  f['--disk-index-dir']=str(ROOT/'work/ours_memory_budget/gist_route_locality/index')
  f['--routing-code-layout']='bfs'
 return f
r.inputs=inputs

def precondition(cmd,folder):
 base_precondition(cmd,folder)
 f=dict(zip(cmd[1::2],cmd[2::2]));d=f.get('--pca-route-dir')
 if not d:return
 names=['sidecar.bin']+(['residual.bin']if f['--pca-tail-mode']=='norm'else[])
 records=[]
 for name in names:
  p=Path(d)/name;offset=0;fd=os.open(p,os.O_RDONLY|os.O_DIRECT)
  try:
   with mmap.mmap(-1,4*MIB)as buffer:
    while offset<p.stat().st_size:
     n=os.preadv(fd,[buffer],offset);assert n>0;offset+=n
  finally:os.close(fd)
  records.append(dict(path=str(p),bytes=offset))
 dump(folder/'pca_precondition.json',dict(files=records,io='O_DIRECT',included_in_timing=False,device_cache_controlled=False))
r.s.pilot.precondition=precondition

def plan(variant):
 if variant=='full1bit':return dict(dimension=960,mode='paged',record=0,residual_bytes=0)
 residual=4_000_000 if variant=='pca_residual'else 0
 p=policy.calibrated_plan(538,certificate_bytes=residual)
 return dict(dimension=p['dimension'],mode='resident',record=p['record_cache_bytes']//MIB,residual_bytes=residual,ledger=p)

def execute(variant,widths=(100,),split='tune',rep=1,tag='',gate=True,cache=None,reference=None):
 global CURRENT
 p=plan(variant);CURRENT=dict(variant=variant,gate=gate,**p)
 record=p['record']if cache is None else cache
 name=f'{variant}{tag}'
 folder=r.execute(name,dict(mode=p['mode'],record=record,pages=16,inflight=256),split,rep,widths=widths,budget=538,reference=reference)
 mem=json.loads((folder/'memory_plan.json').read_text())
 assert mem['pca_residual_norm_bytes']==p['residual_bytes']
 assert mem['optional_cache_bytes']==record*MIB
 if variant!='full1bit':
  assert not mem['routing_capacity_pages']
  expected=p['ledger']['resident_required_bytes']+record*MIB
  assert mem['admission_bytes']==expected
 dump(folder/'variant.json',dict(variant=variant,M=32,gate=gate,cache_mib=record,**p))
 return folder

def setup():
 build()
 dump(OUT/'calibration/lock.json',json.loads((ROOT/'results/04_ours_memory_budget/gist_adaptive_resident/calibration/lock.json').read_text()))
 for k in {plan(v)['dimension']for v in VARIANTS if v!='full1bit'}:
  directory=ASSETS/f'd{k}'
  for name,h in json.loads((directory/'encoded.json').read_text())['files'].items():assert sha(directory/name)==h
 protocol=dict(budget_mib=538,M=32,variants=list(VARIANTS),widths=[40,60,100,180],extension_widths=[260,380],
  target_recall=.95,workers=32,beam=1,validation_queries=100,test_queries=800,test_repeats=2,
  gate='native lowdim 1-bit estimate minus native error (epsilon=1.9), clamp to zero, plus optional PCA residual norm-difference squared',
  threshold='approximate original full4 pool worst score; empirical gate, NOT certified safe pruning',
  rank='epsilon=0 score, same top32 rule in every method; residual added only after ranking',
  full4='RECOMPUTE_FULL in all three methods; no INT8/lowdim MSB inner-product reuse',
  baseline='original-dimensional 1-bit, padded1024, BFS paged; same M32/ranking/gate/full4 protocol, not the untouched production baseline',
  residual='4-byte squared residual norm per base point; query norm from centered norm minus projected norm; no PCA error subtraction',
  memory={v:plan(v)for v in VARIANTS}, selection='fastest validation width with Recall>=.95 separately per variant',
  controls='train100 gate-off no-residual/residual at identical cache8; tune100 width100 identical cache8 to isolate residual from cache cost',
  test_order=[list(VARIANTS),list(reversed(VARIANTS))])
 p=OUT/'protocol.json'
 if p.exists():assert json.loads(p.read_text())==protocol
 else:dump(p,protocol)
 return protocol

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
 protocol=setup()
 a=execute('pca',split='train',tag='_gateoff_cache8',gate=False,cache=8)
 execute('pca_residual',split='train',tag='_gateoff_cache8',gate=False,cache=8,reference=a)
 dump(OUT/'smoke.json',dict(passed=True,residual_does_not_change_ranking_or_full4_when_gate_disabled=True))
 if args.smoke:return
 candidates=[]
 for variant in VARIANTS:
  folder=execute(variant,widths=protocol['widths'],tag='_grid')
  options=[dict(width=x['search_width'],recall=x['recall'],qps=x['qps'],source=str(folder))for x in json.loads((folder/'result.json').read_text())['summary_rows']]
  good=[x for x in options if x['recall']>=protocol['target_recall']]
  if not good:
   folder=execute(variant,widths=protocol['extension_widths'],tag='_extended')
   options += [dict(width=x['search_width'],recall=x['recall'],qps=x['qps'],source=str(folder))for x in json.loads((folder/'result.json').read_text())['summary_rows']]
   good=[x for x in options if x['recall']>=protocol['target_recall']]
  candidates.append(dict(variant=variant,selected=max(good,key=lambda x:x['qps'])if good else None,options=options))
 lock=OUT/'selection_lock.json';value=dict(candidates=candidates,target_recall=protocol['target_recall'],selected_before_test=True,binary_sha256=sha(BIN))
 if lock.exists():
  prior=json.loads(lock.read_text());assert all(prior[k]==v for k,v in value.items())
 else:dump(lock,dict(**value,locked_at=time.time()))
 # This extra run isolates the residual term without reducing the no-residual cache.
 execute('pca',tag='_cache8_control',cache=8)
 for rep in (1,2):
  for candidate in candidates if rep==1 else list(reversed(candidates)):
   x=candidate['selected']
   if x:execute(candidate['variant'],widths=(x['width'],),split='test',rep=rep,tag='_locked')
 dump(OUT/'state.json',dict(status='completed',all_variants_eligible=all(x['selected']is not None for x in candidates),finished=time.time()))

if __name__=='__main__':main()
