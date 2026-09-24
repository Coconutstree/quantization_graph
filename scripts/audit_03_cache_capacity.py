"""Capacity audit only: no claim about records actually touched or retained."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parents[1]
plans=json.loads((ROOT/'docs/analysis/03_common_budget_20260921/audit.json').read_text())['ours_plans']
out=ROOT/'docs/analysis/03_cache_capacity_20260921';out.mkdir(exist_ok=True)
rows=[]
for dataset in ['gist','agnews','dbpedia']:
 path=ROOT/'work/05_disk_system_fair/disk_root/05_disk_system_fair/05C_disk_system_fair'/dataset/'Ours-Disk/hybrid_disk/index.meta'
 m=dict(line.split('=',1) for line in path.read_text().splitlines());n=int(m['base_count']);stride=int(m['ours_compact_record_bytes'])+int(m['ours_residual_record_bytes'])
 # Conservative upper bound: actual BFS drops pages exceeding the 10% node cap.
 graph_upper=(n//10//int(m['nodes_per_page'])+1)*4096
 for gib in [2,4]:
  plan=plans[dataset][str(gib)];budget=plan['cache_available_bytes']-graph_upper
  per=max(0,(budget-4*n-4096)//16//(stride+5))
  dynamic_capacity=sum(min(per,len(range(k,n,16))) for k in range(16))
  rows.append(dict(dataset=dataset,budget_gib=gib,n=n,stride=stride,payload_bytes=n*stride,required_bytes=plan['required_bytes'],graph_cache_upper_bytes=graph_upper,record_budget_lower_bytes=budget,full_records_with_conservative_maps_bytes=n*(stride+13)+8192,dynamic_only_capacity=dynamic_capacity,dynamic_only_fraction=dynamic_capacity/n,aggregate_full_records_fit=n*(stride+13)+8192<=budget,actual_hot_ranks_available=False,meta_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
(out/'audit.json').write_text(json.dumps({'scope':'capacity planning, not measured residency','workers':32,'rows':rows},indent=2)+'\n')
lines=['# 03 Ours 记录缓存容量核查','','按现有正式 full1bit 规划（32 workers）和索引真实 record stride 计算。扣除固定内存规划、BFS 图缓存上界后，剩余额度用于 hot+dynamic。不是用磁盘页文件大小代替内存记录大小。','','| 数据集 | 总预算 GiB | 全量记录净载荷 GiB | 记录缓存可用 GiB（下界） | 纯 dynamic 理论覆盖率 |','|---|---:|---:|---:|---:|']
for r in rows:lines.append(f"| {r['dataset']} | {r['budget_gib']} | {r['payload_bytes']/2**30:.3f} | {r['record_budget_lower_bytes']/2**30:.3f} | {r['dynamic_only_fraction']:.1%} |")
lines+=['','GIST/AGNews 在 2 GiB 下就能容纳全记录总载荷；DBpedia 在 4 GiB 下也能容纳。记录包含 compact 和 residual，不能仅按 1-bit codes 容量判断。','','边界：表中的覆盖率严格对应 static ranks 为空时的 16 分片分配公式，不是实际 hot+dynamic 命中率。本核查未加载真实 validation 热点 ranks；静态热点数和分片不均衡会改变容量，必须在运行中记录 static_nodes、capacity_nodes、resident_nodes、valid_payload_bytes、evictions 和命中率。动态槽预分配不意味着数据已从磁盘填入；每个 width 清空后，只有预热/测量访问过的记录有效。BFS 图缓存仍只允许约 10% 节点，因此记录全容纳也不等于全图常驻或零 I/O。','','预算结论：此前仅以 Starling 能运行为依据选择 4 GiB，不足以证明这是内存受限磁盘实验。4 GiB 可以作为统一资源下的高内存系统对比，但不能声称这些小数据集的记录均需分页。若目标是必须存在容量压力，应增大数据规模，或在 05 扫描更低共同预算并如实列出放不下的方法。不能只限制 Ours 缓存来制造磁盘访问。当前代码默认 4 GiB 保持不变，正式预算结论应结合本核查重新决定。']
(out/'report.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
