"""Render the corrected protocol; accept only completed v2 evidence."""
from pathlib import Path
import json
from protocol import ROOT,HERE,OUT,REFERENCE,MODES,WIDTHS,LIMIT,SAFETY,reference,sha
LABEL={'baseline':'原配置基线','pages':'跨查询page缓存','hot_graph':'热点邻接表','hot_payload':'热点精细记录','hybrid':'混合缓存','nav':'导航小图','nav_pages':'导航+page','nav_hybrid':'导航+混合','full_payload':'全库精细记录'}

def main():
 _,f,old,mem=reference();OUT.mkdir(parents=True,exist_ok=True)
 verified=json.loads((OUT/'verification.json').read_text()) if (OUT/'verification.json').exists() else None
 checks=json.loads((OUT/'checks.json').read_text())if(OUT/'checks.json').exists()else{}
 state=json.loads((OUT/'execution_state.json').read_text()).get('status','paused') if (OUT/'execution_state.json').exists() else 'paused'
 status_label={'paused':'当前保持暂停；此次只核验、改代码和做正确性检查，未恢复性能实验。','running':'新版实验正在执行，以下只列已完整验收的结果。','completed':'新版上一执行阶段已完成，以下只列已完整验收的结果。','failed':'新版上一执行阶段失败；失败或未完成数据不计入成绩。'}[state]
 accepted=[];problems=[]
 from run import verify_acceptance
 for mode in MODES:
  d=OUT/mode
  if not(d/'acceptance.json').exists():continue
  try:a=verify_acceptance(d)
  except Exception as e:problems.append(f'{mode}: {e}');continue
  result=json.loads((d/'result.json').read_text());measurement=json.loads((d/'memory_measurement.json').read_text())
  for row in result['summary_rows']:
   stats=json.loads((d/'memory_stats'/f'L{row["search_width"]}.json').read_text())
   accepted.append({'mode':mode,**row,'peak_rss_mib':measurement['kernel_wait4_peak_rss_bytes']/2**20,'peak_vm_mib':measurement['sampled_high_water_bytes']['VmPeak']/2**20,'cache_mib':stats['cache_reserved_bytes']/2**20})
 statuses={m:('已完成并验收'if any(r['mode']==m for r in accepted)else'未运行 / 未验收')for m in MODES}
 lines=['# Ours 剩余内存用途测试：原实验配置复用版 v2','',f'**{status_label}**','','## 目标与唯一实验变量','','在原GIST实验的2 GiB进程预算内，比较剩余内存用于page缓存、热点邻接表、热点精细记录、导航小图及组合的效果。固定原搜索、图、查询和磁盘布局，只改变新增内存策略。','','## 核验依据','','- [原实验命令](results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/Ours-Disk/command.json)','- [原实验结果](results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/Ours-Disk/result.json)','- [原内存测量](results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/Ours-Disk/memory_measurement.json)','- [v2输入核验明细](results/04_ours_memory_budget/v2/verification.json)；迁移路径按内容哈希识别，不重新划分查询。','',f'配置和输入核验状态：**{"通过" if verified else "待核验"}**。','', '## 固定参数','','| 项目 | 沿用配置 |','|---|---|','| 数据 | GIST：1,000,000条，960维 |','| 图 | 原Ours图，R64 / Lbuild400，不重建 |','| 页布局 | 原BFS graph+compact合并页，residual独立页；保留id_to_slot映射 |','| 测试查询 | 原800条、原ground truth、原optimized_order.u32 |','| 预热 | 原流程100条，即测试顺序前100条；原生流程不变 |','| 搜索候选池 L | 10–30逐整数；40–100步长10；140–580步长40，共40档 |','| beam / workers | 1 / 32 |','| top-k / epsilon / rerank | 10 / 1.9 / 最多100条 |','| query编码与gate | 原INT8 query、DB1 gate |','| I/O | 原O_DIRECT + libaio，4 KiB页，最多128在途I/O |','| 重复次数 | 原repeat_id=0，一次完整扫描 |','| 内存执行口径 | 原测量器、RLIMIT_AS=2 GiB，记录VmPeak和wait4峰值RSS |','| 环境 | 原MALLOC_ARENA_MAX=2、OMP_DYNAMIC=FALSE、OPENBLAS_NUM_THREADS=1、MKL_NUM_THREADS=1、QG05_MEASURE_WHOLE_PROCESS=1 |','', '**L是查询候选池容量**，不是建图Lbuild400、总访问节点数、读页数或并发数。L30最多对最终30个候选重排，L100/L300最多100个，这沿用原实现。','', '## 内存容量如何确定','',f'原历史完整曲线的实测峰值RSS为 **{mem["kernel_wait4_peak_rss_bytes"]/2**20:.2f} MiB**，VmPeak为 **{mem["sampled_high_water_bytes"]["VmPeak"]/2**20:.2f} MiB**。这些是历史基线数据，不是新版已测结果。','', '新程序先跑无新增缓存的完整40档基线，再计算所有方案共享的可用额度：','', '`可用额度 = 2 GiB − 新基线完整扫描的 VmPeak − 64 MiB统一运行余量`','', '- 不再设256 MiB缓存上限；不能用“2 GiB−RSS”当作可全部分配的内存。','- 缓存数据、索引映射、FIFO管理结构、导航图元数据均计入额度；硬上限覆盖整个用户进程。','- 64 MiB为统一预留余量，不是原搜索参数，也不是已验证的最优值。若预算检查失败，本次结果无效，不单独放宽某个方案。','- 静态热点受验证集覆盖限制，可能用不满额度；实际占用单列。全库精细记录放不下就标为不适用，不能突破2 GiB。','- 该口径不含内核和设备缓存，不宣称cgroup总内存限制或受控冷盘。','', '## 测试方案及来源边界','','| 方案 | 新增内存放什么 | 改变搜索路径 | 状态 |','|---|---|---|---|']
 descriptions={'baseline':('无新增缓存，保留原每查询4 MiB缓存','否'),'pages':('16分片FIFO，缓存合并页和residual页','否'),'hot_graph':('按各L归一化展开频率选邻接表，保持边序','否'),'hot_payload':('compact/residual分开归一化后等权排名，缓存完整记录','否'),'hybrid':('邻接表/精细记录各最多25%，所有剩余额度给page','否'),'nav':('2,048代表点的16NN+双向环，寻找原图入口','是，仅入口策略'),'nav_pages':('导航图+page缓存，导航元数据先扣除','是，仅入口策略'),'nav_hybrid':('导航图+混合缓存，导航元数据先扣除','是，仅入口策略'),'full_payload':('全库compact+residual，保留DB1 gate和原重排','否')}
 for m in MODES:lines.append(f'| {LABEL[m]} | {descriptions[m][0]} | {descriptions[m][1]} | {statuses[m]} |')
 lines+=['', '热点节点缓存可对应 [DiskANN原论文§3.4](https://suhasjs.github.io/files/diskann_neurips19.pdf)。FIFO分片、拆分缓存对象和混合比例是本实验设计，不声称论文证明其最优。导航小图借鉴分层导航思路，但不是 [HNSW](https://arxiv.org/abs/1603.09320) 复现；其内部beam16/最多32次展开仅属于新导航策略，不改变原主图beam1。全库精细记录是诊断端点，**没有实现新的2-bit中间筛选层**。','','## 热点统计、淘汰与容量回收\n\n- 仅使用原200条验证查询。每个L保留原100条预热，在所有worker预热完成、正式计时开始前清零热点与I/O计数。预热不参与排名。\n- 分别统计graph、compact、residual的逻辑请求次数；重复请求照计，不等同于磁盘读取。对每个类别、每个L，节点分数为“节点请求次数 / 此类别该L总请求次数”，再对原40档L等权平均；某档总数为0则贡献0。\n- 邻接表按graph分数排名；完整精细记录按 `0.5 × compact分数 + 0.5 × residual分数` 排名。只选正分节点，同分按节点ID升序。这是频率代理，50%/50%不是已证实的最优权重。\n- 热点在测试前预加载，测试期间不新增、不替换。page缓存按实际读取动态填入，16分片各自FIFO，命中不刷新插入顺序；每个L开始清空，随后沿用原预热。\n- hybrid在扣除导航元数据后，邻接表与精细记录各最多使用25%；page得到扣除两者实际计账后的全部剩余额度（至少50%，可更多）。无可缓存节点时不分配空映射表；page按16分片整页取整，少量尾部余量不强填。单独热点方案仍允许用不满，以便隔离策略效果。\n- `memory_stats/L*.json` 分别记录三类的logical_requests、io_requests、read_pages、read_bytes，以及page容量、缓存命中和总计账。I/O按触发读取的操作归属；graph与compact共享页，不能把某类别的减少当作独立因果收益。性能表对同L新基线计算总读页差，纯缓存仍要求逐查询结果与候选一致。\n- profile保存 `L{L}_{graph,compact,residual}_counts.u64` 原始计数、四份 `*_scores.f64` 分数及 `profile_policy.json`。原始计数与分数均为little-endian，按逻辑节点ID排列，可重算排名；旧u32训练文件不再使用。\n- 统计钩子对新版基线和各策略一致启用，有额外计数成本；性能比较采用同一新版二进制，历史QPS仅作为背景。\n', '', '## 测试顺序与验收条件','','1. 核对原40档、beam、查询、顺序、图与页文件哈希，锁定配置。','2. 编译独立v2快照。原生调度、预热、计时、INT8距离、gate、重排均复用，不使用旧诊断调度器。','3. 新基线完整40档×800查询；与保存的原基线逐查询核对ID、召回、访问数及候选数。不一致则停止性能解释。','4. 在原200条验证查询及保存顺序上，以原40档搜索统计热点；测试查询不参与静态热点选择。导航图仅用底库构建。','5. 根据新基线VmPeak确定统一额度，依次运行page、热点邻接表、热点精细记录、混合、导航及组合、全库端点。各方案完整扫描原40档。','6. 每个L配置开始前清空动态共享page缓存，再执行原100条预热；每个方案独立进程，预加载开销单独记录。','7. 纯缓存逐查询核对返回ID、召回、visited、距离计算及DB1/full4候选数；导航以相同召回目标下的QPS/P99比较，不要求ID相同。','8. 只接纳完整配置、内存验收、结果校验均通过的运行。失败/不完整数据不补数、不计为成功。','', '## 本次正确性检查（不是性能测试）','']
 if checks:
  for k,v in checks.items():lines.append(f'- {k}：{v}')
 else:lines.append('- 检查记录尚未生成。')
 lines+=['', '## v2 实测性能结果','']
 if not accepted:lines.append('**暂无已验收的 v2 性能结果。配置核验、训练和单元测试不计为性能成绩。**')
 else:
  baseline_pages={r['search_width']:r['sectors_4k_per_query'] for r in accepted if r['mode']=='baseline'}
  baseline_qps={r['search_width']:r['qps'] for r in accepted if r['mode']=='baseline'}
  def qps_change(row):
   if row['mode']=='baseline':return '—'
   base=baseline_qps.get(row['search_width'])
   return f"{(row['qps']/base-1)*100:+.1f}%" if base and base>0 else '—（缺少有效基线）'
  lines+=['相对基线增长幅度 =（方案QPS ÷ 本轮同L基线QPS − 1）×100%。采用未舍入原始QPS计算，保留一位小数；基线自身显示“—”，负值表示下降。导航方案会改变召回，因此这是同L比较，不是严格同Recall比较。','']
  lines+=['| 方案 | L | Recall@10 | QPS | 相对基线 | P99 μs | 读页/查询 | 比基线少读页/查询 | 峰值RSS MiB | 缓存计账 MiB |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
  for r in accepted:lines.append(f'| {LABEL[r["mode"]]} | {r["search_width"]} | {r["recall"]:.4f} | {r["qps"]:.2f} | {qps_change(r)} | {r["latency_p99_us"]:.1f} | {r["sectors_4k_per_query"]:.1f} | {baseline_pages.get(r["search_width"], float('nan'))-r["sectors_4k_per_query"]:.1f} | {r["peak_rss_mib"]:.1f} | {r["cache_mib"]:.1f} |')
 if problems:lines+=['','未通过证据检查：',*['- '+p for p in problems]]
 lines+=['', '## 旧诊断记录如何处理','','之前四组使用beam4、300条自选查询、旧graph/payload分离页布局以及256 MiB档位，**不满足本协议，全部排除出v2性能表**。原始记录仍保留在 [旧结果目录](results/04_ours_memory_budget/gist/)，便于追溯，不作为正式对比。旧队列保持挂起，不应直接恢复。','', '## 代码与复现','','详见 [测试入口说明](experiments/04_ours_memory_budget/README.md)。`run.py` 默认仅核验；不带`--execute`不会启动性能队列。新版输出固定写入 `results/04_ours_memory_budget/v2/`，拒绝覆盖已有运行。','','本次配置对齐不等于证明新代码与历史二进制完全一致；后续新基线逐查询回归是必经步骤。','']
 if (OUT/'performance_review.json').exists():lines[2:2]=['**性能异常待核查：本轮baseline在L=100仅9.73 QPS，历史同配置为109.30 QPS。进一步ABBA复测发现历史原程序在当前环境也仅约10 QPS，主要等待存储I/O完成；历史环境为何更快仍未确定。下文加速倍数和最优方案判断暂不推广，仅保留本轮存储条件下的观察。详见[基线异常核查](ours_memory_budget_baseline_audit.md)。**','']
 if OUT.name=='v3_direct':
  lines=[line.replace('results/04_ours_memory_budget/v2/','results/04_ours_memory_budget/v3_direct/').replace('复用版 v2','复用版 v3_direct').replace('v2 实测','v3_direct 实测') for line in lines]
  lines[2:2]=['**统一预读重测：每个搜索进程启动前，调用03的storage_precondition.prepare_search完整O_DIRECT顺序读取实际页文件一次；失败即停止，耗时单列不计QPS。所有策略与baseline都重新运行，旧v2成绩不混入。沿用原启动方式，无NUMA绑定；这是04内存消融实验，不是正式03绑定调度。**','', '基线性能门槛：相对已完成的uniform03_current_full恢复测量，L100 QPS和完整40档总吞吐比例都须在0.75–1.35内；只用于拦截大幅漂移，不是统计等效检验。','']
 text='\n'.join(lines)
 (ROOT/'ours_memory_budget_tests.md').write_text(text)
 (OUT/'report.md').write_text(text)
 (OUT/'summary.json').write_text(json.dumps({'results':accepted,'evidence_errors':problems,'performance_paused':state=='paused'},indent=2))
 print(f'report updated; {len(accepted)} accepted v2 performance rows')
if __name__=='__main__':main()
