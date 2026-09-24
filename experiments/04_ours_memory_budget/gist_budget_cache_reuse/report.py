"""Summarize measured feasibility separately from conservative auto admission."""
import collections,csv,json,math,re
import run
s=run.s

def measured_data(out, checks):
 """Export native summary values; validate recall/count/I/O against query traces."""
 rows=[]
 for check in checks:
  folder=out/check['run'];plan=json.loads((folder/'memory_plan.json').read_text())
  mem=json.loads((folder/'memory_measurement.json').read_text())
  routing=json.loads((folder/'routing_stats.json').read_text());traces=s.pilot.traces(folder)
  for native in json.loads((folder/'result.json').read_text())['summary_rows']:
   width=native['search_width'];batch=[r for r in traces if r['search_width']==width]
   assert len(batch)==native['query_count']
   for summary,trace in [('recall','recall_at_10'),('sectors_4k_per_query','sectors_4k'),('bytes_read_per_query','bytes_read'),('io_requests_per_query','io_requests'),('visited_nodes','visited_nodes'),('distance_evaluations','distance_evaluations'),('db1_checks','db1_checks'),('db1_survivors','db1_survivors'),('rerank_candidates','rerank_candidates')]:
    assert math.isclose(native[summary],sum(r[trace] for r in batch)/len(batch),rel_tol=1e-9,abs_tol=1e-8),(folder,width,summary)
   rs=routing.get(str(width),{});cache=json.loads((folder/'memory_stats'/f'L{width}.json').read_text())
   rows.append(dict(run=check['run'],budget_mib=plan['budget_bytes']/s.MIB,mode=plan['mode'],L=width,queries=len(batch),recall=native['recall'],qps=native['qps'],p95_ms=native['latency_p95_us']/1000,p99_ms=native['latency_p99_us']/1000,total_pages_per_query=native['sectors_4k_per_query'],routing_pages_per_query=rs.get('reads',0)/len(batch),routing_pages=rs.get('reads',0),total_bytes_per_query=native['bytes_read_per_query'],routing_bytes=rs.get('bytes',0),io_requests_per_query=native['io_requests_per_query'],visited_nodes_per_query=native['visited_nodes'],distance_evaluations_per_query=native['distance_evaluations'],db1_checks_per_query=native['db1_checks'],db1_survivors_per_query=native['db1_survivors'],rerank_candidates_per_query=native['rerank_candidates'],process_VmPeak_mib=mem['sampled_high_water_bytes']['VmPeak']/s.MIB,process_VmHWM_mib=mem['sampled_high_water_bytes']['VmHWM']/s.MIB,kernel_peak_RSS_mib=mem['kernel_wait4_peak_rss_bytes']/s.MIB,routing_capacity_pages=plan['routing_capacity_pages'],routing_peak_inflight=rs.get('peak_active',0),record_cache_reserved_bytes=cache['cache_reserved_bytes'],static_records=cache['payload_nodes'],dynamic_capacity_nodes=(cache.get('dynamic_records') or {}).get('capacity_nodes',0),timing_scope='observed_with_concurrent_work_not_isolated_benchmark',result_source=str(folder.relative_to(out)/'result.json'),trace_source=str(folder.relative_to(out)/'queries.jsonl')))
 s.dump(out/'measured_results.json',rows)
 with (out/'measured_results.csv').open('w',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 s.dump(out/'validation/measured_results_audit.json',dict(passed=True,rows=len(rows),queries=sum(r['queries'] for r in rows),native_summaries_matched_traces=True,timing_values_copied_from_native_summary=True))
 lines=['','## 实际搜索数据','',
  '以下是本轮实际输出，不是估计值。每行 100 条测量查询，每个 L 之前预热 100 条；数据来自 validation 集、单轮运行。**QPS 和 P95/P99 受其他并发工作影响，保留观测值，但不能据此比较方法快慢或宣称加速。** 256 MiB 未完成查询，没有有效搜索性能行，不填 QPS=0。',
  '', '[完整 CSV](measured_results.csv) · [完整 JSON](measured_results.json) · [汇总与逐查询核对](validation/measured_results_audit.json)。CSV 另含访问、剪枝、精算、字节、缓存容量和内存峰值；内存峰值是整个进程运行的峰值，重复出现在各 L 行，不是该 L 的独立峰值。',
  '', '总读页包含精细记录和 routing 的物理读取；共享 routing 读取只计一次，再除以该 L 的查询数。页大小为 4 KiB，预热 I/O 不计入下表。P95/P99 直接使用原生程序输出，未跨轮混合或重新估算。']
 for calibration in [False,True]:
  lines+=['','### '+('校准运行（2 GiB；显式缓存额度）' if calibration else '目标预算运行'),'', '| 配置（链接到原始结果） | L | Recall@10 | QPS（观测） | P95 ms | P99 ms | 总读页/查询 | routing 读页/查询 |','|---|---:|---:|---:|---:|---:|---:|---:|']
  subset=[r for r in rows if r['run'].startswith('calibration/')==calibration]
  for r in sorted(subset,key=lambda r:(r['budget_mib'],r['run'],r['L'])):
   label=f"{r['budget_mib']:g} MiB / {r['mode']}"
   if r['run'].startswith('diagnostics/'):label+=' / 64页诊断'
   elif r['run'].startswith('auto_checks/'):label+=' / auto'
   else:
    label+=' / '+({'resident':'无记录缓存','factors':'分页额度1 MiB','paged':'分页额度1 MiB','hot_dynamic':'记录缓存额度64 MiB'}[r['mode']])
   lines.append(f"| [{label}]({r['result_source']}) | {r['L']} | {r['recall']:.4f} | {r['qps']:.2f} | {r['p95_ms']:.2f} | {r['p99_ms']:.2f} | {r['total_pages_per_query']:.2f} | {r['routing_pages_per_query']:.2f} |")
 import plot
 plot.main()
 return ['','## 实测曲线','','![预算、Recall 与实测 QPS](figures/budget_curves.png)','','a：Recall–QPS；圆、三角、方块分别是 L100、L300、L580。相同 Recall 的不同 L 点保留并沿 L 顺序连接，不合并。b：固定 L 的预算–QPS；实线为 auto，虚线为固定 64 页的全分页诊断，两类配置不混连。两图纵轴均为对数刻度。256 MiB 无有效 QPS，在独立状态行标记；384 MiB 的灰三角表示 auto 拒绝，其显式分页结果仍正常绘制。640 MiB 的 hot_dynamic 动态容量为 0。','','**这些是单轮、100 条 validation 查询的观测值，存在并发运行影响，不能作为独占设备的加速结论；没有重复实验误差条。**','','[主图 PNG](figures/budget_curves.png) · [PDF](figures/budget_curves.pdf) · [SVG](figures/budget_curves.svg)','','校准组采用相同 2 GiB 进程上限，但显式缓存额度不同，单独展示，不能把差异归因于预算大小。','','![校准组实测曲线](figures/calibration_curves.png)','','[校准图 PDF](figures/calibration_curves.pdf) · [SVG](figures/calibration_curves.svg)']+lines

def main():
 out=s.OUT;checks=[];stages=[]
 for log in sorted(out.rglob('terminal.log')):
  width=None
  for line in log.read_text().splitlines():
   match=re.search(r'05C Ours-Disk search:.* width=(\d+)',line)
   if match:width=int(match[1])
   if line.startswith('memory_stage '):
    name=re.search(r'name=(\w+)',line)[1]
    values={key:int(value)*1024 for key,value in re.findall(r'(VmPeak|VmSize|VmRSS):\s+(\d+) kB',line)}
    stages.append(dict(run=str(log.parent.relative_to(out)),stage=name,width=width,bytes=values))
 s.dump(out/'validation/stage_measurements.json',stages)
 old=s.ROOT/'results/04_ours_memory_budget/gist_budget_sweep/calibration/resident'
 reference={(r['search_width'],r['query_id']):r for r in s.pilot.traces(old)}
 for path in sorted(out.rglob('acceptance.json')):
  folder=path.parent;a=json.loads(path.read_text());assert a['binary_sha256']==s.sha(s.BIN)
  for name,h in a['files'].items():assert s.sha(folder/name)==h,(folder,name)
  totals=collections.defaultdict(collections.Counter);keys=set()
  for r in s.pilot.traces(folder):
   key=(r['search_width'],r['query_id']);assert key not in keys;keys.add(key)
   for f in s.FIELDS:assert r[f]==reference[key][f],(folder,key,f)
   for f in ['io_requests','sectors_4k','bytes_read']:totals[key[0]][f]+=r[f]
  routing=json.loads((folder/'routing_stats.json').read_text())
  for width,actual in totals.items():
   ops=json.loads((folder/'memory_stats'/f'L{width}.json').read_text())['operations'];rs=routing.get(str(width),{})
   expected={f:sum(v[op] for v in ops.values())+rs.get(rk,0) for f,op,rk in [('io_requests','io_requests','reads'),('sectors_4k','read_pages','reads'),('bytes_read','read_bytes','bytes')]}
   assert dict(actual)==expected,(folder,width,actual,expected)
  checks.append(dict(run=str(folder.relative_to(out)),queries=len(keys),exact_old_version_parity=True,physical_io_passed=True))
 s.dump(out/'validation/parity_io_audit.json',dict(passed=True,checks=checks))
 config=json.loads((out/'calibration/lock.json').read_text())
 lines=['# GIST 查询缓存复用：低预算复测','', '**历史验证记录：本页保留查询缓存复用实验的原始数据。当前主实验采用整进程预算，分别扣除fixed、factors和reserve后判断codes常驻与缓存容量，见 [整进程自动策略报告](../gist_memory_auto_policy/report.md)。codes独立额度实验仅为辅助对照，不替代整进程准入判断。**','',f"运行状态：{json.loads((out/'state.json').read_text())['status']}。二进制 SHA256：`{s.sha(s.BIN)}`。",'',f"固定开销估计：{config['fixed_bytes']/s.MIB:.2f} MiB；routing 常驻准入阈值：{config['resident_threshold_bytes']/s.MIB:.2f} MiB。沿用 8 MiB 完整查询集余量和 64 MiB 安全余量。",'', '| 预算 MiB | 保守 auto 预检 | 全分页诊断（64页） | 诊断 VmPeak / RSS MiB | auto 实测 |','|---:|---|---|---:|---|']
 for mib in run.BUDGETS:
  folder=out/'diagnostics'/f'low_budget_{mib}';p=json.loads((folder/'probe.json').read_text());pre=out/'preflight'/str(mib)
  status=json.loads((pre/'status.json').read_text())['status'];plan=json.loads((pre/'memory_plan.json').read_text()) if (pre/'memory_plan.json').exists() else {}
  mp=folder/'memory_measurement.json';m=json.loads(mp.read_text()) if mp.exists() else {};peak=m.get('sampled_high_water_bytes',{})
  auto=out/'auto_checks'/f'budget_{mib}';passed=(auto/'acceptance.json').exists()
  mem_text=f"{peak['VmPeak']/s.MIB:.2f} / {peak.get('VmHWM',0)/s.MIB:.2f}" if 'VmPeak' in peak else '—'
  label={'passed_three_widths_exact_parity':'通过，逐查询一致','not_run_auto_branch_already_verified':'未重复执行（auto 已通过）','memory_or_thread_resource_failure':'失败：内存/线程资源','setup_failure_cause_not_exposed':'初始化失败，底层原因未暴露','timeout':'超时'}.get(p['status'],p['status'])
  selected=plan.get('mode','预检拒绝' if status=='admission_rejected' else status)
  lines.append(f"| {mib} | {selected} | {label} | {mem_text} | {'通过' if passed else '预检拒绝，未运行'} |")
 from threshold_explanation import section
 lines+=section(out)
 lines+=['','每个通过的组：validation 集前 100 条、100 条预热、L100/300/580、beam1、workers32、RLIMIT_AS soft=hard。与旧版本已保存的校准参考逐查询 ID、recall、访问、剪枝、精算计数一致，物理 I/O 核对见 `validation/parity_io_audit.json`。','','诊断绕过估算准入，不代表已按 auto 准入。本轮使用 validation 集进行校准与功能检查，未执行独立 test 集的 800 查询/40 档 L 完整验收。本轮与旧版实验及部分验证任务重叠，下方保留 QPS 观测值，但不将其作为独占设备的性能比较。低预算失败日志原样保留；RSS 为 VmHWM 高水位，VmPeak 为进程报告的虚拟地址峰值。']
 old_config=json.loads((s.ROOT/'results/04_ours_memory_budget/gist_budget_sweep/calibration/lock.json').read_text())
 minimum=config['fixed_bytes']+10*s.MIB+2703360+4352
 lines += ['',f'估算最小全分页准入 {minimum/s.MIB:.3f} MiB；factors 常驻最小准入 {(minimum+20000000)/s.MIB:.3f} MiB。低于预检阈值的诊断采用显式模式检查可行性，未删除安全余量或修改自动准入。']
 lines+=['','## 新旧校准对比','',f"旧版本固定开销估计 {old_config['fixed_bytes']/s.MIB:.2f} MiB，常驻阈值 {old_config['resident_threshold_bytes']/s.MIB:.2f} MiB；新版本对应为 {config['fixed_bytes']/s.MIB:.2f} 和 {config['resident_threshold_bytes']/s.MIB:.2f} MiB。这是保守准入阈值，不是实测最低运行内存。",'', '| 校准分支 | 旧 VmPeak MiB | 新 VmPeak MiB | 新 RSS MiB |','|---|---:|---:|---:|']
 old_observations={r['mode']:r for r in old_config['observations']}
 for row in config['observations']:
  m=json.loads((out/'calibration'/row['mode']/'memory_measurement.json').read_text())
  lines.append(f"| {row['mode']} | {old_observations[row['mode']]['peak']/s.MIB:.2f} | {row['peak']/s.MIB:.2f} | {m['sampled_high_water_bytes'].get('VmHWM',0)/s.MIB:.2f} |")
 lines+=['','## 自动分支实测','', '| 预算 MiB | 模式 | routing 页容量 | 精细记录缓存额度 MiB | VmPeak / RSS MiB |','|---:|---|---:|---:|---:|']
 for a in sorted((out/'auto_checks').glob('*/acceptance.json')):
  p=json.loads((a.parent/'memory_plan.json').read_text());m=json.loads((a.parent/'memory_measurement.json').read_text())['sampled_high_water_bytes']
  lines.append(f"| {p['budget_bytes']/s.MIB:.0f} | {p['mode']} | {p['routing_capacity_pages']} | {p['optional_cache_bytes']/s.MIB:.2f} | {m['VmPeak']/s.MIB:.2f} / {m.get('VmHWM',0)/s.MIB:.2f} |")
 stats640=json.loads((out/'auto_checks/budget_640/memory_stats/L100.json').read_text())
 lines+=['',f"640 MiB 的静态优先策略实际缓存 {stats640['payload_nodes']:,} 条记录，动态容量为 {(stats640.get('dynamic_records') or {}).get('capacity_nodes',0)}；hot_dynamic 名称不代表本档动态容量非零。640 MiB 已通过自动分支，因此未重复执行 64 页显式全分页诊断（not_run，不是失败）。512 MiB 的补充小缓存诊断在调整执行顺序前已启动，结果按实际情况保留。"]
 lines+=measured_data(out,checks)
 lines+=['','## 实现与验收证据','','- `prepare.py`：独立复制并修改依赖；旧二进制与索引未改。逐行差异见 `validation/implementation.patch`。','- 每个 worker 内共享一份缓存；查询间在 mutex 保护下清空索引、链表和计数，保留底层页数组。','- `validation/cache_reset.log`：100 次清空/淘汰/重填；clear 无分配、无陈旧命中、数据一致。','- `validation/sharing_identity.json`：初始化 Arc 相同，100 次重置后原生指针/容量保持不变；不同 worker 不共用缓存。','- `validation/unit_tests.json`：5 项 Rust 回归，覆盖缓存淘汰/命名空间、超缓存大小批次、分页与常驻评分以及预算边界。',f"- `validation/parity_io_audit.json`：{len(checks)} 组、{sum(r['queries'] for r in checks)} 条测量查询与旧校准参考逐项一致；请求/页/字节物理 I/O 计账通过。",'- `validation/stage_measurements.json`：启动、codec/映射加载、缓存配置、各 L 预热后的 VmSize/VmPeak/RSS 观测；分配类别未精确归因，不将估算预留当作实测占用。','- `validation/old_version_unchanged.json`：旧二进制、生成源码及冻结依赖散列未变。','- `build.json`：二进制、生成源码和 C++ 依赖哈希；每组 acceptance.json 固定原始日志、命令、内存和逐查询输出哈希。','','## 限制与失败处理','','每个配置只运行一次本轮验证（512 MiB 包括 auto 和已启动的全分页补充诊断两个配置），没有失败后调小线程、静默换模式或覆盖日志。256 MiB 的运行失败不能证明分页算法本身的内存下限；384 MiB 显式全分页虽通过，本轮 auto 预检仍拒绝；640 等通过不能代替原 800 查询、40 档 L、两轮性能实验。所有预算都限制整个进程的地址空间，不仅是 routing 数据或 RSS。并发运行会影响时序与分配峰值，本次报告只据此报告观测值和通过/失败状态，不承诺性能改善。']
 (out/'report.md').write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
if __name__=='__main__':main()
