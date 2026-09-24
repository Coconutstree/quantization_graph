"""Same-budget GIST trials; hotspots use independent validation queries only."""
import argparse,importlib.util,json,math,struct,time
from pathlib import Path
from build import ROOT,WORK,sha,build

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
prev=module('optimized_pilot',ROOT/'experiments/04_ours_memory_budget/routing_paged_optimized/run.py')
pilot=prev.pilot
OUT=ROOT/'results/04_ours_memory_budget/routing_factors_hot'
pilot.OUT=OUT;pilot.WORK=WORK
BINARY=WORK/'ours_routing_factors_hot'
FIELDS=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates','full4_page_reads','rerank_page_reads']
def dump(p,x):pilot.dump(p,x)
def progress(s):print(time.strftime('%F %T'),s,flush=True)
def budget(memory,fraction):
 return 2*1024**3 if not fraction else memory['nonrouting_bytes']+memory['paging_overhead_bytes']+math.floor(math.ceil((memory['routing_bytes']+24)/4096)*fraction/100)*4352

def execute(base,memory,mode,fraction,rep,widths=[100],training=False):
 label='profile_validation' if training else f'{mode}_p{fraction}_r{rep}'+('_regression' if widths!=[100] else '')
 folder=OUT/'gist/runs'/label
 if (folder/'acceptance.json').exists():
  a=json.loads((folder/'acceptance.json').read_text());assert a['binary_sha256']==sha(BINARY)
  for name,h in a['files'].items():assert sha(folder/name)==h
  return folder
 if folder.exists():raise RuntimeError('inspect unaccepted run: '+str(folder))
 folder.mkdir(parents=True)
 f=base.copy();limit=budget(memory,fraction)
 f.update({'--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),'--run-id':'native_integration_factors_hot',
 '--repeat-id':str(rep),'--workers':'32','--warmup-queries':'0' if training else '100','--search-dram-budget-gib':repr(limit/1024**3),
 '--integration-widths':','.join(map(str,widths)),'--integration-beams':'1','--parity-mode':'external','--native-binary-sha256':sha(BINARY),
 '--implementation-fingerprint':'factors-hot-'+mode,'--routing-other-reservation-bytes':str(memory['other_reservation_bytes']),
 '--routing-safety-reservation-bytes':str(memory['safety_reservation_bytes']),'--routing-max-inflight-pages':'64',
 '--routing-cache-policy':'clock','--routing-factors':'resident' if mode in ['factors','hot'] else 'paged'})
 if training:f['--routing-profile-output']=str(folder/'page_counts.u64')
 if mode=='hot':f['--routing-hot-pages']=str(OUT/f'gist/inputs/hot_p{fraction}.u64')
 command=[str(BINARY)]+[v for k,x in f.items() for v in [k,x]]
 dump(folder/'budget.json',dict(**memory,budget_bytes=limit,mode=mode,fraction=fraction,training=training))
 progress(label);pilot.precondition(command,folder);measured=pilot.measurement.measure(command,folder,budget=limit,timeout=1200);assert measured["user_address_space_budget_passed"]
 data=pilot.traces(folder);assert len(data)==100*len(widths)
 st=prev.stats(folder)
 if mode=='resident':assert not st
 else:
  for s in st.values():assert s['peak_active']<=s['inflight']==64 and s['peak_pages']<=s['capacity']
 if not training and mode!='resident':
  ref=OUT/'gist/runs'/('resident_p0_r'+str(rep)+('_regression' if widths!=[100] else ''))
  reference={(r['search_width'],r['query_id']):r for r in pilot.traces(ref)}
  for r in data:
   for key in FIELDS:assert r[key]==reference[r['search_width'],r['query_id']][key],(label,key,r['query_id'])
  for w,s in st.items():
   for key,counter in [('sectors_4k','reads'),('bytes_read','bytes'),('io_requests','reads')]:
    assert sum(r[key]-reference[r['search_width'],r['query_id']][key] for r in data if r['search_width']==w)==s[counter]
 dump(folder/'routing_stats.json',st)
 dump(folder/'acceptance.json',dict(passed=True,training=training,queries=len(data),fields=[] if training else FIELDS,binary_sha256=sha(BINARY),files={p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!='acceptance.json'}))
 return folder

def prepare_hot(base,memory):
 folder=OUT/'gist/inputs';f=base.copy()
 source=ROOT/'artifacts/query_splits/gist/shared'
 def subset(path):
  raw=path.read_bytes();stride=(struct.unpack_from('<I',raw)[0]+1)*4;assert len(raw)>=100*stride
  return raw[:100*stride],stride
 q,qs=subset(source/'validation_query.fvecs');g,_=subset(source/'validation_gt.ivecs')
 test=Path(base['--query']).read_bytes();assert not set(q[i:i+qs] for i in range(0,len(q),qs)) & set(test[i:i+qs] for i in range(0,len(test),qs))
 for name,data in [('validation_query.fvecs',q),('validation_gt.ivecs',g)]:
  p=folder/name
  if p.exists():assert p.read_bytes()==data
  else:p.write_bytes(data)
 f['--query']=str(folder/'validation_query.fvecs');f['--groundtruth']=str(folder/'validation_gt.ivecs');f['--query-split-sha256']=sha(f['--query'])
 training=execute(f,memory,'factors',75,0,training=True)
 raw=(training/'page_counts.u64').read_bytes();counts=struct.unpack('<'+'Q'*(len(raw)//8),raw)
 head=(Path(base['--disk-index-dir'])/'ours_db1_sidecar.bin').read_bytes()[:24]
 _,codes,factors=struct.unpack('<8sQQ',head)
 choices={}
 for fraction in [25,75]:
  slots=(budget(memory,fraction)-memory['nonrouting_bytes']-memory['paging_overhead_bytes']-factors)//4352
  n=max(1,slots//10)
  chosen=sorted(sorted(range(len(counts)),key=lambda i:(-counts[i],i))[:n])
  target=folder/f'hot_p{fraction}.u64';data=struct.pack('<'+'Q'*n,*chosen)
  if target.exists():assert target.read_bytes()==data
  else:target.write_bytes(data)
  choices[fraction]=dict(hot_pages=n,training_request_coverage=sum(counts[i] for i in chosen)/sum(counts),sha256=sha(target))
 dump(folder/'hot_protocol.json',dict(training_queries=100,source='validation split first 100',test_overlap=0,hot_fraction_of_cache=.1,selection='descending validation page requests, tie by page ID',counts_sha256=sha(training/'page_counts.u64'),codes_bytes=codes,factors_bytes=factors,choices=choices))

def main():
 p=argparse.ArgumentParser();p.add_argument('--build',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--regression',action='store_true');a=p.parse_args()
 if a.build:build()
 if a.run or a.regression:
  manifest=json.loads((WORK/'build.json').read_text());assert sha(BINARY)==manifest['binary_sha256']
  for path,h in manifest['source_sha256'].items():assert sha(ROOT/path)==h,path
  dump(OUT/'build.json',manifest)
  f,memory=pilot.inputs('gist');prepare_hot(f,memory)
  if a.run:
   for rep in [1,2]:
    execute(f,memory,'resident',0,rep)
    order=[(fraction,mode) for fraction in [25,75] for mode in ['clock','factors','hot']]
    if rep==2:order.reverse()
    for fraction,mode in order:execute(f,memory,mode,fraction,rep)
  if a.regression:
   execute(f,memory,'resident',0,1,[300,580])
   for fraction in [25,75]:
    for mode in ['clock','factors','hot']:execute(f,memory,mode,fraction,1,[300,580])
  progress('All requested runs accepted.')
if __name__=='__main__':main()
