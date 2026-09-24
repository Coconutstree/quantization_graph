"""Only accepted new measurements appear as performance observations."""
import csv,json,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'results/04_ours_memory_budget/gist_memory_auto_policy';MIB=2**20

def main(render=True):
 OUT.mkdir(parents=True,exist_ok=True)
 for split in ['pilot','full']:
  if (OUT/split/'completed.json').exists() and not (OUT/'validation'/f'{split}_audit.json').exists():
   spec=importlib.util.spec_from_file_location("auto_policy_audit",Path(__file__).with_name("audit.py"));audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
   audit.main(split)
 def read(p,default=None):return json.loads(p.read_text()) if p.exists() else default
 lock=read(OUT/'calibration/lock.json',{});exp=read(OUT/'experiment.json',{});state=read(OUT/'state.json',{})
 rows=[];groups=[]
 for split in ['pilot','full']:
  for a in sorted((OUT/split).glob('*/acceptance.json')):
   folder=a.parent;accept=read(a);assert accept['passed'] and accept['whole_process_accounting'] and accept['physical_io']
   p=read(folder/'memory_plan.json');m=read(folder/'memory_measurement.json');routing=read(folder/'routing_stats.json')
   groups.append(dict(split=split,run=folder.name,queries=accept['queries']))
   for r in read(folder/'result.json')['summary_rows']:
    w=r['search_width'];cache=read(folder/'memory_stats'/f'L{w}.json');rs=routing.get(str(w),{})
    rows.append(dict(split=split,run=folder.name,reference=folder.name.startswith('resident_reference'),budget_mib=p['budget_bytes']/MIB,mode=p['mode'],repeat=int(folder.name[-1]),L=w,queries=r['query_count'],recall=r['recall'],qps=r['qps'],p95_ms=r['latency_p95_us']/1000,p99_ms=r['latency_p99_us']/1000,pages_per_query=r['sectors_4k_per_query'],routing_reads=rs.get('reads',0),routing_bytes=rs.get('bytes',0),routing_peak_pages=rs.get('peak_pages',0),routing_peak_inflight=rs.get('peak_active',0),routing_capacity_pages=p['routing_capacity_pages'],fixed_mib=p['fixed_bytes']/MIB,factors_mib=p['factors_bytes']/MIB,reserve_mib=p['reserve_bytes']/MIB,cache_reserved_mib=cache['cache_reserved_bytes']/MIB,static_records=cache['payload_nodes'],dynamic_capacity=(cache.get('dynamic_records') or {}).get('capacity_nodes',0),VmPeak_mib=m['sampled_high_water_bytes']['VmPeak']/MIB,RSS_mib=m['kernel_wait4_peak_rss_bytes']/MIB,source=str((folder/'result.json').relative_to(OUT))))
 (OUT/'measured_results.json').write_text(json.dumps(rows,indent=2)+'\n')
 if rows:
  with (OUT/'measured_results.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 lines=['# GIST 整进程预算自动策略实验','',f"状态：**{state.get('status','初始化')}**；阶段：`{state.get('stage','—')}`。",'',
 '**本次仅完成两轮test100，未启动800查询／40档L完整实验；完整实验自动执行已禁用。**','',
 '每个配置只在加载前计算一次：`usable = budget − fixed − factors − reserve`。factors始终常驻；fixed与reserve始终分开。codes放不下时分页，恰好放下或余量不足建缓存时仅常驻，余量足够时分给hot_dynamic。','',
 f"当前锁定 fixed：**{lock.get('fixed_bytes',357*MIB)/MIB:.0f} MiB**，是基础及工作负载开销的校准估算，包含8 MiB完整查询集预留；reserve：**{lock.get('reserve_bytes',64*MIB)/MIB:.0f} MiB**，禁止分配的安全余量。factors按实际文件计账，GIST为19.073486 MiB。centroid已在fixed中，不重复加。",'',
 '全量codes为1,000,000×128字节＝122.0703125 MiB。分配计划的expected_peak不包含reserve，admission=expected_peak+reserve。运行验收要求VmPeak不超过expected_peak，不能使用reserve掩盖计划低估。分页页数据与元数据合计4352字节/槽；在途读取使用同一槽缓冲，不重复计账；服务开销10 MiB及评分scratch另列。','',
 '## 阈值与预算','',
 'T₁：在factors常驻、基础开销和安全余量已经扣除后，能够建立最小codes分页服务所需的整进程预算；不是启动阈值或实测最低预算。T₂：完整codes常驻预算。T₃：能够建立记录缓存的预算，包含记录索引及至少一条记录，不等于产生显著收益。','',
 '| 阈值 | 字节 | MiB |','|---|---:|---:|']
 if (OUT/'revision.json').exists():
  lines[2:2]=['**校准版本v2：** v1精确常驻边界运行正常退出，但VmPeak=558.426 MiB超过不含安全余量的计划498.144 MiB，已停止后续批次并保留[失败证据](archive/v1/validation/resident_exact/failure.json)。当前版本将该峰值纳入fixed重新校准，reserve仍单列64 MiB，不沿用旧阈值。','']
 for k,v in exp.get('thresholds',{}).items():lines.append(f'| {k} | {v:,} | {v/MIB:.9f} |')
 lines+=['','档位为各阈值向上取整到MiB后±16 MiB，加512/640/768/1024/1536/2048 MiB并去重。256/384 MiB仅用于预期拒绝。','','| 预算MiB | 预检状态 / 模式 | fixed MiB | factors MiB | reserve MiB | 页槽 | 记录缓存额度MiB |','|---:|---|---:|---:|---:|---:|---:|']
 for f in sorted((OUT/'preflight').glob('*/memory_plan.json'),key=lambda x:read(x)['budget_bytes']):
  if not f.parent.name.isdigit():continue
  p=read(f);lines.append(f"| {p['budget_bytes']/MIB:.0f} | {p.get('mode',p['status'])} | {p['fixed_bytes']/MIB:.0f} | {p['factors_bytes']/MIB:.3f} | {p['reserve_bytes']/MIB:.0f} | {p.get('routing_capacity_pages','—')} | {p.get('optional_cache_bytes',0)/MIB:.3f} |")
 lines+=['','## 实测与验收','',
 '固定原BFS索引、beam1、32workers，原独立validation热点排名。pilot为原test顺序前100条、L100/300/580；完整实验为原800条、40档L；每组每L预热100条，两轮反转预算顺序。统一O_DIRECT预读，不宣称冷设备缓存。两种查询规模的曲线分开。','',
 f"已验收 **{len(groups)}组、{sum(g['queries'] for g in groups):,}条测量查询**。所有已验收组检查结果ID、recall、访问、剪枝及精算计数与同期常驻参考一致，校验物理I/O单次计账、容量、在途峰值与整进程硬上限。",'',
 '[完整CSV](measured_results.csv) · [完整JSON](measured_results.json) · [边界测试](validation/boundaries.log) · [校准记录](calibration/lock.json) · [预算清单](experiment.json)','']
 if state.get('status')=='failed':lines+=['**运行已停止，后续批次未执行。**',f"错误：`{state.get('error','')}`。失败日志保留，不产生性能点。",'']
 for split in ['pilot','full']:
  subset=[r for r in rows if r['split']==split]
  if not subset:continue
  success=sorted({r['budget_mib'] for r in subset if not r['reference']})
  lines += [f'### {split}','',f"所测档位中已成功预算：{success} MiB；这不是精确最低运行预算。",'']
  if render and (OUT/split/'completed.json').exists():
   spec=importlib.util.spec_from_file_location("auto_policy_plot",Path(__file__).with_name("plot.py"));plot=importlib.util.module_from_spec(spec);spec.loader.exec_module(plot)
   plot.main(split)
  if (OUT/'figures'/f'{split}_curves.png').exists():lines += [f'![{split}曲线](figures/{split}_curves.png)',f'[PDF](figures/{split}_curves.pdf) · [SVG](figures/{split}_curves.svg)','两轮等查询数QPS取调和均值，范围表示两轮min–max，不是置信区间。预检拒绝独立显示，不作零吞吐。','']
  if split=='full':
   lines+=['以下仅列L100/300/580便于与pilot对照；CSV/JSON保留全部40档L。','']
  lines+=['| 配置/轮 | L | Recall | QPS | P95 ms | P99 ms | 页/查询 | routing读页 | VmPeak/RSS MiB |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
  for r in subset:
   if split=='full' and r['L'] not in [100,300,580]:continue
   lines.append(f"| [{r['run']}]({r['source']}) | {r['L']} | {r['recall']:.4f} | {r['qps']:.2f} | {r['p95_ms']:.2f} | {r['p99_ms']:.2f} | {r['pages_per_query']:.2f} | {r['routing_reads']} | {r['VmPeak_mib']:.2f}/{r['RSS_mib']:.2f} |")
  lines+=['']
 lines+=['## 来源与限制','','fixed是经验校准包络，未对每一分配类别精确归因；reserve是政策性留白。VmPeak为用户进程虚拟地址空间峰值，RSS为物理驻留峰值，不包括内核内存和设备缓存。每个运行的command、预处理记录、memory_plan、memory_measurement、terminal日志、逐查询trace和acceptance均保留。历史codes独立额度实验仅为辅助对照，不复用其性能点。']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
