from pathlib import Path
import sys,json,os,hashlib,struct
root=Path.cwd();sys.path.insert(0,str(root/'src'))
from disk_bench.storage_precondition import prepare_search
from disk_bench.memory_runner import run_measured
out=root/'results/diagnostics/symphony_shared_cache_20260921_verified'
out.mkdir(exist_ok=False)
old=json.loads((root/'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/SymphonyQG-DiskPort/command.json').read_text())
f=dict(zip(old[1::2],old[2::2]));binary=root/'build/disk/native/qgraph05_symphonyqg_disk_port'
query=root/'artifacts/query_splits/gist/shared/validation_query.fvecs'; gt=query.with_name('validation_gt.ivecs')
dim=struct.unpack('<I',query.read_bytes()[:4])[0];n=query.stat().st_size//(4*(dim+1))
order=out/'order.u32';order.write_bytes(struct.pack('<'+'I'*n,*range(n)))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
f.update({'--phase':'validation','--query':str(query),'--groundtruth':str(gt),'--query-order':str(order),'--query-order-sha256':sha(order),'--query-split-sha256':sha(query),'--native-binary-sha256':sha(binary),'--search-dram-budget-gib':'4','--cache-mode':'standard','--integration-widths':'40,100,580','--warmup-queries':'100','--run-id':'symphony_shared_cache_validation','--workers':'32'})
cpus=sorted(os.sched_getaffinity(0))[:32]
for repeat,modes in enumerate([['off','auto'],['auto','off']]):
 for mode in modes:
  folder=out/f'r{repeat}_{mode}';folder.mkdir(exist_ok=False)
  flags=dict(f);flags.update({'--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),'--repeat-id':str(repeat)})
  if mode=='off':flags['--shared-page-cache-bytes']='0'
  cmd=[str(binary)]+[x for kv in flags.items() for x in kv]
  (folder/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
  print('precondition',folder.name,flush=True)
  prepare_search(cmd,folder/'storage.json')
  print('search',folder.name,flush=True)
  evidence=run_measured(cmd,evidence_path=folder/'resources.json',log_path=folder/'terminal.log',budget_bytes=4*2**30,rss_budget=True,cwd=root,env={**os.environ,'MALLOC_ARENA_MAX':'2','OMP_DYNAMIC':'FALSE'},cpu_affinity=cpus,timeout=900)
  print(folder.name,evidence.get('exit_code'),flush=True)
  assert (folder/'result.json').exists(),evidence
print('DONE',flush=True)
from pathlib import Path
import json
root=Path.cwd();out=root/'results/diagnostics/symphony_shared_cache_20260921_verified'
records={}
for rep in range(2):
 for mode in ['off','auto']:
  folder=out/f'r{rep}_{mode}'
  resource=json.loads((folder/'resources.json').read_text())
  assert resource['budget_admitted'] and resource['exit_code']==0
  result=json.loads((folder/'result.json').read_text())
  trace=[json.loads(x) for x in (folder/'queries.jsonl').read_text().splitlines()]
  records[rep,mode]=(result,trace,resource)
reference={(r['search_width'],r['query_id']):r for r in records[0,'off'][1]}
for _,trace,_ in records.values():
 assert len(trace)==len(reference)==600
 for r in trace:
  ref=reference[r['search_width'],r['query_id']]
  for key in ['result_ids','recall_at_10','visited_nodes','distance_evaluations']:
   assert r[key]==ref[key],(key,r['query_id'],r['search_width'])
rows=[]
for width in [40,100,580]:
 for mode in ['off','auto']:
  groups=[next(r for r in records[rep,mode][0]['summary_rows'] if r['search_width']==width) for rep in range(2)]
  trace=[r for rep in range(2) for r in records[rep,mode][1] if r['search_width']==width]
  n=sum(g['query_count'] for g in groups)
  rows.append(dict(width=width,mode=mode,recall=sum(r['recall_at_10'] for r in trace)/len(trace),qps=n/sum(g['query_count']/g['qps'] for g in groups),pages=sum(r['sectors_4k'] for r in trace)/len(trace),hits=sum(r['shared_cache_hits'] for r in trace),misses=sum(r['shared_cache_misses'] for r in trace),cache_mib=max(r['cache_bytes'] for r in trace)/2**20,peak_rss_gib=max(records[rep,mode][2]['observed_peak_rss_bytes'] for rep in range(2))/2**30))
(out/'comparison.json').write_text(json.dumps({'parity_passed':True,'trace_rows':2400,'rows':rows},indent=2)+'\n')
lines=['# SymphonyQG 跨查询页缓存验证','','GIST 原完整索引；200 条 validation 查询，32 workers，4 GiB 整进程 RSS 预算，100 条预热。两轮反序 off/auto、auto/off；每个进程依次 width 40/100/580，各 width 重建缓存。每组前顺序 O_DIRECT 预读全索引；不声称冷盘或设备缓存受控。CPU affinity 固定，未绑定 NUMA 内存，因此为诊断结果，不是正式性能准入。','','2400 条逐查询记录核验通过：有序结果 ID、Recall、访问节点数、距离计算数与关闭共享缓存一致。所有进程通过 4 GiB RSS 验收。QPS 按查询数/总时间合并两轮，无显著性结论。','','| width | 模式 | Recall@10 | QPS | 4 KiB 页/查询 | 共享缓存 MiB | 整进程峰值 GiB |','|---:|---|---:|---:|---:|---:|---:|']
for r in rows:lines.append(f"| {r['width']} | {r['mode']} | {r['recall']:.4f} | {r['qps']:.2f} | {r['pages']:.1f} | {r['cache_mib']:.1f} | {r['peak_rss_gib']:.3f} |")
lines+=['','合成测试：126 组官方参考对照、192 组缓存遍历对照、8 线程并发淘汰压力测试通过。小图充分热缓存下后续读取为零。','', '共享缓存固定预分配，额度含数据与元数据；整进程预算另保留查询缓存和工作区。单独参考路径的全数据正式准入仍待接入，formal_ready 保持 false。']
(out/'report.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(rows,indent=2))
