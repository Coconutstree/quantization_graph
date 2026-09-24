"""Codes-only capacity, factors and runtime memory reported separately."""
import collections,csv,json
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
  accounting=run.account(folder);traces=s.pilot.traces(folder);totals=collections.defaultdict(collections.Counter);keys=set()
  for r in traces:
   key=(r['search_width'],r['query_id']);assert key not in keys;keys.add(key)
   for field in s.FIELDS:assert r[field]==reference[key][field],(folder,key,field)
   for field in ['io_requests','sectors_4k','bytes_read']:totals[key[0]][field]+=r[field]
  for r in json.loads((folder/'result.json').read_text())['summary_rows']:
   width=r['search_width'];cache=json.loads((folder/'memory_stats'/f'L{width}.json').read_text());ops=cache['operations'];rs=routing.get(str(width),{})
   expected={f:sum(v[op] for v in ops.values())+rs.get(rk,0) for f,op,rk in [('io_requests','io_requests','reads'),('sectors_4k','read_pages','reads'),('bytes_read','read_bytes','bytes')]};assert dict(totals[width])==expected
   rows.append(dict(run=folder.name,case=folder.name.rsplit('_r',1)[0],repeat=int(folder.name[-1]),codes_budget_bytes=p['codes_cache_budget_bytes'],codes_budget_mib=p['codes_cache_budget_bytes']/run.MIB,process_limit_mib=p['budget_bytes']/run.MIB,mode=p['mode'],L=width,queries=r['query_count'],recall=r['recall'],qps=r['qps'],p95_ms=r['latency_p95_us']/1000,p99_ms=r['latency_p99_us']/1000,pages_per_query=r['sectors_4k_per_query'],routing_reads=rs.get('reads',0),routing_bytes=rs.get('bytes',0),routing_capacity_pages=p['routing_capacity_pages'],routing_peak_inflight=rs.get('peak_active',0),factors_outside_bytes=p['factors_bytes'],routing_page_metadata_outside_bytes=p['routing_page_metadata_outside_budget_bytes'],paging_scratch_outside_bytes=p['paging_scratch_bytes'],cache_reserved_bytes=cache['cache_reserved_bytes'],static_records=cache['payload_nodes'],dynamic_capacity=(cache.get('dynamic_records') or {}).get('capacity_nodes',0),VmPeak_mib=m['sampled_high_water_bytes']['VmPeak']/run.MIB,RSS_mib=m['kernel_wait4_peak_rss_bytes']/run.MIB,source=str(folder.relative_to(OUT)/'result.json')))
  checks.append(dict(run=folder.name,queries=len(keys),physical_io_passed=True,cross_round_exact_parity=True,**accounting))
 s.dump(OUT/'validation/codes_and_io_audit.json',dict(passed=True,groups=checks));s.dump(OUT/'measured_results.json',rows)
 if rows:
  with (OUT/'measured_results.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 C=exp['codes_bytes'];F=exp['factors_bytes'];MiB=run.MIB
 lines=['# GIST：codes 容量实验（factors 在额度外）','',
 '**本轮只用 codes 的大小判断“1-bit 编码能否全部放进 RAM”，不再把 factors 或整个进程开销扣进 codes 容量。factors 始终驻留，在额度外列账。**','',
 f"全量 codes **C={C:,} 字节＝{C/MiB:.7f} MiB**；factors **F={F:,} 字节＝{F/MiB:.6f} MiB**。本轮边界 T=C，而不是 C+F，也不是旧的整进程562.14 MiB准入估算。",'',
 '## “评分”是什么，实际还需要什么','',
 '当前代码中的 routing 评分，是对 fresh 候选计算近似内积、距离下界等量，再决定是否有必要读取候选的更多数据。它不是只做一次二进制码比较，也不是最终结果的精确距离。', '',
 '```text\n查询向量 → 查询预处理（当前配置为INT8粗查询路径）\n图邻接表 + visited → fresh候选ID\n预处理查询 + 候选1-bit codes + factors → 内积估计/距离下界\n下界与候选池当前阈值比较 → 剪枝\n幸存候选 → 读取compact 4-bit记录 → 进一步距离计算 → 更新候选池\n最终候选 → residual数据 → 重排\n```','',
 '| 数据或状态 | 实际用途 | 是否占codes额度B |','|---|---|---|',
 '| 全量1-bit codes，或分页时codes页数据缓冲 | 候选的压缩编码，用于粗粒度距离/内积计算 | 是 |',
 '| factors | 候选范数、缩放/误差相关参数、有效标记；恢复估计与剪枝界 | **否，始终常驻，F单列** |',
 '| 原始查询及预处理查询 | 查询变换/量化结果、查询范数等；每次查询需要 | 否，工作区单列 |',
 '| centroid及量化/变换模型参数 | 构造一致的查询/数据表示；当前GIST centroid为3840字节 | 否，基础进程内存计账 |',
 '| 图邻接表及映射 | 找到下一批候选；不属于codes本身 | 否；图主体在SSD，读取缓冲/映射另计 |',
 '| visited、fresh、候选池、frontier | 去重、搜索顺序和当前剪枝阈值 | 否，查询工作区 |',
 '| 查询页缓存、routing服务、页元数据、评分scratch、线程栈 | I/O共享/缓冲、批量评分及并发运行 | 否，运行开销 |',
 '| compact 4-bit／residual记录 | 幸存候选的后续距离计算与重排 | 主体在SSD；只有B−C的可选记录缓存占剩余额度 |','',
 'factors全量常驻是本轮固定的存储策略，不是算法要求它们必须始终全量常驻；评分时对应参数必须可用，也可以通过分页获得。本轮固定factors在codes额度外，目的是隔离codes容量变化的影响。codes额度128 MiB不等于整个进程只有128 MiB。','',
 'factors提取代码中的字段包括 norm_sqr、data_norm、cross_scale、error_cross_scale、valid；routing输出结构含 lower_bound、short_ip、alpha、ip_hat、error_bound、valid。剪枝比较的是 lower_bound 与候选池尾部距离，并受 epsilon 参数及有效标记控制。不能把“评分需要这些数据”解释成“它们必须和codes共用预算”。', '',
 '实现位置：快照 `native/ours_port.rs` 的 prepare、paper_estimates、distances 与 search_ours_disk_graph；依赖 `space_rabitq.h` 的 prepare_query、extract_paper_prune_sidecar 和对应距离估计内核。预算修改不改变这些计算内核和候选顺序。','',
 '## 现在如何分配和判断','',
 'codes容量从sidecar文件头读取，并与节点数×实际codes stride及文件长度交叉校验。当前GIST是1,000,000条×128字节＝128,000,000字节；960维理论位串是120字节，但现有实现按128字节stride存储，必须按实际分配量计算。换数据集时重新读取N和stride，不能沿用122.0703125 MiB这个数值。factors单独按其实际长度计账，不加到C里。','',
 '```text\nB < C：factors在B外常驻；codes分页\n       codes页数据槽数 = floor(B / 4096)\n       页元数据、服务、scratch也在B外\nB = C：codes恰好全驻留；factors另计；没有可选记录缓存\nB > C：codes全驻留；B−C给hot_dynamic记录缓存\n       余量不足以建立记录缓存时保留为空，dynamic容量允许为零\n```','',
 'B在分页时包含codes页内的对齐空间，但**不包含256字节/页的cache元数据**。在常驻时，B−C按现有完整记录缓存分配器计账，其记录索引元数据也必须放进这份剩余额度；不会偷偷借用factors额度扩展缓存。','',
 f'例如128 MiB额度：codes占{C/MiB:.6f} MiB，剩余 **{128-C/MiB:.6f} MiB** 可给记录缓存；factors的{F/MiB:.6f} MiB在B外。现在128 MiB不会再因扣除factors而被判为codes放不下。','',
 '整进程保护独立执行：本轮每组RLIMIT_AS soft=hard=2 GiB。预计进程占用＝继承的421 MiB基础安全包络＋F＋实际codes/记录缓存分配＋分页元数据/服务/scratch（若分页）。421 MiB是同缓存实现的经验预留，包含centroid，不是codes容量、不是实测最低进程内存。factors和其他开销不是免费内存，均记录在最终VmPeak/RSS中。进程保护不足就单独报错，不能据此改变“codes能否放下”的判断。','',
 '## 实验与预算表','',
 '原BFS索引，beam1、32workers，独立test集原顺序前100条，L100/300/580，每L预热100条，两轮反转预算顺序。每轮同查询集常驻参考；热点排名仍来自原validation profile，不使用test训练。统一O_DIRECT预读，不宣称冷设备缓存。当前仍为GIST专用入口，换数据集需读取真实codes大小、核实布局和单独校准其他内存。','',
 '| 档位 | B字节 | B MiB | codes模式 | 页数据槽 | 记录缓存MiB | F额外MiB | 页元数据额外MiB |','|---|---:|---:|---|---:|---:|---:|---:|']
 for label,budget in exp['cases']:
  p=json.loads((OUT/'preflight'/label/'memory_plan.json').read_text())
  lines.append(f"| {label} | {budget:,} | {budget/MiB:.9f} | {'分页' if p['mode']=='factors' else p['mode']} | {p['routing_capacity_pages']} | {p['optional_cache_bytes']/MiB:.3f} | {F/MiB:.3f} | {p['routing_page_metadata_outside_budget_bytes']/MiB:.3f} |")
 lines+=['',f"状态：{json.loads((OUT/'state.json').read_text())['status']}；已验收{len(checks)}组、{sum(c['queries'] for c in checks)}条测量查询。[完整CSV](measured_results.csv) · [JSON](measured_results.json) · [容量/逐查询/物理I/O审计](validation/codes_and_io_audit.json)。",'']
 if len(checks)==16:
  import plot
  plot.main()
  lines+=['## 实测曲线','','![codes额度曲线](figures/codes_budget_curves.png)','','a：Recall–QPS；b：固定L的codes容量档位–QPS。b的离散档位不是等间隔内存刻度，保留相差1字节的T−1B和T。QPS是两轮等查询数调和均值，范围为两轮min–max，不是置信区间。只使用本轮新数据。','','[PNG](figures/codes_budget_curves.png) · [PDF](figures/codes_budget_curves.pdf) · [SVG](figures/codes_budget_curves.svg)','']
 lines+=['## 逐轮实测数据','','P95/P99按轮保留。VmPeak/RSS是整个运行的峰值，不是各L的独立内存峰值。','', '| 档位 / 轮 | L | Recall | QPS | P95 ms | P99 ms | 总页/查询 | codes读页 | VmPeak / RSS MiB | 静态记录 / 动态容量 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
 for r in rows:lines.append(f"| [{r['run']}]({r['source']}) | {r['L']} | {r['recall']:.4f} | {r['qps']:.2f} | {r['p95_ms']:.2f} | {r['p99_ms']:.2f} | {r['pages_per_query']:.2f} | {r['routing_reads']} | {r['VmPeak_mib']:.2f} / {r['RSS_mib']:.2f} | {r['static_records']} / {r['dynamic_capacity']} |")
 lines+=['','## 验证范围','','[边界测试](validation/codes_boundaries.log)覆盖C±1字节、最小页数据容量、factors大小变化不改变codes阈值、进程上限变化不改变数据判断，以及记录缓存启动。实际逐查询结果ID/recall/访问/剪枝/精算与常驻参考一致；codes容量、全局在途上限和物理I/O计账单独检查。旧二进制/索引/历史结果未改，未把旧图改标签当作新结果。本轮为100测试查询两轮，不声称完成800查询/40档L的完整实验。']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n');print(len(checks),'groups',OUT/'report.md')
if __name__=='__main__':main()
