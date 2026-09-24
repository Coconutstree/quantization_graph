"""Bounded L100 diagnosis; does not launch the formal 40-width test800 suite."""
import hashlib, importlib.util, json, os, signal, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[2]
OUT=ROOT/'results/04_ours_memory_budget/gist_diskann_protocol_audit'
sys.path.insert(0,str(HERE.parent/'gist_width40_test100'))
import measurement
sys.path.insert(0,str(ROOT/'src/disk_bench'))
from storage_precondition import prepare_search

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2)+'\n')
def interrupted(sig,frame):raise KeyboardInterrupt()

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 for sig in [signal.SIGTERM,signal.SIGINT,signal.SIGHUP]:signal.signal(sig,interrupted)
 base=json.loads((ROOT/'results/04_ours_memory_budget/gist_width40_test100/runs/diskann_c0_r1/command.json').read_text());flags=dict(zip(base[1::2],base[2::2]))
 old=ROOT/'results/diagnostics/03_disk_system/runtime_test_L_400_w_32/agnews_acceptance_20260912/binaries/qgraph05_diskann_port';new=Path(base[0])
 assert sha(old)=='daff1bfb8e7fce43fd5c4f8ca2651d7e21661efa927dce846e97f1ba88fcb77b'
 assert sha(new)=='001e4ae19b16f4fbbb8ed958b98f5b47fb8d97b166223467976dab59db18704e'
 cases=[('old100_cap_no_pre',old,100,True,False),('new100_cap_no_pre',new,100,True,False),('new100_uncap_no_pre',new,100,False,False),('new100_cap_pre',new,100,True,True),('new800_cap_pre',new,800,True,True),('old800_cap_no_pre',old,800,True,False)]
 dump(OUT/'protocol.json',dict(widths=[100],workers=32,beam=1,warmup=100,cache='c0',formal_suite=False,cases=[dict(name=n,binary_sha256=sha(b),queries=q,cap_2g=c,sequential_pre_read=p) for n,b,q,c,p in cases],limitations=['Single observations, serial order; device cache and time drift uncontrolled.','No extra pre-read does not imply cold storage; preceding runs may affect later runs.','800-query case is a single-width diagnostic, not formal 40-width results.']))
 build=2388331;paused=False;summary=json.loads((OUT/'summary.json').read_text()) if (OUT/'summary.json').exists() else []
 try:
  cmdfile=Path(f'/proc/{build}/cmdline')
  if cmdfile.exists():
   assert b'msmarco_graph_build_20260915/run_diskann_fair' in cmdfile.read_bytes()
   os.kill(build,signal.SIGSTOP);paused=True
   dump(OUT/'build_suspension.json',dict(pid=build,paused_unix=time.time(),restored=False))
  for name,binary,count,cap,pre in cases:
   folder=OUT/name
   if (folder/'acceptance.json').exists():
    assert json.loads((folder/'acceptance.json').read_text())['passed'];continue
   if folder.exists():raise RuntimeError('preserve existing evidence: '+str(folder))
   folder.mkdir()
   f=dict(flags);f.update({'--integration-widths':'100','--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),'--native-binary-sha256':sha(binary),'--run-id':'native_integration_gist_diskann_protocol_audit'})
   if count==800:
    f['--query']=str(ROOT/'artifacts/query_splits/gist/shared/test_query.fvecs');f['--groundtruth']=str(ROOT/'artifacts/query_splits/gist/shared/test_gt.ivecs');f['--query-order']=str(ROOT/'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/optimized_order.u32')
    pair=hashlib.sha256()
    for key in ['--query','--groundtruth']:
     pair.update(Path(f[key]).name.encode());pair.update(bytes.fromhex(sha(f[key])))
    assert pair.hexdigest()=='9b46438f0c6472734127bfa9cd1af70b6f2fde91c76fb5ea353f515c3e1c3354'
    assert sha(f['--query-order'])=='e8d41290fb3fbc73ee4d94966388ecfecac9c9de4b6fc3834c4ac7d37671e0f6'
    f['--query-split-sha256']=pair.hexdigest();f['--query-order-sha256']=sha(f['--query-order'])
   command=[str(binary)]+[v for pair in f.items() for v in pair]
   dump(OUT/'state.json',dict(status='running',case=name,pid=os.getpid()));print(name,flush=True)
   if pre:prepare_search(command,folder/'storage_precondition.json')
   else:dump(folder/'storage_precondition.json',dict(protocol='fixed_query_warmup_only',extra_pre_read=False,cold_claim=False))
   mem=measurement.measure(command,folder,budget=2*1024**3 if cap else None,timeout=900)
   result=json.loads((folder/'result.json').read_text());r=result['summary_rows'][0];trace=list(map(json.loads,(folder/'queries.jsonl').open()))
   assert len(result['summary_rows'])==1 and r['search_width']==100 and r['query_count']==len(trace)==count
   assert abs(sum(t['recall_at_10'] for t in trace)/count-r['recall'])<1e-9
   assert abs(sum(t['sectors_4k'] for t in trace)/count-r['sectors_4k_per_query'])<1e-7
   dump(folder/'acceptance.json',dict(passed=True,count=count))
   summary.append(dict(case=name,queries=count,recall=r['recall'],qps=r['qps'],io_wait_ms=r['io_wait_us']/1000,latency_mean_ms=r['latency_mean_us']/1000,pages=r['sectors_4k_per_query'],peak_rss_mib=mem['kernel_wait4_peak_rss_bytes']/2**20))
   dump(OUT/'summary.json',summary)
  dump(OUT/'state.json',dict(status='completed'))
 except BaseException as e:
  dump(OUT/'state.json',dict(status='failed',error=repr(e)));raise
 finally:
  if paused:
   os.kill(build,signal.SIGCONT);dump(OUT/'build_suspension.json',dict(pid=build,restored=True,restored_unix=time.time()))

if __name__=='__main__':main()
