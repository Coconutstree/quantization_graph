"""Report actual data-budget runs, never relabel process-budget history."""
import collections,csv,json,statistics
from pathlib import Path
import run
s=run.s;OUT=s.OUT

def main():
 exp=json.loads((OUT/'experiment.json').read_text());rows=[];checks=[]
 reference={(r['search_width'],r['query_id']):r for r in s.pilot.traces(OUT/'runs/resident_reference_r1')}
 for a in sorted((OUT/'runs').glob('*/acceptance.json')):
  folder=a.parent;accept=json.loads(a.read_text());assert accept['binary_sha256']==s.sha(s.BIN)
  for name,digest in accept['files'].items():assert s.sha(folder/name)==digest,(folder,name)
  p=json.loads((folder/'memory_plan.json').read_text());m=json.loads((folder/'memory_measurement.json').read_text());routing=json.loads((folder/'routing_stats.json').read_text())
  assert m['hard_limit_verified'] and m['user_address_space_budget_passed']
  data_account=run.account(folder)
  traces=s.pilot.traces(folder);totals=collections.defaultdict(collections.Counter);keys=set()
  for r in traces:
   key=(r['search_width'],r['query_id']);assert key not in keys;keys.add(key)
   for field in s.FIELDS:assert r[field]==reference[key][field],(folder,key,field)
   for field in ['io_requests','sectors_4k','bytes_read']:totals[key[0]][field]+=r[field]
  for r in json.loads((folder/'result.json').read_text())['summary_rows']:
   width=r['search_width'];cache=json.loads((folder/'memory_stats'/f'L{width}.json').read_text());ops=cache['operations'];rs=routing.get(str(width),{})
   expected={f:sum(v[op] for v in ops.values())+rs.get(rk,0) for f,op,rk in [('io_requests','io_requests','reads'),('sectors_4k','read_pages','reads'),('bytes_read','read_bytes','bytes')]}
   assert dict(totals[width])==expected
   rows.append(dict(run=folder.name,case=folder.name.rsplit('_r',1)[0],repeat=int(folder.name[-1]),data_budget_bytes=p['data_cache_budget_bytes'],data_budget_mib=p['data_cache_budget_bytes']/run.MIB,process_limit_mib=p['budget_bytes']/run.MIB,mode=p['mode'],L=width,queries=r['query_count'],recall=r['recall'],qps=r['qps'],p95_ms=r['latency_p95_us']/1000,p99_ms=r['latency_p99_us']/1000,pages_per_query=r['sectors_4k_per_query'],routing_reads=rs.get('reads',0),routing_bytes=rs.get('bytes',0),routing_capacity_pages=p['routing_capacity_pages'],routing_peak_inflight=rs.get('peak_active',0),cache_reserved_bytes=cache['cache_reserved_bytes'],static_records=cache['payload_nodes'],dynamic_capacity=(cache.get('dynamic_records') or {}).get('capacity_nodes',0),VmPeak_mib=m['sampled_high_water_bytes']['VmPeak']/run.MIB,RSS_mib=m['kernel_wait4_peak_rss_bytes']/run.MIB,source=str(folder.relative_to(OUT)/'result.json')))
  checks.append(dict(run=folder.name,queries=len(keys),physical_io_passed=True,cross_round_exact_parity=True,**data_account))
 s.dump(OUT/'validation/data_and_io_audit.json',dict(passed=True,groups=checks))
 s.dump(OUT/'measured_results.json',rows)
 if rows:
  with (OUT/'measured_results.csv').open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 lines=['# GIST：按 routing／缓存数据预算分档','', '**中间口径：本页为codes+factors共享额度，不是单独codes容量。新的 [codes容量实验](../gist_codes_budget/report.md) 将factors在额度外计账，本页仅作历史记录。**','',
 '**已修正控制变量：横轴是数据／缓存预算 D，不是整个进程的地址空间上限。旧的 562.14 MiB 整进程准入估算不再用于选择分页或常驻。**','',
 f"codes={exp['codes_bytes']:,} 字节（{exp['codes_bytes']/run.MIB:.6f} MiB）；factors={exp['factors_bytes']:,} 字节（{exp['factors_bytes']/run.MIB:.6f} MiB）。本实验的完整 routing 阈值 **T=C+F={exp['resident_data_threshold_bytes']:,} 字节（{exp['resident_data_threshold_bytes']/run.MIB:.6f} MiB）**。codes 与 factors 分列，未把 codes 单独大小与完整 routing 大小混称。",'',
 '注意：128 MiB 虽大于纯 codes 的122.07 MiB，但本实验D还要容纳19.07 MiB factors，因此仍不足以容纳完整routing。这是明确的数据组成计算，不是再次扣除线程/运行开销。','',
 'D 包括常驻 codes/factors、routing 页缓存（含页元数据）及精细记录缓存（含其元数据）。centroid、每线程查询缓存/visited/栈、评分 scratch、routing 服务等在 D 外另行计账。所有组整进程 RLIMIT_AS soft=hard 固定为 2 GiB，只用于运行保护，不决定数据策略。继承的421 MiB非数据安全包络只用于预计进程峰值验收，并不是本实验横轴，也不是为1-bit本身预留的容量。','',
 '```text\nD >= C+F：routing 全量常驻；剩余 D−C−F 给记录缓存\nD < C+F 且 D >= F+一页槽：factors 常驻，剩余 D−F 给 codes 页缓存\nD < F+一页槽：全分页，D 给 routing 页缓存\n数据最小容量不足或独立进程安全检查不足：分别报错，不静默换策略\n```','',
 '预算规划从实际 sidecar 文件头读取 C/F，不用写死的148 MB构造边界。当前入口和记录缓存布局仍限于GIST/32workers；换数据集需要读取新布局、校准非数据开销，不能复用GIST固定预留。','',
 '独立 test 集原顺序前100条，beam1、32 workers、L100/300/580；每个 L 预热100条，两轮预算顺序反转。每轮独立常驻参考；结果ID、recall、访问/剪枝/精算逐查询完全一致才验收。O_DIRECT预读沿用原协议，不宣称冷设备缓存。此轮按单一runner顺序执行，旧整进程实验已结束；不与旧validation集或并发时的QPS直接比较。','',
 '| 档位 | 数据预算字节 | MiB | 模式 | routing 页槽 | 记录缓存 MiB | 静态记录 / 动态容量 |', '|---|---:|---:|---|---:|---:|---:|']
 for label,data in exp['cases']:
  p=json.loads((OUT/'preflight'/label/'memory_plan.json').read_text());accepted=[r for r in rows if r['case']==label];c=accepted[0] if accepted else None
  lines.append(f"| {label} | {data:,} | {data/run.MIB:.9f} | {p['mode']} | {p['routing_capacity_pages']} | {p['optional_cache_bytes']/run.MIB:.3f} | {str(c['static_records'])+' / '+str(c['dynamic_capacity']) if c else '待测'} |")
 lines+=['','T−1B 与 T 相差一个字节，但储存模式不同；不能将两者四舍五入到相同预算后合并。图中的离散档位不是等间距的内存轴。记录缓存按静态优先，spare不足以建缓存时可为零，dynamic也允许为零。','',f"状态：{json.loads((OUT/'state.json').read_text())['status']}；已验收 {len(checks)} 组、{sum(c['queries'] for c in checks)} 条测量查询。[全部逐轮数据 CSV](measured_results.csv) · [JSON](measured_results.json) · [数据容量及物理 I/O 审计](validation/data_and_io_audit.json)。",'']
 if len(checks)==16:
  import plot
  plot.main()
  lines+=['## 修正后的实测图','','![数据预算曲线](figures/data_budget_curves.png)','','a 为 Recall–QPS，b 为固定 L 的预算档位–QPS。QPS取两轮等查询数的调和均值；误差范围为两轮最小–最大，不是置信区间。b 使用离散档位轴保留相差1字节的边界。主图只展示本轮新测数据，不混入旧整进程结果。','','[PNG](figures/data_budget_curves.png) · [PDF](figures/data_budget_curves.pdf) · [SVG](figures/data_budget_curves.svg)','']
 lines+=['## 逐轮实测数据','','P95/P99不跨轮平均；VmPeak/RSS是整个运行的峰值，各L行重复展示该进程峰值。两轮配置实际都受相同2GiB进程上限保护。','', '| 档位 / 轮 | L | Recall | QPS | P95 ms | P99 ms | 页/查询 | routing读页 | VmPeak / RSS MiB |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
 for r in rows:lines.append(f"| [{r['run']}]({r['source']}) | {r['L']} | {r['recall']:.4f} | {r['qps']:.2f} | {r['p95_ms']:.2f} | {r['p99_ms']:.2f} | {r['pages_per_query']:.2f} | {r['routing_reads']} | {r['VmPeak_mib']:.2f} / {r['RSS_mib']:.2f} |")
 lines+=['','## 验证范围','','原位清空／共享缓存沿用已验证版本；新边界测试见 [data_boundaries.log](validation/data_boundaries.log)：数据T±1字节、最小全分页、factors切换、记录缓存启动，以及更改整进程上限不改变数据策略。新版本没有复用旧预算性能点，也未改写旧二进制/索引。这里是100测试查询的两轮验证，不宣称完成800查询、40档L的完整性能实验。']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n')
 print(len(checks),'groups',OUT/'report.md')
if __name__=='__main__':main()
