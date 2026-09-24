"""Prepare targeted edits to the user's revision-52 experiment document; no network writes."""
from pathlib import Path
import copy, html, json, struct, xml.etree.ElementTree as ET
P=Path(__file__).resolve().parent
repo=P.parents[2]
before=json.loads((P/'before.json').read_text())['data']['document']
root=ET.fromstring('<doc>'+before['content']+'</doc>')
old=list(root); patches=[]; changes={}
E=html.escape
def p(s):return '<p>'+E(s)+'</p>'
def h(s):return '<h2>'+E(s)+'</h2>'
def pre(s):return '<pre lang="text"><code>'+E(s)+'</code></pre>'
def link(label,url):return '<p><a href="'+E(url,quote=True)+'">'+E(label)+'</a></p>'
def table(headers,rows):
 return '<table><thead><tr>'+''.join('<th><p>'+E(c)+'</p></th>' for c in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td><p>'+E(c)+'</p></td>' for c in r)+'</tr>' for r in rows)+'</tbody></table>'
def replace(i,s,reason):
 ET.fromstring('<fragment>'+s+'</fragment>')
 changes[i]=s; patches.append({'command':'block_replace','block_id':old[i].get('id'),'content':s,'reason':reason})
replace(2,p('本实验在统一硬件、查询负载和明确内存预算下比较 Ours 与 ANN 方法。保留 05A 量化器、05B 图与存储机制、05C 完整系统三层结构；正式主结果固定 32 个查询 worker、5 次独立重复。新版磁盘主实验拟采用 4 GiB cgroup v2 内存预算，先做可运行性检查，再冻结协议与配置；05C 另做 1/2/4/8 GiB、固定 32 线程的预算扫描。05A resident 为不受该预算约束的计算参考，单独报告用量。比较 Recall@10、QPS、可获取的延迟、实际内存、磁盘索引大小与 I/O。')+p('2026-09-17 核验更新，基于本飞书文档 revision 52 局部修改。4 GiB 是新方案的待验证起点，不是已完成的测量；cgroup 跑批与验收接口尚需实现。旧 2 GiB RLIMIT_AS 结果保留为地址空间受限诊断，不能改标签充当新版内存预算实验。第 10 节保留历史审计，第 11—14 节给出本次核验、执行协议和依据。'),'澄清预算对象、保留原五次重复、区分方案与完成状态')
replace(8,p('实验保留 3 个 core 目标数据集和 5 个 extension 候选。core 表示计划的验收范围，不表示当前已全部 formal。按“数据集 × 实验层 × 方法 × 配置 × 预算”逐项准入：输入与真值匹配、划分有效、原生路径支持、实际预算满足。05A/05B 检查各自需要的 fixed candidates、量化产物和图；05C 检查 Ours、DiskANN、AiSAQ、Starling 的各自原生索引，不再要求旧五系统或不相关的 SAQ 产物全部齐备。')+p('Cohere10M 对当前 L2-only 路径仍因 metric 不匹配而阻塞。MSMARCO 的 Ours DB1 位面按当前 N、d=1024 估算已约 13.53 GiB，尚未计因子和工作区，在本次 1—8 GiB 范围内不可行；这不能推导其他方法也不可行。扩展集记录每种方法的支持情况，允许缺失项，不强行补齐曲线。'),'准入按方法配置、预算分开，避免一方法阻塞所有实验')
rows=[];headers=[]
labels={'agnews':'文本 embedding','dbpedia':'高维文本 embedding','gist':'视觉检索','sift10m':'UCI SIFT10M 派生划分','deep1B':'Deep1B 约 10M 子集','msmarco':'大规模文本 embedding','bigann10m':'BIGANN/SIFT 10M','cohere10m':'官方真值按 inner product'}
for ds in labels:
 files={}
 for kind,ext in [('base','fvecs'),('query','fvecs'),('groundtruth','ivecs')]:
  f=repo/'data'/ds/f'{ds}_{kind}.{ext}'
  with f.open('rb') as stream:dim,=struct.unpack('<i',stream.read(4))
  size=f.stat().st_size;assert size%(4*(dim+1))==0
  files[kind]={'path':str(f.relative_to(repo)),'dimension':dim,'rows':size//(4*(dim+1)),'bytes':size}
 headers.append({'dataset':ds,'files':files})
 rows.append([ds,'core 目标' if ds in ['agnews','dbpedia','gist'] else 'extension',f'{files["base"]["rows"]:,}',str(files['base']['dimension']),f'{files["query"]["rows"]:,}',f'{files["base"]["bytes"]/(1<<30):.3f}',labels[ds]])
(P/'input_header_check.json').write_text(json.dumps({'scope':'file headers and sizes only; no full hash or ground-truth recomputation','datasets':headers},ensure_ascii=False,indent=2))
replace(9,table(['数据集','范围','base N','D','query 条数','base 文件 GiB','用途'],rows)+p('2026-09-17 通过本地 fvecs/ivecs 头部和文件长度核对上述 N/D/query 条数；大小统一为 base 文件字节数，包含每行维度头，不再混用目录、缓存与三件套大小。本次没有对全部大文件重新计算 SHA-256，也没有重算所有真值；来源、归一化和真值正确性仍由正式运行清单及验证证据确认。'),'修正混用的大小口径并保存实际头部核验')
replace(11,p('正式 input manifest 记录 N/D/source_metric/runner_metric/k、输入类型、归一化验证、base/query/GT 路径与 SHA-256。当前统一任务按 squared L2 评估；DBpedia、Deep1B、MSMARCO 若利用 L2 与 cosine/IP 排序等价性，必须先核验所需归一化条件和真值，不能仅凭数据集名称判断。Cohere10M 的原 IP 真值不能直接交给 L2-only 路径，也不能通过单独归一化输入后继续沿用原真值。原生 baseline 是否支持某度量，以固定版本和实际配置为准。'),'不把原文归一化描述当作本次已验证结论')
replace(13,table(['参数','新版设置'],[
 ['workers','32 个实际查询 worker；核查 OpenMP/MKL 等隐藏线程。GIST 1/4/8/16/32 scaling 为独立补充组。'],
 ['磁盘主实验预算','05A payload_on_ssd、05B、05C 拟采用 cgroup v2 memory.max=4294967296 B（4 GiB），memory.swap.max=0；预检后冻结。'],
 ['预算范围','从原生搜索进程启动、索引加载、工作区初始化到预热和查询结束；包含所有线程、查询缓冲、缓存及 cgroup 计入的其他内存。建索引另测。'],
 ['05A resident','计算参考，预算标签为 unconstrained_reference；报告实际驻留/峰值，不并入受限磁盘系统主图。'],
 ['05C 内存扫描','1/2/4/8 GiB；全部保持 32 线程，不因某方法失败而单独降线程。'],
 ['query split','保留策略默认值：AGNews/GIST/SIFT10M/MSMARCO 为 200 validation，其他为 1000；强制 0<validation<total，记录实际划分和 SHA-256。'],
 ['实际内存指标','cgroup memory.peak、进程峰值 RSS、索引/线程工作区分类；VmPeak 仅作地址空间诊断。'],
 ['repeats','pilot=1；formal=5，repeat_id={0,1,2,3,4}。'],
 ['seed','外层查询顺序/运行调度 seed=20260813；各原生训练/建图 seed 单独记录，不强制改源码统一。'],
 ['page size','4096 B 作为页数统计单位；同时记录实际请求字节和原生布局，不假定一节点一页。'],
 ['disk profile','auto 仅用于发现设备，正式运行冻结目标 SSD、挂载、文件系统和 I/O 后端。'],
 ['disk root / output root','保留 work/05_disk_system_fair/disk_root 与 results/disk_environment；不同协议/预算/运行 ID 分目录。'],
 ['run order','来源和数据核验 → 布局与预算预检 → 导出/复用索引 → 正确性与计时核验 → validation 调参 → 冻结配置 → formal → 验收与绘图。']]),'以总内存控制替代记账即限额，保留原重复和划分')
replace(14,p('05A/05B 的受控 I/O 实验保留经核验的 direct I/O 端口和已登记调度参数。05C 保留各原生方法的读取层、缓存和 I/O 调度；不得为了统一最大 128 个 in-flight I/O 而改写官方代码。记录实际 direct/buffered 模式、请求粒度、队列深度和后端，主对比要求可比的缓存条件。节点可跨多页；缺失的逐查询计数写 null 并标明 availability，不用批量均值伪造逐查询记录。当前 DiskANN Rust 接入的读取层差异仍需核验，不能仅因调用官方路径就称为无修改原版。'),'移除对原生系统强行统一调度和虚构计数的风险')
replace(18,pre('disk_<dataset>_<layer>_w32_cg4g_pilot_<timestamp>\ndisk_<dataset>_<layer>_w32_cg4g_formal_<timestamp>\ndisk_<dataset>_05c_w32_cg{1,2,4,8}g_scan_<timestamp>\n旧地址空间诊断保留 as2g/as4g 标记；不改名为 cg2g/cg4g。'),'运行ID体现限制机制、预算和实验层')
replace(20,p('保留原始输入、磁盘索引、运行产物、发布结果四类目录。正式报告只引用通过新版验收且可回溯原始 artifact 的 CSV 与图；位于 results/disk_environment 或文件名包含 formal 本身不代表正式结果。下列目录为发布组织约定，已有 test_L_400_w_32 历史目录保持原样。'),'目录不是验收证明')
replace(21,table(['类型','存放位置','内容 / 使用约定'],[
 ['原始输入','data/<dataset>/','base、query、GT；各方法保持相同向量值和 ID。'],
 ['磁盘索引','work/05_disk_system_fair/disk_root/<dataset>/<method>/<index-id>/','原生索引/受控端口产物，记录实际设备；已构建索引可按哈希引用复用。'],
 ['每次运行','results/disk_environment/.formal_runs/runs/<run-id>/','原生输出、日志、结果 ID、资源记录、manifest；此目录约定仍需跑批接口落实。'],
 ['发布结果','results/disk_environment/{01_quantizer_fair,02_diskann_fair,03_system_fair}/<dataset>/<protocol-id>/<run-id>/','csv、logs、figures、manifests；协议标识含线程数、预算和控制机制，避免覆盖旧结果。']]),'保留原根目录，增加协议与run隔离')
replace(25,pre('results/disk_environment/\n  01_quantizer_fair/<dataset>/<protocol-id>/<run-id>/{csv,logs,figures,manifests}/\n  02_diskann_fair/<dataset>/<protocol-id>/<run-id>/{csv,logs,figures,manifests}/\n  03_system_fair/<dataset>/<protocol-id>/<run-id>/{csv,logs,figures,manifests}/\n  .formal_runs/runs/<run-id>/\n  05_disk_system_fair/test_L_400_w_32/  # 保留历史诊断，不自动迁移')+p('后文 05A/05B/05C 输出表中的路径为旧相对命名，执行新版时统一在 <dataset> 后加入 <protocol-id>/<run-id>。原始日志和失败记录均保留；旧宽度点不能与新预算点拼成一条曲线。'),'明确后续输出表路径的新版解释')
replace(38,p('05B 保留 PQ/SQ/SAQ 共用 float32 baseline Vamana 图、Ours 使用 ExRaBitQ4 对称距离构建自有图的安排。Ours 内部消融固定自己的图、输入、物理布局和可控制的查询条件；跨方法差异包含构图与搜索差异。既有 full4-resident/no-gate 分支还可能改变 query codec，必须逐项披露，不能只按名称把差值归因于 gate。当前 locality 读取器要求 coalescing+reuse，不能直接用它跑关闭二者的四级消融；完整四级机制实验先固定支持各状态的普通布局，locality 另作明确的布局实验。'),'核验实际locality开关约束，纠正纯消融归因')
replace(39,table(['项目','内容'],[
 ['Ours','Ours-Disk'],['Baselines','PQ-DiskANN-Disk、SQ-DiskANN-Disk、SAQ-DiskANN-Disk（受控机制版本）'],
 ['控制变量','PQ/SQ/SAQ 同一图哈希；Ours 各消融同一 Ours 图哈希；同一查询集、32 worker、冻结后的 cgroup 预算与 I/O 口径。'],
 ['主结果模式','hybrid_disk；记录普通/locality 等实际布局，不能只靠标签判断。'],
 ['参考模式','disk_payload 与 full4-resident/no-gate 的用途、实际驻留、预算及 query codec 单独披露，不作为严格性能上界。'],
 ['扫描参数','search width；参数实义和生效值写入清单。'],
 ['指标','Recall@10、QPS、可测延迟、visited、距离计算、请求数与字节数；阶段计数仅比较同定义的测量。']]),'统一新预算，保留05B实验对象')
replace(43,table(['ablation','真实含义','解释边界'],[
 ['full4-resident/no-gate','完整主码/payload 驻留；关闭 gate。','驻留量和 query codec 一并变化时，属于组合参考，不是 gate 单因素对照或严格上界。'],
 ['db1-resident/full4-on-ssd','DB1 与因子驻留，筛后读完整主码。','披露残差是否随主码同读、是否仍有查询内缓存。'],
 ['db1+coalescing','同一读取批次按页号排序、去重。','不等于整个 query 的页只读一次，也不等于 BFS 重布局。'],
 ['db1+coalescing+reuse','在上述基础上复用仍在有界缓存中的页。','缓存驱逐后仍可重复读；报告缓存容量、生命周期与元数据。']]),'修正缓存无限命中和页合并范围描述')
replace(44,p('05B 的四级消融应使用同一、支持各开关的物理布局。05C Ours 则冻结当前 locality 配置：图与主码共页、独立 residual.pages、BFS 物理排列和 ID→slot 映射；DB1 筛选后按需取主码，最终用浮点 query 加 residual4 重排。若研究 locality 收益，单列普通布局与 locality 的对照，注明排列、共置、残差分离同时变化，不把全部收益归给单一因素。'),'区分机制消融和完整系统布局')
replace(50,p('05C 主方法更新为 Ours、DiskANN、AiSAQ、Starling 的完整磁盘系统。baseline 优先固定无补丁官方版本，保留原生建图、量化、搜索、重排、缓存与 I/O 调度，只使用官方暴露参数调优。Ours 冻结本次源码、图、编码、locality 布局与查询配置。Glass/Symphony 磁盘移植版仅作单列补充；OG-LVQ 未通过原方法一致性核验前不进入主对比。已有 DiskANN/AiSAQ 兼容补丁路径需披露，不能直接标为无修改原版。'),'主baseline与已确认方案一致')
replace(51,table(['项目','内容'],[
 ['主方法','Ours-Disk、DiskANN-PQ-Disk、AiSAQ-Disk、Starling-Disk'],
 ['控制条件','同一数据与查询/GT、设备、32 worker、统一 cgroup 预算；各方法在预算内使用自己的结构与缓存，不要求同样耗尽内存。'],
 ['主实验','4 GiB 待验证；同 Recall@10 比较 QPS、实际内存和磁盘开销。'],
 ['预算实验','固定 32 worker，1/2/4/8 GiB；记录方法/配置可运行范围与目标召回率下的性能。'],
 ['参数选择','validation 中选择官方图度数、PQ 码长、导航/缓存/搜索参数；正式测试前冻结。不同系统同名 L/beam 不自动等价。'],
 ['可测指标','Recall@10、QPS、峰值内存、索引大小；延迟分位数和内部 I/O 计数按原生可获得性报告。'],
 ['Starling 支持范围','GIST R48 已有索引与诊断；当前固定版本 FP32 路径的 AGNews/DBpedia 跨页限制独立记录，增加内存不能解决。']]),'增加资源扫描与支持范围，不强行补齐四方法')
replace(57,pre('method, protocol_id, experiment_group, dataset, input_hash, index_hash,\nconfig_id, search_param, search_width, beam_width, workers, repeat_id, run_id,\nrecall, qps, latency_mean_us, latency_p50_us, latency_p95_us, latency_p99_us,\nindex_size_bytes, memory_limit_bytes, memory_limit_scope, memory_enforcement,\ncgroup_memory_peak_bytes, process_peak_rss_bytes, sampled_vm_peak_bytes,\nresident_index_bytes, worker_scratch_bytes, cache_bytes, query_buffer_bytes,\nswap_peak_bytes, memory_events, elapsed_scope, io_backend, direct_io, page_size,\nio_requests_per_query, bytes_read_per_query, metric_availability,\nrun_status, failure_reason, formal_ready')+p('以上为新版输出契约设计，尚需接入。分类开销用于解释总量，RSS 与 cgroup peak 是不同观测值，不能相加或互相替代；未测得字段为 null。预算失败行保留运行状态和证据，不填写伪造的 QPS=0 或 recall=0。'),'设计可审计的内存字段，区分未知和零')
replace(59,p('先在独立 validation 集中调参，再固定配置测 test。05A/05B 保留既定候选/搜索宽度扫描；05C 为每个方法登记官方参数空间与可比调参投入，不能只扫同一个 L 就认定公平。预算扫描允许在各预算内重新选择官方配置，但必须保存完整配置与索引哈希，图注明“各预算调参后的系统表现”；若只改预算、不改任何参数，则另标“固定配置敏感性”，两种解释不混用。'),'明确调参与预算曲线解释')
replace(60,table(['参数','设置'],[
 ['workers','主实验与预算扫描均为 32；线程 scaling 单独分组。'],
 ['内存','磁盘主实验拟 4 GiB；05C 扫描 1/2/4/8 GiB；均使用相同 cgroup 计量。05A resident 单列。'],
 ['05A candidate width','沿用 10..30 step 1；40..100 step 10；140..580 step 40，并记录实际生效候选数。'],
 ['05B search width','沿用现有扫描范围，检查至少满足 k=10 等约束；区分请求值与生效值。'],
 ['05C search 参数','按原生能力在 validation 中选取范围；不把不同方法的 L/ef/beam 当成同一控制变量。'],
 ['05A/05B/05C 模式','payload_on_ssd / hybrid_disk / 原生磁盘路径；另记录准确物理布局与常驻对象。'],
 ['目标 Recall@10','预登记 0.90、0.95、0.99；未达到的目标写未达到，不外推；报告完整实测曲线和实际 recall。']]),'移除旧固定2GiB与强制同搜索宽度')
replace(64,pre('主实验 workers = 32；预算扫描 workers = 32\nformal repeats = 5，repeat_id = {0,1,2,3,4}\ninput/metric/query split verified = true\nnative source, index and config fingerprints verified = true\nconfig frozen before test = true\nmemory_enforcement = cgroup_v2 (disk-backed new protocol)\nmemory.max = declared budget in bytes; memory.swap.max = 0\nresource scope includes search startup, loading, scratch, warmup and queries\nresource peaks/events and actual constraints recorded; no OOM/failure\nsearch wall-clock scope, query count and warmup verified = true\nI/O and cache conditions audited = true\nrequired metrics available; missing optional metrics explicitly null\nformal_ready = true only after all applicable checks pass\n05A resident: unconstrained_reference, io_backend=resident, direct_io=false')+p('memory.max 为内核强制预算，并非“任意瞬时观测绝不超过”的数学保证。保存峰值和 events；出现超额或内存压力事件时核验是否完成回收、是否 OOM、统计范围是否正确，异常点先保持诊断状态。不得仅检查自报的几类字节之和就给出正式验收。'),'约束执行证据取代自报内存和手写formal标记')
replace(66,p('每个 operating point 的 5 次独立运行报告 median 与 IQR；可另外报告方法明确的置信区间。CV>5% 触发环境检查，所有尝试保留，不择优删掉高波动运行。AGNews/GIST 当前 800 条 test query，p99 仅作诊断；只有原生提供且查询样本量足以解释的延迟分位数才进入主表。增加重复 query epoch 可延长吞吐测量，但不会增加独立查询数量，不能据此宣称 p99 代表性提高。MSMARCO 等 extension 的尾延迟需求在运行前另行确定，不能看过 test 后再调整划分。'),'保留五次统计要求，纠正重复epoch等同独立样本')
replace(67,p('未满足新版约束的运行保留诊断用途。旧 single-repeat、旧 RLIMIT_AS、来源/计时/资源不明的结果均不追认。正式结果按新 run ID 完成 5 次测量；输入、图和编码配置未变时可复用核验过的索引。不可运行的方法保留在支持矩阵，其他通过验收的方法可以独立完成实验；只在实际共同支持的数据集、预算和 recall 范围内给出方法间比较。'),'失败不阻塞其他方法，历史索引可复用、性能不可改标签')
replace(69,p('本节保留 revision 36/41/45 至 revision 52 的历史审计，表中的“当前状态”“正在后台运行”均指原记录时点，不作为 2026-09-17 的实时状态。历史 2 GiB、旧五方法和构建命令不覆盖新版第 1—9、11—14 节。此次只复核与新协议有关的代码、目标文件头和已存诊断；未逐项重跑 R01—R16 的所有验证。'),'保留历史审计同时消除过期状态的当前含义')
replace(72,p('历史 R16 发现旧 sift10m 为 BIGANN/SIFT1B 别名，随后按 UCI 数据的固定区间重建。2026-09-17 本次读取 data/sift10m/dataset_manifest.json，并核对到 10M×128 的 base、10,000 条 query 和每 query 1000 个 GT ID 的文件；manifest 声明独立划分与 squared L2 真值。此次未重算全量哈希、GT，也未核验其全部 SAQ/图索引产物，故不再沿用“流水线正在后台运行”作为当前状态，亦不自动升格为 formal。'),'用本次证据更新旧后台任务陈述')
replace(73,p('放行按方法、实验层、配置和预算分别判定。metric 不兼容、布局不支持、预算不足、运行错误、未验收分别记录；文档更新不等于程序能力或正式结果已经具备。'),'避免一个方法不支持即禁止整个数据集')
addition=h('11. 2026-09-17 核验结论与待修复项')
addition+=table(['问题','本次证据','处理 / 状态'],[
 ['内存口径混用','原文称 search-index DRAM，现有 Starling runner 使用 resource.RLIMIT_AS；native_contract 对几类自报字节求和。','方案改为总 cgroup 预算 + 实测峰值 + 分类解释；执行器待接入。'],
 ['入口硬编码 2 GiB','orchestrator.py 拒绝 search_dram_budget_gib!=2.0；native_contract 的完整性检查含 2.0 与旧矩阵。','按显式 protocol/matrix 配置验收；不能只把参数改成 4 就宣称完成。'],
 ['32 线程的实际开销','GIST Starling 每线程坐标缓冲 60 MiB，32 线程合计 1920 MiB；已存诊断峰值 RSS 约 2.01 GiB。','4 GiB 是预检起点；2 GiB 失败有实现资源原因；从 AS 改成 cgroup 也不保证 2 GiB 能跑。'],
 ['Starling 布局限制','R64 在三个目标维度小样本构建失败；GIST R48 完成索引；AGNews/DBpedia 当前 FP32 跨页路径阻塞。','独立标记 unsupported_layout；不改页长、维度或算法补齐。'],
 ['05C 方法过期','revision 52 仍含旧四 baseline；本地已确认原生 DiskANN、AiSAQ、Starling。','更新本篇主方法集合；移植版单列补充。'],
 ['消融并非全部单因素','ours_port.rs 的 locality 要求 coalescing+reuse；resident/no-gate 还需核对 query codec。','固定支持各开关的布局做消融；完整 locality 系统另测。'],
 ['数据大小混用','头部 N/D 与主要表项一致；旧大小列混用了目录和输入文件。','新版统一列 base 文件 GiB，保存头部核验；全量输入哈希运行前再确认。'],
 ['旧通过标记不能复用','官方 CLI 接入与新资源/计时契约不是同一件事。','来源、支持性、资源和计时均通过后才设置 formal_ready。']])
addition+=h('12. 内存预算如何执行与比较')
addition+=p('12.1 三种量分开：常驻索引字节解释算法规模；进程峰值 RSS 解释实际驻留；cgroup memory.peak 解释资源组总占用。VmPeak/虚拟地址空间只作诊断。GiB 固定为 2^30 B，论文中的 GB/MB 保留原单位，不擅自视为完全相同。')
addition+=p('12.2 每次独立 repeat 建立新 cgroup，原生搜索进程在 exec 和任何索引加载之前加入；子进程也在组内。设置 memory.max 为预算字节数、memory.swap.max=0，记录实际层级和父组约束；避免继承旧 2 GiB RLIMIT_AS 形成第二个更紧的限制。观察器放在组外。缺少 memory controller、委派权限或可靠峰值采集时记录 blocked_environment，不静默降级为 RLIMIT_AS 或仅 RSS 采样。')
addition+=p('12.3 总预算包括索引、码本、所有 worker 的队列/visited/LUT/I/O 缓冲、方法缓存、查询输入缓冲、运行时，以及 cgroup 计入的文件缓存和内核内存。GT 评估放到搜索进程外；原生 CLI 若必须加载 GT，则如实计入，不能事后减掉。建索引单独测内存与时间；搜索加载计入内存峰值，QPS 计时从纯查询区间开始。峰值缺少阶段划分时准确标为进程生命周期峰值，不能假称只测查询。')
addition+=p('12.4 memory.peak、memory.current、memory.stat、memory.events 与 swap 记录在每次运行保存；峰值 RSS 同时从原生进程/可靠系统接口获取。若只能采样，字段明确写 sampled，不能冒充无遗漏峰值。原生 CLI 多个搜索宽度复用一个进程时，只能共享该进程生命周期峰值；主实验优先每个 operating point 单独进程，便于对齐内存和性能。')
addition+=p('12.5 固定目标 SSD、CPU/NUMA 与方法运行顺序策略；同一时刻只有一个被测系统，构建、fio 和其他基准不与搜索争用目标盘。预热 query 列表和次数在 validation 上确定并冻结，所有方法同协议；方法内部缓存容量/生命周期允许不同但全部计入预算。O_DIRECT 不能消除设备缓存。涉及 buffered/mmap 时还需核验页缓存归属与预热，否则单列结果；不能假定新建 cgroup 自动清空已存在的系统缓存。')
addition+=table(['实验组','固定条件','变化量','允许的结果'],[
 ['05C 主性能','32 线程；拟 4 GiB；输入、设备、预热和统计协议一致','各方法冻结的官方配置与搜索强度','共同 recall 下的性能和实际用量；支持缺失明确列出。'],
 ['05C 预算扫描','32 线程；同一任务与硬件','1/2/4/8 GiB；各预算仅通过官方参数调优','可运行/预算不足/未达到目标 recall，及实际 QPS、内存、磁盘成本。'],
 ['05A/05B 机制实验','保留原候选或图控制关系；统一新磁盘预算','编码或已披露的机制状态','解释组件贡献，不能冒充全部同图的完整系统比较。'],
 ['线程 scaling（补充）','固定一个预算与同一数据任务','1/4/8/16/32 worker，所有方法同组','单独画并发曲线，不与 w32 预算曲线混合。']])
addition+=p('4 GiB 预检只确认合法输入、正确性、资源与运行可行性，不用测试集上的赢家来选择预算。如果某原生方法仍不适配，记录原因并继续其他方法；8 GiB 保留为已登记扫描点，不为单个方法偷偷放宽主预算。预算内不要求用满内存，不强制相同 PQ 字节、导航比例或额外缓存。')
addition+=p('当前 core 数据的原始向量约为几 GiB，4/8 GiB 预算可能容纳其中一些完整 base。仍需验证被测路径确实按声明从 SSD 读取索引；此时结果只能支持当前数据规模上的磁盘系统表现，不能据此声称数据量必然大于内存或证明十亿规模扩展性。MSMARCO 的 DB1 下界超过本轮最高预算，报告 Ours 的限制，不伪造完整扫描曲线。')
addition+=h('13. 执行顺序、输出和验收')
addition+=table(['步骤','工作','完成标准'],[
 ['1 冻结来源','固定 baseline commit、子模块、编译参数、二进制哈希及实际差异；Ours 保存源码快照。','官方原生/兼容诊断版本明确区分。'],
 ['2 核验输入','base/query/GT 一致；验证向量值、ID、度量和划分；完整哈希写入 run manifest。','每方法读到同一任务；已有测试若用于调参需标探索性或另立未使用测试集。'],
 ['3 复用或导出索引','先核验已有图、码本、布局与参数哈希；变化才重建对应产物。','完整原生路径正确、支持当前维度；无需全部重新建图。'],
 ['4 接入资源控制','外层实现 cgroup launcher、字节预算、环境预检、峰值/events 采集及状态分类。','以独立小程序验证组内执行和限制生效；不修改被测搜索算法。'],
 ['5 更新契约','替换硬编码 2 GiB、旧五方法、隐含缓存/计数要求；新增 protocol_id 与预算矩阵。','缺失可选指标可如实表示，必需资源与计时证据不放宽。'],
 ['6 validation 调参','每方法、每预算登记合理候选范围和计算/建索引投入；达到目标 recall 的配置在测试前冻结。','保留所有尝试和配置，不能逐条测试结果择优回填调参。'],
 ['7 正式运行','每点独立 5 次，方法次序交错/随机；相同预热，隔离后台干扰。','纯搜索批次计时已核验；输入数量和顺序相同；失败记录保留。'],
 ['8 聚合与绘图','按 dataset/protocol/threads/budget/method/config 分组。','中位数与 IQR 溯源到 5 次；各预算不可混聚合；缺失和失败有矩阵。']])
addition+=p('QPS 定义为计时区间内实际完成查询数除以查询批次墙钟时间，排除建索引、加载、预热、GT 评估与结果文件写出。不能把 /usr/bin/time 的整个程序耗时直接当作纯搜索时间；原生计时范围未核验则 throughput_comparable=false。若短查询批次需要重复 epoch，应冻结 epoch 数和缓存策略，在每方法相同条件下测量，并单独注明重复查询负载。')
addition+=p('主要图表：① 各数据集在冻结主预算下的 Recall@10–QPS 曲线；② 目标 recall 下的预算–QPS 曲线与方法可运行矩阵；③ 实际峰值内存与磁盘索引大小；④ 05A/05B 组件证据图。目标 recall 表报告实测 recall 与预登记的容差/选点规则，无法覆盖目标则留空，不外推加速比。p95 仅在原生数据可得时报告；p99/P99.9 和内部计数不能从 batch 均值推造。')
addition+=table(['状态','判定依据','报告方式'],[
 ['passed','来源、输入、正确性、资源、计时与重复验收全部通过','可进入对应正式图表。'],
 ['budget_exceeded','已证明不可避免的驻留下界超限，或实际分配失败/OOM 且有资源证据','报告具体预算/线程/配置；不据一次配置失败宣称该算法全部配置都不可能。'],
 ['unsupported_layout / metric','固定原生版本不支持当前输入布局或度量','保留支持范围说明，不改算法补齐。'],
 ['runtime_error','运行失败但尚无充分内存证据','保存日志与退出码，不能把所有 SIGSEGV 归为 OOM。'],
 ['pending / blocked_environment','尚未完成或控制器/运行环境不足','不进入正式性能比较。']])
addition+=p('此次修订只更新实验文档和核验记录，没有实现 cgroup runner、修改算法、重新建图、启动搜索或升级任何已有结果的 formal 状态。后续先完成步骤 4—5，再运行新预算测量；现有 AS=4 GiB 的 Starling 诊断不能直接作为 cgroup=4 GiB 的正式结果。')
addition+=h('14. 三篇论文与内存限制的依据')
addition+=p('DiskANN 原论文 §3—4 以 64 GB RAM 单机为容量与性能背景，内存保存 PQ 码和缓存，SSD 保存图与原向量；这不是 RLIMIT_AS=64 GB 的证据。Starling §6.1、附录 N 默认每 segment 2 GB 内存/10 GB 磁盘、8 查询线程，调导航图等配置满足预算；原文不足以确认它完整计入线程工作区或用哪种 OS 限额。AiSAQ §4.2 用 /usr/bin/time 测查询峰值内存，仅加载 10 条 query、不加载 GT；约 11—14 MB 不能直接与 32 线程、整批查询配置比较。')
addition+=link('DiskANN 原论文','https://harsha-simhadri.org/pubs/DiskANN19.pdf')
addition+=link('Starling 原论文 §6.1 与附录 N','https://arxiv.org/html/2401.02116v3')
addition+=link('AiSAQ 原论文 §4.2—4.3','https://arxiv.org/html/2404.06004v2')
addition+=p('本方案借鉴固定资源条件和实际内存测量，但 32 线程、4 GiB 及预算扫描属于本项目协议。Linux RLIMIT_AS 限制虚拟地址空间；cgroup v2 memory.max 约束资源组所计入内存，memory.swap.max=0 禁止该组使用 swap。cgroup 用量可包含文件缓存、内核内存，且内核允许某些场景短暂超过 memory.max，所以应同时保存统计与事件，避免将其简化成进程 RSS 的绝对上限。')
addition+=link('Linux getrlimit：RLIMIT_AS 定义','https://man7.org/linux/man-pages/man2/getrlimit.2.html')
addition+=link('Linux cgroup v2 内存控制文档','https://docs.kernel.org/admin-guide/cgroup-v2.html')
addition+=p('本地实现证据：experiments/05_disk_system_fair/orchestrator.py、native_contract.py、run_starling_existing_layout.py、native_rust/src/ours_port.rs；诊断证据 docs/analysis/starling_memory_recovery_20260917/README.md；输入头部核验保存在 docs/analysis/disk_memory_protocol_review_20260917/input_header_check.json。')
ET.fromstring('<fragment>'+addition+'</fragment>')
patches.append({'command':'append','content':addition,'reason':'补充本次核验、内存协议、执行验收与论文依据'})
for i,x in enumerate(patches):
 f=P/f'patch_{i:02d}.xml';f.write_text(x.pop('content'));x['file']=f.name
(P/'patches.json').write_text(json.dumps(patches,ensure_ascii=False,indent=2))
result='\n'.join(changes.get(i,ET.tostring(e,encoding='unicode')) for i,e in enumerate(old))+'\n'+addition
(P/'planned.xml').write_text(result)
print('Prepared',len(patches),'targeted operations; base revision',before['revision_id'])
