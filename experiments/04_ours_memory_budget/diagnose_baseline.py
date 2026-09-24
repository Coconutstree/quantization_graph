"""Isolated ABBA diagnosis; L100 only, original query/order/warmup/budget retained."""
import json,os,subprocess,sys,threading,time
from pathlib import Path
from protocol import ROOT,OUT,REFERENCE,flags,sha,LIMIT
from run import load_measure,read_trace
DEST=ROOT/'results/04_ours_memory_budget/baseline_diagnosis_20260919'
OLD=ROOT/'results/diagnostics/03_disk_system/runtime_test_L_400_w_32/agnews_acceptance_20260912/binaries/qgraph05_shared_graph_port'
NEW=ROOT/'work/ours_memory_budget/v2/bin/ours_memory_budget_v2'

def monitor(stop,path):
 with path.open('w') as f:
  while not stop.is_set():
   row={'time':time.time()}
   for name in ['diskstats','stat','loadavg','pressure/io','pressure/cpu']:
    row[name]=Path('/proc',name).read_text()
   children=Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children').read_text().split()
   row['children']={}
   for pid in children:
    try:row['children'][pid]={k:Path('/proc',pid,k).read_text() for k in ['stat','status','io','cgroup']}
    except (FileNotFoundError,ProcessLookupError):pass
   f.write(json.dumps(row)+'\n');f.flush();stop.wait(1)

def main():
 DEST.mkdir(exist_ok=False)
 assert sha(OLD)=='a64dfc64e5f351aa48e5d929d8413143935aa0b6079046402d3a04a2f826c92c'
 original=flags(json.loads((REFERENCE/'command.json').read_text()))
 current=flags(json.loads((OUT/'baseline/command.json').read_text()))
 resolved=json.loads((OUT/'verification.json').read_text())['resolved_paths']
 (DEST/'diagnostic_protocol.json').write_text(json.dumps({'sequence':['old','new','new','old'],'widths':[100],'not_formal_results':True,'old_sha256':sha(OLD),'new_sha256':sha(NEW),'affinity':sorted(os.sched_getaffinity(0)),'original_widths_replaced_only_for_diagnosis':True},indent=2))
 summaries=[]
 for i,mode in enumerate(['old','new','new','old']):
  d=DEST/f'{i+1}_{mode}';d.mkdir();(d/'memory_stats').mkdir()
  args=original.copy() if mode=='old' else current.copy();args.update(resolved)
  binary=OLD if mode=='old' else NEW
  args.update({'--integration-widths':'100','--result-json':str(d/'result.json'),'--query-trace':str(d/'queries.jsonl'),'--run-id':'native_integration_baseline_diagnosis','--native-binary-sha256':sha(binary)})
  if mode=='new':args['--memory-stats-dir']=str(d/'memory_stats')
  command=[str(binary)]+[x for k,v in args.items() for x in (k,v)]
  (DEST/'state.json').write_text(json.dumps({'status':'running','current':d.name,'pid':os.getpid()}))
  print('START',d.name,flush=True)
  stop=threading.Event();thread=threading.Thread(target=monitor,args=(stop,d/'host_samples.jsonl'));thread.start()
  try:mem=load_measure().measure(command,d,budget=LIMIT,timeout=900)
  finally:stop.set();thread.join()
  result=json.loads((d/'result.json').read_text());r=result['summary_rows'][0]
  actual=read_trace(d/'queries.jsonl');ref={k:v for k,v in read_trace(REFERENCE/'queries.jsonl').items()if k[0]==100}
  assert actual.keys()==ref.keys()
  fields=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','sectors_4k','io_requests']
  differences=[(key,k)for key in actual for k in fields if actual[key][k]!=ref[key][k]]
  (d/'parity.json').write_text(json.dumps({'passed':not differences,'compared_fields':fields,'differences':differences[:20],'count':len(differences)},indent=2))
  assert not differences
  summaries.append({'run':d.name,**r,'VmPeak':mem['sampled_high_water_bytes']['VmPeak']})
  (DEST/'summary.json').write_text(json.dumps(summaries,indent=2))
  print('DONE',d.name,'QPS',r['qps'],'IO_US',r['io_wait_us'],flush=True)
 (DEST/'state.json').write_text(json.dumps({'status':'completed','pid':os.getpid()}))
if __name__=='__main__':main()
