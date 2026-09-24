# Ours 磁盘版优化方案：结合 SymphonyQG 与 Starling

## 0. 任务目标与边界

本文档用于指导 Codex 阅读当前 Ours 代码、SymphonyQG 与 Starling 的论文及公开实现，并在 **不改变 Ours 核心量化设定** 的前提下设计和实现磁盘场景优化。

当前 Ours 的核心设定必须先确认并保持一致：

- Query：沿用当前实现的 query codec 与距离计算路径，不在本方案中改动计算精度。
- Database：`primary 4bit + residual 4bit`。
- 图：Vamana。
- 搜索目标：在 SSD 场景下减少 I/O、减少 I/O round、提高 page 利用率，同时维持高 Recall。
- 当前已有或应重点检查的机制包括：`beam_width`、`batch_prefetch`、异步/O_DIRECT 路径、短记录或 route code、primary/residual 距离计算、候选队列、缓存、I/O 统计。

精细评分使用 `primary 4bit + residual 4bit`，不新增原始向量重排路径。

本方案不引入小型内存导航图；优化范围限定为量化路由、residual 策略、磁盘布局、页调度与 I/O 流水线。

---

# 1. 先做代码审计，不要直接修改

Codex 在开始改代码前，先完成一份“当前实现事实表”。必须从代码本身确认，不允许根据本文档猜测。

重点回答下面的问题：

1. primary 4bit 当前存在哪里：内存、SSD、还是两者都有？
2. residual 4bit 当前存在哪里：和 primary 共页、独立 sidecar、还是内存？
3. Vamana 的 adjacency 存在哪里？一个节点的磁盘 record 具体包含什么？
4. SSD 的最小实际读取单位是多少？逻辑 page 是否固定为 4 KiB？
5. `beam_width` 当前控制的是候选节点数、并发 I/O 数，还是两者混在一起？
6. `batch_prefetch` 当前到底是在发真实异步 I/O，还是只做 CPU prefetch / read-ahead？
7. 当前是否存在 `loaded page cache`、`inflight page set`、page-level 去重？
8. 当前 candidate queue 的排序键是什么？只用 primary 4bit，还是已经混入 residual？
9. residual 是对所有 candidate 都算，还是仅对部分 candidate 算？
10. 当前 `total_full_distance_count` 的“full”到底指什么。明确它对应 primary 评分还是 residual 精化，并在文档和日志中使用准确命名。
11. 当前索引文件格式是否允许调整物理 node order 而保持 logical node ID 不变？
12. 当前搜索阶段是否能从 node id O(1) 得到 page id？
13. 当前是否已经使用 `O_DIRECT/libaio/io_uring` 中的某一种；队列深度与 completion 处理在哪里？
14. 当前 cache 的单位是 node、page，还是 byte range？
15. primary score 是否是全局确定的：同一个节点 `v` 无论从哪个 parent 被发现，`d4(q,v)` 都一样？

建议在当前仓库使用 `rg` 定位这些关键词和符号：

`beam_width`、`batch_prefetch`、`prefetch_issued`、`total_full_distance_count`、`shortCodeIpFromBits`、`encodeShortRecord`、`query_distance_lower_bound_from_short_record`、`O_DIRECT`、`libaio`、`io_uring`、`pread`、`page`、`sector`、`cache`、`residual`、`route`、`candidate`、`visited`、`inflight`。

审计完成前不要做结构性修改。

---

# 2. SymphonyQG：要读什么

论文重点不是全部阅读，而是针对 Ours 的三个问题：

- 量化距离如何直接参与 graph navigation；
- 如何把量化数据布局与 graph expansion 对齐；
- 量化误差怎样影响搜索路径。

论文优先阅读：

- §3.1.1：Searching on Graphs with FastScan and RaBitQ。
- §3.1.2：implicit re-ranking 与 multiple estimated distances。
- Figure 2：数据布局与访问模式。
- §3.2.2：graph refinement 与 FastScan batch 对齐。
- Figure 3：补边使 degree 与 batch size 对齐。
- §4.2.3：multiple estimates 和 graph refinement 的 ablation。

从论文中真正需要迁移的思想是：

1. **cheap approximate score 直接负责图搜索导航**；
2. **数据布局要和搜索时的访问模式对齐**；
3. **batch 机制不能只是算得快，图和数据布局也应尽量让 batch 被充分使用**；
4. 量化误差可能改变图搜索路径，因此应该特别关注 ranking flip，而不是只看平均量化误差。

不要直接迁移：

- raw vector implicit re-ranking；
- 原始向量 final rerank；
- neighbor-side duplicated code 的大内存方案；
- multiple estimated distances，除非 Ours 中同一个节点从不同 parent 被发现时确实会产生不同 estimate。

---

# 3. SymphonyQG：要读哪些代码

优先看论文对应的旧实现仓库：

`https://github.com/gouyt13/SymphonyQG`

README 已确认核心目录：

- `symqglib/index/qg/`：quantized graph 核心。
- `symqglib/index/fastscan/`：FastScan helper/kernel。
- `reproduce/`：实验入口与参数。
- `python/`：只用于理解 API，不是重点。

阅读顺序：

第一步先进入 `symqglib/index/qg/`，定位以下逻辑：

- search 主循环；
- beam/candidate 数据结构；
- visited 判断；
- 邻居展开；
- approximate distance 计算入口；
- exact/raw distance 在什么时机计算；
- 同一节点允许被多次加入 beam 的实现；
- graph build/refinement；
- degree 补齐与 adaptive pruning；
- 最终 index data layout。

第二步读 `symqglib/index/fastscan/`：

- query LUT 在哪里构造；
- 一次 batch 的大小；
- neighbor code 如何连续存储；
- SIMD scan 的输入布局；
- 为什么 degree 需要是 batch size 的倍数。

第三步读 `reproduce/`：

- `EF`、`R`、`K` 等参数如何映射到实现；
- ablation 是如何关闭 multiple estimates 或 graph refinement 的。

此外，旧 SymphonyQG 仓库 README 已说明该实现后来合入 RaBitQ-Library。建议同时阅读当前版本：

`https://github.com/VectorDB-NTU/RaBitQ-Library`

当前版本中优先检查：

- `include/rabitqlib/index/symqg/qg.hpp`
- `include/rabitqlib/index/symqg/qg_builder.hpp`
- `sample/cpp/symqg_indexing.cpp`
- `include/rabitqlib/quantization/rabitq.hpp`
- `include/rabitqlib/quantization/rabitq_impl.hpp`
- `include/rabitqlib/quantization/data_layout.hpp`
- `include/rabitqlib/quantization/pack_excode.hpp`
- `include/rabitqlib/index/query.hpp`
- `include/rabitqlib/index/estimator.hpp`
- `src/simd/fastscan_avx2.cpp`
- `src/simd/fastscan_avx512.cpp`

如果路径在当前版本有变化，不要猜，使用 `find` / `rg` 搜索 `QuantizedGraph`、`QGBuilder`、`FastScan`、`estimator`、`multiple`、`beam`、`visited` 等符号。

Codex 阅读这些代码时，要输出一张映射表：

`论文机制 -> 具体函数/类 -> 数据结构 -> 对 Ours 是否可迁移`

---

# 4. Starling：要读什么

Starling 对 Ours 的价值主要在 SSD I/O，而不是量化本身。

论文优先阅读：

- §3.1 I/O-efficiency Analysis：两个根问题是 poor data locality 和 long search path。
- §3.2 Framework Overview：disk layout、block search。
- §4.1 Block Shuffling：为什么要让图上相关节点同页。
- §5.1 Block Search：block pruning、I/O-computation pipeline、PQ-based approximate distance。
- Algorithm 2：candidate set、block read、pipeline 的完整流程。
- §6.3 I/O-efficiency：vertex utilization ratio 与 search path length。
- §6.5 Ablation：block shuffling、pruning、pipeline、PQ distance。
- §7 Discussion 中关于 modern SSD 多 block 并发读取与 round-trip 的讨论。

对 Ours 最重要的 Starling 思想：

1. 搜索单位不能只看 node，还要看 **page/block**。
2. 一次 page read 已经发生后，应尽量利用 page 中其他有价值的数据。
3. 物理 node order 应尽量与 graph topology / 实际访问局部性对齐。
4. approximate score 应在发 SSD I/O 之前帮助筛选 candidate。
5. 多个随机 block 可以并发发出，要优化 I/O rounds，不只是 I/O count。
6. SSD read 与 CPU distance computation 应 pipeline 化。

---

# 5. Starling：要读哪些代码

公开仓库：

`https://github.com/zilliztech/starling`

README 已确认仓库具有：

- disk graph build；
- Graph Partition；
- Beam Search；
- Page Search；
- `use_ratio`；
- SQ；
- `libaio` 依赖。

优先阅读目录：

- `include/`
- `src/`
- `graph_partition/`
- `scripts/`

由于 Starling 的公开仓库是从 DiskANN 派生，且论文中的 “block shuffling / BNF” 与仓库中 “Graph Partition” 的代码对应关系需要实际核验，**不要假定某个文件就是论文 BNF 的完整实现**。先以符号搜索确定真实调用链。

进入仓库后先运行：

- `rg "Page Search|page_search|page search" include src tests`
- `rg "Beam Search|beam_search|cached_beam_search" include src tests`
- `rg "use_ratio" include src tests scripts`
- `rg "PQFlashIndex|pq_flash" include src`
- `rg "AlignedFileReader|LinuxAlignedFileReader|libaio|io_submit|io_getevents" include src`
- `rg "graph_partition|partition|shuffle|reorder|mapping" .`
- `rg "cache|cached nodes|hot" include src`
- `rg "block|sector|page" include src`

重点追踪 4 条调用链：

1. **Page Search 主循环**：candidate 如何选择、一个 block 读回来后哪些节点被处理。
2. **I/O 提交与 completion**：一轮能同时提交多少 block，下一轮何时开始。
3. **approximate score**：PQ/SQ 在什么时候参与下一次磁盘读取决策。
4. **graph partition / reorder**：logical node id 和 physical block id 如何映射。

同时阅读 `scripts/`，确认论文中 Beam Search / Page Search / Graph Partition 的实际运行参数。

Codex 必须输出：

`论文中的 block shuffling / block search / pipeline -> 仓库实际代码位置 -> 是否与论文描述完全一致`

如果公开实现缺失某一论文算法，直接标明“paper-only / repo mapping unclear”，不要自行补全成作者实现。

---

# 6. 两篇论文与 Ours 的正确对应关系

可以把三者统一成下面的逻辑。

SymphonyQG 主要解决：

`如何用便宜但有误差的量化距离高效地决定图搜索方向`

Starling 主要解决：

`图已经放到 SSD 后，如何减少随机 I/O、减少 I/O round、提高一次 page read 的有效利用率`

Ours 可以把两者结合成：

`现有 query codec + primary 4bit routing + adaptive residual refinement + page-aware Vamana + async beam I/O`

也就是：

`4bit 负责“该往哪里走”`

`residual 负责“4bit 判断不清时修正排序”`

`page-aware layout/search 负责“每次 SSD 读回来尽量多一点有用内容”`

`beam + async I/O 负责“减少等待轮数并隐藏 I/O latency”`

---

# 7. 优化一：Primary 4bit 只负责路由

## 当前目标

把 primary 4bit 明确变成 route score，而不是默认所有 candidate 都进入 4+4bit。

对于 query `q` 和节点 `v`：

`d4(q,v)` = primary 4bit estimate

Candidate queue 默认按 `d4` 排序。

其职责是：

- 判断哪个 candidate 最值得扩展；
- 决定下一批要读取哪些 page；
- 在 residual 未使用时尽量完成大部分粗筛。

## 实现要求

先检查当前 primary code 是否全量常驻内存。

如果 primary 常驻内存，则所有新 neighbor 在不产生额外 SSD I/O 的情况下先计算 `d4`。

如果 primary 不常驻内存，则要先评估把 primary route code 常驻内存的预算：

`N × primary_code_size + per-vector metadata`

不要直接实现，先计算内存成本。

## 验证指标

新增：

- `primary_distance_count/query`
- `candidates_generated/query`
- `candidates_pruned_by_primary/query`

同时保留：

- Recall@K
- QPS / latency
- unique page reads/query
- I/O rounds/query

---

# 8. 优化二：Adaptive 4bit -> 4+4bit residual refinement

这是 Ours 最有方法特色的一层。

当前不希望：

`所有 candidate -> primary + residual`

目标改成：

`所有 candidate -> primary`

只有无法可靠判断去留的 candidate：

`primary -> residual`

最终得到：

`d4+4(q,v)`

## 第一版：margin-based refinement

先定义当前 candidate acceptance boundary `tau`。

`tau` 不能凭空定义。Codex 必须根据当前搜索队列语义选择：

- 如果 queue 固定容量为 `L`，可用当前第 `L` 名的 score；
- 如果有 separate frontier/result bound，则使用当前实现真正控制 candidate 接受/终止的 bound。

第一版规则：

- `d4 < tau - Delta`：直接保留为 route candidate。
- `d4 > tau + Delta`：直接 prune。
- `|d4 - tau| <= Delta`：计算 residual，得到 `d4+4` 后重新判断。

`Delta` 不要手拍。

先在 validation set 上收集：

- `d4`
- `d4+4`
- `|d4+4 - d4|`
- candidate 是否因 residual 发生排序翻转
- candidate 与当前 `tau` 的 margin

然后用 quantile 或目标误判率来选 `Delta`。

## 第二版：bound/confidence-based refinement

如果当前 ExRaBitQ 已经有合法的 lower/upper bound，或者可以通过已有 metadata 在不读取 residual 的情况下得到置信区间：

`LB4(v) <= true/refined score <= UB4(v)`

则优先使用：

- `LB4(v) > tau`：安全 prune。
- 区间完全优于当前边界：直接保留。
- 区间和 `tau` 重叠：才读取 residual。

如果计算 bound 本身需要先读取 residual，则失去该设计的意义。

## 一个关键判断

如果 residual 和 graph/node record 已经在同一个 SSD page 中一起被读取，那么 adaptive residual 主要节省 **CPU computation**，不一定节省 I/O。

如果 residual 是独立 sidecar / 独立 page，adaptive residual 才能同时减少：

- residual computation；
- residual page reads。

因此 Codex 在实现前必须画出实际 data layout，并明确这一优化到底省 CPU、I/O，还是两者都省。

## 新增指标

- `residual_refine_count/query`
- `residual_refine_rate = residual_refine_count / primary_distance_count`
- `ranking_flip_count/query`
- `residual_page_reads/query`，仅当 residual 独立驻盘时
- Recall / QPS / latency

---

# 9. 优化三：Page-oriented beam scheduler

这是最先建议实现的 SSD 优化，因为它不改变量化公式。

当前 beam 如果直接以 node 为 I/O 单位：

`A, B, C, D -> 4 requests`

但如果：

`page(A)=page(B)=page(C)=P1`

`page(D)=P2`

真正需要的是：

`P1, P2 -> 2 requests`

## 具体实现

Candidate queue 仍然是 node-level，因为排序需要 node score。

在准备发 I/O 时做：

`node id -> page id`

然后：

1. 按 route score 取前若干 candidate。
2. 转成 page id。
3. page-level dedup。
4. 跳过已经在 `loaded_pages` 中的 page。
5. 跳过已经在 `inflight_pages` 中的 page。
6. 直到凑够 `beam_width` 个 **unique pages** 或 candidate 不足。
7. 批量 submit。

建议明确区分：

- `node_beam_width`
- `io_beam_width` / `page_beam_width`

不要继续让一个 `beam_width` 同时表达“候选宽度”和“I/O 并发深度”。

## 需要的数据结构

- `visited_nodes`
- `loaded_pages`
- `inflight_pages`
- `page_cache`
- `page_id -> nodes/records` mapping
- I/O completion queue

## 新增指标

- `page_requests_total/query`
- `unique_page_reads/query`
- `duplicate_page_requests_avoided/query`
- `pages_submitted_per_round`
- `io_rounds/query`
- `page_cache_hits/query`

---

# 10. 优化四：I/O 与计算真正 pipeline 化

不要做：

`read page -> wait -> compute -> read next page`

目标是：

`SSD 正在读 round t+1`

同时：

`CPU 正在处理 round t`

也就是让 I/O 和 CPU overlap。

## 实现原则

把搜索循环拆成：

- submit stage；
- compute stage；
- completion stage。

只要有未完成 I/O，就不要让 CPU 无意义等待；只要有可计算的已完成 page，就尽量消费。

若当前使用 libaio：

重点检查 `io_submit`、completion polling、request batch size、queue depth。

若当前仍是同步 `pread`：

先不要同时改 layout 和 residual，先单独实现异步 pipeline，做 ablation。

## 新增指标

- `io_wait_time/query`
- `compute_time/query`
- `overlapped_time/query`
- `io_queue_depth_avg`
- `io_rounds/query`
- p50/p95/p99 latency

---

# 11. 优化五：Graph-aware physical node reordering

这是从 Starling 最值得迁移的 offline 优化。

第一版不要修改 Vamana topology。

保持：

`logical node id`
`neighbor list`
`graph edges`

全部不变。

只修改：

`logical node id -> physical page position`

目标：

让搜索中容易共同访问、或图上直接相邻的节点尽量处于相同/相邻 page。

## 第一版建议

不要一上来复刻 Starling 的 BNF/BNS。

先实现一个简单可验证的 greedy layout：

1. 选择一个尚未布局的 seed node。
2. 把它放入新 page。
3. 按某种 priority 从它的邻居中填满 page。
4. 已布局节点跳过。
5. page 满后开始下一 page。
6. 建立 `node_id -> page_id, slot` 映射。

priority 可以先用：

- direct graph neighbor；
- edge rank；
- build-time neighbor distance；
- 可选的离线 co-visit frequency。

第一版优先使用 graph topology，避免先引入 query workload dependency。

## 第二版

如果第一版有效，再实现类似 Starling 的 iterative improvement：

将节点移动到“包含最多自身邻居”的 page，或进行局部 swap，以提高 graph-locality score。

## 必须保持

- logical node id 不变；
- ground truth / graph topology 不变；
- Recall 不应因为纯 physical reorder 自身变化；
- index format 有版本字段或明确兼容策略。

## 建议指标

定义：

`page_neighbor_overlap(v) = 同一 page 中属于 N(v) 的节点数 / (page 内其他节点数)`

全局取平均。

同时运行时统计：

`page_utilization = 真正被搜索使用的 loaded nodes / 所有从 SSD 带回的 nodes`

以及：

- unique pages/query
- I/O rounds/query
- QPS
- latency

---

# 12. 优化六：Page-aware opportunistic search

仅有 physical reorder 还没有完全利用 page locality。

假设因为 candidate `v1` 读取了 page：

`P = {v1, v2, v3, v4, ...}`

如果 page 中其他 node 的数据已经免费进入内存，就应该判断它们是否值得加入搜索，而不是只处理 `v1`。

但这里不能机械照搬 Starling。

Codex 要先确认每个 page 内实际有什么：

- 只有 adjacency？
- 有 residual？
- 有 primary code？
- 有 node metadata？
- 是否能在不额外随机读的情况下得到 `d4(q,vi)`？

如果 primary code 已全量常驻内存，那么 page 读回来后可以对 page 内节点做便宜的 `d4` 评分。

建议第一版：

- target node 一定正常 expand；
- page 内其他 nodes 只做 `d4` opportunistic scoring；
- 只允许 top-r 或低于阈值的少量 node 进入 candidate queue；
- 不直接 expand page 内所有节点。

这对应 Starling 的 block pruning 思想，但 Ours 的筛选距离换成 primary 4bit。

需要一个参数：

`page_use_ratio` 或 `page_extra_candidates`

不要固定复刻 Starling 的 `sigma=0.3`，必须在自己的 validation set 上调。

新增指标：

- `extra_nodes_scored_from_loaded_pages/query`
- `extra_nodes_admitted/query`
- `page_utilization`
- 由 page opportunistic 引入的额外 CPU time
- unique pages/query 的下降量

---

# 13. 固定总内存预算

所有优化必须在同一进程总内存预算下比较：

`B_total = B_route + B_metadata + B_cache + B_workspace + B_other`

- `B_route`：实际常驻的路由编码，包含保留的 DB1 sidecar 或新增的 primary code，避免漏算或重复计账。
- `B_metadata`：scale/norm、逻辑 ID 与物理槽位映射等元数据。
- `B_cache`：共享 page cache 与热点记录缓存。
- `B_workspace`：各 worker 的查询缓存、候选队列、在途 I/O 缓冲区等工作区。
- `B_other`：其余进程开销。

新增 primary 驻留、页映射或异步缓冲区时，必须重新核算缓存可用额度和进程峰值内存，并与同预算下的现有缓存策略比较。

---

# 14. 暂时不要做的优化

## 14.1 保持现有计算精度与量化路径

沿用当前 query codec，数据库使用 `primary 4bit + residual 4bit`，最终 refined score 是 `4+4bit`。

本方案不改 query 计算精度，不引入原始向量重排。

## 14.2 不要直接做 multiple estimated distances

SymphonyQG 中，同一个节点从不同 parent 被发现时可能得到不同的 neighbor-side quantization code / estimate，因此 multiple estimates 有意义。

如果 Ours 当前对每个节点保存的是全局唯一：

`primary_code(v)`

则：

`d4_A(q,v) = d4_B(q,v)`

从不同 parent 发现 `v` 还是同一个 score，多次插入没有信息增益。

只有未来引入：

- parent-relative code；
- edge-specific correction；
- local residual；
- 其他 path-dependent estimate；

才值得重新考虑 multiple estimates。

## 14.3 不要一开始修改 Vamana pruning rule

先验证：

- page scheduler；
- pipeline；
- physical reorder；
- adaptive residual；

这些在不改 graph topology 的情况下是否已经有效。

只有物理 locality 仍然不足时，才考虑 page-aware graph construction/pruning。

---

# 15. 建议开发顺序

## Phase 0：事实审计与 instrumentation

不改算法。

补齐：

- primary count
- residual count
- node -> page mapping
- page requests
- unique page reads
- duplicate page avoided
- I/O rounds
- pages/round
- cache hit
- I/O wait time
- compute time
- ranking flips

输出当前 baseline。

## Phase 1：Page-oriented beam + page dedup

只改 I/O scheduler。

验证：

`相同 Recall 下 unique pages/query 和 I/O rounds/query 是否下降`

## Phase 2：Async I/O-compute pipeline

不改量化。

验证：

`相同 page reads 下 latency / QPS 是否改善`

## Phase 3：Graph-aware physical reordering

不改 Vamana topology。

验证：

`page overlap ↑ -> page utilization ↑ -> unique pages/query ↓`

## Phase 4：Page-aware opportunistic search

利用已经读取的 page 中的额外节点。

验证：

`额外 CPU 成本是否换来更少 I/O`

## Phase 5：Adaptive residual

实现：

`4bit -> uncertain only -> 4+4bit`

验证：

`residual_refine_rate 显著下降且 Recall 基本保持`

如果 residual 单独驻盘，再验证：

`residual_page_reads/query ↓`

---

# 16. 推荐的主线结构

如果前五个阶段成立，Ours 的最终搜索流程应是：

`Query（沿用现有 query codec）`

-> 从 entry point 开始维护 `Q_route`

-> 所有新 candidate 先用 primary 4bit 得到 `d4`

-> 按 node score 排序，但提交 I/O 前转成 page id 并去重

-> 一次异步读取多个 unique pages

-> CPU 同时处理上一批完成的 pages

-> target node 正常展开

-> 已读取 page 中其他节点可被 opportunistically 用 `d4` 筛选

-> 对接近当前决策边界的 candidate 才做 residual refinement

-> refined candidate 用 `d4+4` 更新排序

-> 重复直到现有搜索终止条件满足

整个过程沿用现有 query 计算路径，精细评分使用数据库端的 4+4bit 编码。

---

# 17. 实验矩阵

正式实验必须逐层 ablation，不要把所有优化一次性打开。

建议至少包含：

`O0 = Current Ours`

`O1 = O0 + page dedup / page-oriented beam`

`O2 = O1 + async I/O-compute pipeline`

`O3 = O2 + graph-aware physical reorder`

`O4 = O3 + page-aware opportunistic search`

`O5 = O4 + adaptive residual`

每一项画：

- Recall-QPS
- Recall-latency
- Recall-unique pages/query
- Recall-I/O rounds/query

补充表格：

- page utilization
- residual refinement rate
- cache hit rate
- average pages/round
- I/O wait fraction
- index size
- DRAM usage

必须在相同：

- hardware；
- NVMe；
- `O_DIRECT`；
- dataset；
- graph parameters；
- memory budget；
- thread count；

下比较。

---

# 18. Codex 最终需要交付的内容

在真正实现前，先提交一份设计报告，包含：

1. Ours 当前真实 data layout。
2. Ours 当前 search call graph。
3. primary / residual 具体调用链。
4. node -> SSD offset / page id 的计算方式。
5. I/O submit / completion 调用链。
6. cache 与 prefetch 当前真实语义。
7. SymphonyQG 论文机制到代码的映射。
8. Starling 论文机制到代码的映射。
9. 上述每个优化应修改 Ours 的哪些文件/函数。
10. 哪些优化只省 CPU，哪些真正省 I/O。
11. 每一阶段预计新增哪些统计指标。
12. 不确定或无法从代码确认的地方必须明确列出，不能猜。

报告确认后，再按照 Phase 1 -> Phase 5 的顺序逐阶段实现。

每完成一阶段：

- 单独 commit；
- 跑 correctness test；
- 跑小数据 smoke test；
- 跑同参数 baseline；
- 输出指标差异；
- Recall 明显下降时先停止，不继续叠加后续优化。

---

# 19. 最核心的一句话

本项目的目标不是简单把 SymphonyQG 或 Starling 搬进 Ours。

真正要形成的是：

**用 primary 4bit 决定“该读谁”，用 residual 只修正“不确定的谁”，用 page-aware layout/search 提高“每次读回来多少有用”，再用 beam + async I/O 减少“要等多少轮”。**

这四件事必须分别做 ablation，才能说明性能提升到底来自量化、布局、I/O 调度还是并发。
