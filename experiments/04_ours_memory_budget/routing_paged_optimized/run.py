"""Same-budget L100 pilot: resident, v1, batched LRU and batched CLOCK.
100 held-out test queries, warmup100, workers32, two interleaved rounds.
"""
import argparse,importlib.util,json,math,sys,time
from pathlib import Path
from build import ROOT,WORK,build,sha
OLD=ROOT/'experiments/04_ours_memory_budget/routing_paged'
spec=importlib.util.spec_from_file_location('previous_pilot',OLD/'run.py')
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)
OUT=ROOT/'results/04_ours_memory_budget/routing_paged_optimized';pilot.OUT=OUT;pilot.WORK=WORK
WIDTHS=[100];MIB=1024**2
V1=WORK/'before/ours_routing_paged_v1';V2=WORK/'ours_routing_paged_optimized'

def dump(path,data):pilot.dump(path,data)
def progress(message):
 print(time.strftime('%F %T'),message,flush=True)
 (ROOT/'ours_routing_paged_optimized_tests.md').write_text('# Routing 分页批量化优化\n\n'+message+'\n\n结果：`results/04_ours_memory_budget/routing_paged_optimized/`\n\n100 条独立测试查询，L100、beam1、workers32、100 预热、两轮；比较同期 v1、优化 LRU、优化 CLOCK，另有常驻对照。分页各组使用完全相同的预算与在途上限。\n')

def stats(folder):
 samples={}
 for line in (folder/'terminal.log').read_text().splitlines():
  if line.startswith('routing_stats '):
   r=dict(x.split('=',1) for x in line.split()[1:]);key=(int(r.pop('L')),r.pop('phase'));samples[key]={k:int(v) for k,v in r.items()}
 result={}
 for width,phase in samples:
  if phase!='measurement_end':continue
  end=samples[width,phase];begin=samples[width,'measurement_start']
  result[width]={k:end[k]-begin[k] for k in end if k not in ['peak_active','peak_pages','capacity','inflight','reserved_bytes']}
  result[width].update({k:end[k] for k in ['peak_active','peak_pages','capacity','inflight','reserved_bytes']})
 return result

def verify(folder,reference,mode,budget,widths):
 data=pilot.traces(folder);ref=pilot.traces(reference)
 ref={(r['search_width'],r['query_id']):r for r in ref};assert len(data)==100*len(widths)
 fields=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates','full4_page_reads','rerank_page_reads']
 for row in data:
  expected=ref[row['search_width'],row['query_id']]
  for key in fields:assert row[key]==expected[key],(key,row['query_id'])
 st=stats(folder)
 if mode=='resident':assert not st
 else:
  assert set(st)==set(widths)
  for w,s in st.items():
   assert s['peak_active']<=s['inflight']==64 and s['peak_pages']<=s['capacity']
   for key,counter in [('sectors_4k','reads'),('bytes_read','bytes'),('io_requests','reads')]:
    actual=sum(row[key] for row in data if row['search_width']==w)
    expected=sum(row[key] for (ww,_),row in ref.items() if ww==w)
    assert actual-expected==s[counter],(key,actual,expected,s[counter])
 mem=json.loads((folder/'memory_measurement.json').read_text());assert mem['user_address_space_budget_passed']
 dump(folder/'routing_stats.json',st)
 dump(folder/'acceptance.json',dict(passed=True,mode=mode,budget=budget,queries=len(data),fields=fields,reference=str(reference),binary_sha256=sha(V1 if mode=='v1' else V2),files={p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!='acceptance.json'}))

def run_group(dataset,base,memory,mode,fraction,round_id,widths=[100],suffix=''):
 label=f'{mode}_p{fraction}_r{round_id}{suffix}' if mode!='resident' else f'resident_r{round_id}{suffix}'
 folder=OUT/dataset/'runs'/label;reference=OUT/dataset/'runs'/f'resident_r{round_id}{suffix}'
 binary=V1 if mode=='v1' else V2
 if (folder/'acceptance.json').exists():
  record=json.loads((folder/'acceptance.json').read_text());assert record['binary_sha256']==sha(binary)
  for name,digest in record['files'].items():assert sha(folder/name)==digest
  return
 if folder.exists():raise RuntimeError('inspect unaccepted run before retry: '+str(folder))
 folder.mkdir(parents=True);f=base.copy()
 if mode=='resident':budget=2*1024**3
 else:
  capacity=math.floor(math.ceil((memory['routing_bytes']+24)/4096)*fraction/100)
  budget=memory['nonrouting_bytes']+memory['paging_overhead_bytes']+capacity*4352
 f.update({'--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),
  '--run-id':'native_integration_routing_optimized','--repeat-id':str(round_id),
  '--workers':'32','--warmup-queries':'100','--search-dram-budget-gib':repr(budget/1024**3),
  '--integration-widths':','.join(map(str,widths)),'--integration-beams':'1','--parity-mode':'external',
  '--native-binary-sha256':sha(binary),'--implementation-fingerprint':'routing-optimized-'+mode,
  '--routing-other-reservation-bytes':str(memory['other_reservation_bytes']),
  '--routing-safety-reservation-bytes':str(memory['safety_reservation_bytes']),
  '--routing-max-inflight-pages':'64'})
 if mode in ['lru','clock']:f['--routing-cache-policy']=mode
 command=[str(binary)]+[v for k,x in f.items() for v in [k,x]]
 dump(folder/'budget.json',dict(**memory,budget_bytes=budget,mode=mode,fraction=fraction))
 progress(dataset+'/'+label);pilot.precondition(command,folder)
 pilot.measurement.measure(command,folder,budget=budget,timeout=1200)
 verify(folder,reference,mode,budget,widths);summarize()

def summarize():
 rows=[]
 for dataset in ['gist','agnews']:
  for folder in sorted((OUT/dataset/'runs').glob('*')):
   if not (folder/'acceptance.json').exists():continue
   a=json.loads((folder/'acceptance.json').read_text());b=json.loads((folder/'budget.json').read_text());st=json.loads((folder/'routing_stats.json').read_text());mem=json.loads((folder/'memory_measurement.json').read_text())
   for r in json.loads((folder/'result.json').read_text())['summary_rows']:
    rows.append(dict(dataset=dataset,run=folder.name,mode=b['mode'],fraction=b['fraction'],L=r['search_width'],qps=r['qps'],mean_us=r['latency_mean_us'],p95_us=r['latency_p95_us'],p99_us=r['latency_p99_us'],recall=r['recall'],VmPeak=mem['sampled_high_water_bytes']['VmPeak'],RSS=mem['kernel_wait4_peak_rss_bytes'],budget=b['budget_bytes'],routing=st.get(str(r['search_width']),{})))
 dump(OUT/'summary.json',rows)

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build',action='store_true');parser.add_argument('--run',action='store_true');parser.add_argument('--regression',action='store_true');args=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
 if args.build:build()
 if args.run or args.regression:
  manifest=json.loads((WORK/'build.json').read_text());assert sha(V2)==manifest['binary_sha256']
  for path,digest in manifest['source_sha256'].items():assert sha(ROOT/path)==digest,'source drift '+path
  dump(OUT/'build.json',manifest)
  for dataset in ['gist','agnews']:
   f,memory=pilot.inputs(dataset);dump(OUT/dataset/'protocol.json',dict(**memory,query_count=100,workers=32,warmup=100,old_binary_sha256=sha(V1),new_binary_sha256=sha(V2)))
   if args.run:
    for rep in [1,2]:
     run_group(dataset,f,memory,'resident',0,rep)
     choices=[(25,'v1'),(25,'lru'),(25,'clock'),(75,'v1'),(75,'lru'),(75,'clock')]
     if rep==2:choices.reverse()
     for fraction,mode in choices:run_group(dataset,f,memory,mode,fraction,rep)
   if args.regression:
    run_group(dataset,f,memory,'resident',0,1,[300,580],'_regression')
    for mode in ['lru','clock']:
     for fraction in [25,75]:run_group(dataset,f,memory,mode,fraction,1,[300,580],'_regression')
  progress('全部请求的试跑与验收完成。');summarize()
 else:print('Use --build --run; --regression adds L300/580 parity runs.')
if __name__=='__main__':main()
