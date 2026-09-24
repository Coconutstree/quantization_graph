"""Report accepted data; expose cache cost, empirical gate semantics and Recall."""
import csv,json,statistics
from prepare import OUT,dump
NAMES={'pca':'PCA + 1-bit','pca_residual':'PCA + 1-bit + residual norm','full1bit':'完整 1-bit'}
def read(p):return json.loads(p.read_text())
def main():
 rows=[]
 for a in sorted(OUT.glob('*/*/acceptance.json')):
  f=a.parent;v=read(f/'variant.json');p=read(f/'memory_plan.json');mem=read(f/'memory_measurement.json');rs=read(f/'routing_stats.json')
  for x in read(f/'result.json')['summary_rows']:
   w=x['search_width'];n=x['query_count'];s=read(f/'memory_stats'/f'L{w}.json');route=rs.get(str(w),{})
   gate_candidates=x['db1_checks']-(x['visited_nodes']-1)if v['gate']else 0
   gate_pruned=gate_candidates-x['db1_survivors']if v['gate']else 0
   assert gate_pruned>=-1e-6
   rows.append(dict(variant=v['variant'],split=f.parent.name,run=f.name,dimension=v['dimension'],M=32,width=w,
    gate=v['gate'],recall=x['recall'],qps=x['qps'],total_pages_q=x['sectors_4k_per_query'],
    route_pages_q=route.get('reads',0)/n,record_graph_pages_q=x['sectors_4k_per_query']-route.get('reads',0)/n,
    full4_pages_q=x['full4_page_reads'],full4_candidates_q=x['full4_candidates'],
    gate_candidates_q=gate_candidates,gate_pruned_before_full4_q=gate_pruned,
    gate_pruned_fraction=gate_pruned/gate_candidates if gate_candidates else 0,
    route_hit_rate=route.get('hits',0)/max(1,route.get('requests',0)),record_hits_q=s['record_hits']/n,
    query_prep_us=x['query_prep_us'],record_cache_mib=p['optional_cache_bytes']/2**20,
    residual_norm_mib=p['pca_residual_norm_bytes']/2**20,codes_mib=p['codes_bytes']/2**20,
    route_cache_mib=(p['routing_page_data_bytes']+p['routing_page_metadata_bytes']+p['paging_service_bytes'])/2**20,
    factors_mib=p['factors_bytes']/2**20,pca_extra_mib=p['pca_extra_reserved_bytes']/2**20,
    admission_mib=p['admission_bytes']/2**20,vmpeak_mib=mem['sampled_high_water_bytes']['VmPeak']/2**20,
    source=str((f/'result.json').relative_to(OUT))))
 if not rows:return
 with (OUT/'measured_results.csv').open('w')as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 effects=[]
 for variant in NAMES:
  xs=[x for x in rows if x['variant']==variant and x['split']=='test']
  if not xs:continue
  assert len({x['width']for x in xs})==1
  avg=lambda key:statistics.mean(x[key]for x in xs)
  effects.append(dict(variant=variant,dimension=xs[0]['dimension'],M=32,width=xs[0]['width'],repeats=len(xs),
   recall=avg('recall'),qps=statistics.harmonic_mean(x['qps']for x in xs),total_pages_q=avg('total_pages_q'),
   route_pages_q=avg('route_pages_q'),full4_candidates_q=avg('full4_candidates_q'),
   gate_pruned_before_full4_q=avg('gate_pruned_before_full4_q'),gate_pruned_fraction=avg('gate_pruned_fraction'),
   record_hits_q=avg('record_hits_q'),admission_mib=avg('admission_mib'),
   vmpeak_mib=max(x['vmpeak_mib']for x in xs),meets_095=min(x['recall']for x in xs)>=.95))
 dump(OUT/'effects.json',effects)
 state=read(OUT/'state.json')if(OUT/'state.json').exists()else{}
 lines=['# GIST：PCA residual 三组对照，固定 M=32','',
  f'状态：{"完成"if state.get("status")=="completed"else"运行中，仅显示已验收结果"}。本轮只测试 M=32，没有 M=64。', '',
  '实验归属：本页保留为 **04 优化消融**；相同 RAM 预算下的跨方法正式比较单独归入 [05 内存预算实验](../../../experiments/05_memory_budget/README.md)，不混入 03 主实验。', '',
  '正式方案决定（2026-09-21）：完整 1-bit 放不下时采用 **PCA + 1-bit，不加 residual norm，M=32**，维度按完整内存规划选择最高可常驻前缀；能放下时保留原维度路径。已接入[正式 03 入口](../../../experiments/03_disk_system/README.md#ours-低内存主方案pca--1-bit2026-09-21)。本页仍是原 538 MiB 消融结果，不是新 RSS 协议的正式性能数据。', '',
  '三组使用同一 R64 图、538 MiB 整进程预算、32 workers、beam=1、相同完整 4-bit 重算和 residual 重排。M=32 指每次扩展邻居的 shortlist；width 是图搜索候选池大小。', '',
  '## 三种版本和实际门控','',
  '1. PCA + 1-bit：`max(0, 低维估计 − native 1-bit误差界)`。',
  '2. PCA + 1-bit + residual norm：第一式加 `(||r_query||−||r_base||)²`。没有再扣 PCA 截断误差。',
  '3. 完整 1-bit：原始 960 维、实际 1024-bit codes，采用本次统一 M32 与完整 4-bit 重算口径。', '',
  '三组先按 epsilon=0 的 1-bit 分数取 top32，再按 epsilon=1.9 计算门控，pool 已满且该分数大于最差 4-bit 分数时跳过候选。residual 仅加入第二次门控，不改变第一轮排序公式。PCA 不复用低维 short_ip，完整维度基线也统一不复用 INT8 short_ip。', '',
  '**本轮是经验门控的 Recall/I/O/QPS 对照，不是严格安全剪枝证明。** epsilon=1.9 属于统计误差口径，当前 τ 又是近似 4-bit 分数。数学上的 PCA residual 下界并不单独解决这两点。当前完整 1-bit baseline 也不是旧版无 top32 限制的生产 baseline，不能直接拼接历史 QPS。', '',
  '## 独立 test800，两轮反序确认','',
  '|版本|维度|width|Recall@10|QPS 调和均值|总页/query|route 页/query|完整4bit候选/query|门控跳过/query|轮数|',
  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
 for x in effects:lines.append(f'|{NAMES[x["variant"]]}|{x["dimension"]}|{x["width"]}|{x["recall"]:.6f}|{x["qps"]:.2f}|{x["total_pages_q"]:.2f}|{x["route_pages_q"]:.2f}|{x["full4_candidates_q"]:.2f}|{x["gate_pruned_before_full4_q"]:.2f}|{x["repeats"]}|')
 if len(effects)==3 and all(x['repeats']==2 for x in effects):
  base=next(x for x in effects if x['variant']=='full1bit')
  for x in effects:
   if x['variant']=='full1bit':continue
   lines+=['',f'{NAMES[x["variant"]]} 相对本轮完整 1-bit：Recall 差 {x["recall"]-base["recall"]:+.6f}，QPS {x["qps"]/base["qps"]-1:+.1%}，总 I/O {x["total_pages_q"]/base["total_pages_q"]-1:+.1%}。{"test 达到 0.95 目标"if x["meets_095"]else"test 未达到 0.95 目标，不据 test 重选参数"}。']
  a,b=effects[:2]
  lines+=['',f'加 residual 相对不加：Recall {b["recall"]-a["recall"]:+.6f}，QPS {b["qps"]/a["qps"]-1:+.1%}，总 I/O {b["total_pages_q"]/a["total_pages_q"]-1:+.1%}。这里同时包含 residual 的门控影响和 record cache 从 12 MiB 减至 8 MiB 的代价。各组 Recall 若不同，就不能称为精确等 Recall 比较。']
 lines+=['','## validation width 扫描','','|版本|width|Recall|QPS|总页/query|门控前top32候选/query|门控跳过/query|','|---|---:|---:|---:|---:|---:|---:|']
 for x in rows:
  if x['split']=='tune'and('_grid_'in x['run']or'_extended_'in x['run']):
   lines.append(f'|{NAMES[x["variant"]]}|{x["width"]}|{x["recall"]:.6f}|{x["qps"]:.2f}|{x["total_pages_q"]:.2f}|{x["gate_candidates_q"]:.2f}|{x["gate_pruned_before_full4_q"]:.2f}|')
 lines+=['','validation100 从 width=40/60/100/180 中选 Recall≥0.95 的最快点；不达标才按预定规则追加260/380。每组单独锁定 width，再运行 test，不能把 test 结果用于重新选参。','',
  '## 相同 cache 的 residual 消融','','|版本|cache MiB|width|Recall|QPS|总页/query|完整4bit候选/query|门控跳过/query|','|---|---:|---:|---:|---:|---:|---:|---:|']
 for x in rows:
  if x['split']=='tune'and x['width']==100 and(x['run']=='pca_cache8_control_r1'or x['run']=='pca_residual_grid_r1'):
   lines.append(f'|{NAMES[x["variant"]]}|8|100|{x["recall"]:.6f}|{x["qps"]:.2f}|{x["total_pages_q"]:.2f}|{x["full4_candidates_q"]:.2f}|{x["gate_pruned_before_full4_q"]:.2f}|')
 lines+=['','gate-off/train100 两组均 cache8，逐查询核对 result IDs、Recall、visited、距离计算次数及候选数一致。上述同 cache/tune 对照只改变 residual 的门控贡献及计算成本。单轮 QPS 差异包含噪声。','',
  '## 内存','','|版本|codes总量 MiB|route页及服务 MiB|factors MiB|PCA预留 MiB|residual norm MiB|record cache MiB|准入 MiB|最大VmPeak MiB|','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
 for v in NAMES:
  xs=[x for x in rows if x['variant']==v and x['split']in('test','tune')and'control'not in x['run']]
  if xs:
   x=xs[0];lines.append(f'|{NAMES[v]}|{x["codes_mib"]:.3f}|{x["route_cache_mib"]:.3f}|{x["factors_mib"]:.3f}|{x["pca_extra_mib"]:.3f}|{x["residual_norm_mib"]:.3f}|{x["record_cache_mib"]:g}|{x["admission_mib"]:.3f}|{max(t["vmpeak_mib"]for t in xs):.3f}|')
 lines+=['','预算沿用 fixed=426 MiB、reserve=64 MiB；PCA 两组都按完整账本选中128维。残差统计为每点一个 FP32 平方范数，共4,000,000 bytes；query 通过 centered norm²−projected norm² 计算尾部平方范数，不在线加载完整960×960 basis。完整1bit的 codes 总量在 SSD，不是122 MiB全部常驻；其route页、服务和paging scratch按native账本计费。准入不是物理内存极限。','',
  '读页单位为4 KiB，route I/O单列；非route包含图页、compact和重排residual。DB1 checks 包含排序和门控两次估计，因此不是唯一节点数。门控跳过/query 指在完整4bit读取前被跳过的候选，不包括验证后插入阶段的第二次阈值检查。PCA query准备计入QPS。','',
  'O_DIRECT，warmup100排除在计时之外；设备缓存不受控，未宣称完全独占。完整原始命令、内存硬限制、逐查询结果和I/O加总核验分别保留。','',
  '[CSV](measured_results.csv) · [协议](protocol.json) · [选参锁定](selection_lock.json) · [验收](audit.json) · [复现](../../../experiments/04_ours_memory_budget/gist_pca_residual_ablation/README.md)','']
 if (OUT/'figures/recall_qps_validation.png').exists():
  lines+=['![三组validation曲线](figures/recall_qps_validation.png)','',
   '曲线仅连接本轮实测width点，M固定32；左图Recall–QPS，右图Recall–总I/O。每点为validation100单次测量，无平滑或虚构置信区间。标注数字为width。', '',
   '[PDF](figures/recall_qps_validation.pdf) · [SVG](figures/recall_qps_validation.svg)','']
 (OUT/'report.md').write_text('\n'.join(lines))
if __name__=='__main__':main()
