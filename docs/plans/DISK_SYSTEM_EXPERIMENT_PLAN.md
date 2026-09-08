# 05 磁盘系统公平实验完整方案

## 1. 目标与核心结论

当前 `01_quantizer_fair`、`02_diskann_fair` 和 `03_system_fair` 都以数据或索引驻留内存为主。磁盘场景不能只补第三组端到端系统实验，否则无法回答性能变化究竟来自量化载荷、共享图遍历还是完整系统设计。因此，本方案将磁盘评估组织为 05A、05B、05C 三层，在不改变数据集、查询划分和核心距离数学的前提下迁移到独占本地 NVMe，回答以下问题：

1. 固定候选不变时，不同 4-bit 编码的载荷大小、随机页面读取、解码计算和距离误差分别是多少。
2. 共享同一 Vamana 图和搜索循环时，DB1 初筛能否减少 full-4bit 页面读取，并在相同 recall 下降低尾延迟。
3. 在标准混合磁盘驻留模式下，完整系统的 Recall@10、单请求延迟和并发吞吐如何变化。
4. Ours 的 `DB=1bit + INT8 query` 初筛能否在机制实验和端到端实验中同时减少 NVMe I/O。
5. 性能变化来自编码密度、计算、图访问、I/O 合并还是缓存命中率。

完整磁盘实验包含三层：

- `05A_disk_quantizer_io`：对应实验 01，固定候选量化与磁盘载荷微基准。
- `05B_diskann_shared_graph`：对应实验 02，共享 Vamana 图和统一搜索循环的磁盘机制实验。
- `05C_disk_system_fair`：对应实验 03，完整系统端到端磁盘对比。

其中 05C 正式端到端实验包含五个系统：

- `Ours-Disk`
- `SymphonyQG-DiskPort`
- `OG-LVQ-DiskPort`
- `Glass-NSG-DiskPort`
- `DiskANN-PQ-Disk`

前四个系统必须明确标记为“算法保持的磁盘移植版”，不能写成官方实现原生提供的磁盘模式。`DiskANN-PQ-Disk` 使用官方磁盘实现，作为标准磁盘检索参照。

实验代码放在：

```text
quantization_graph/experiments/05_disk_system_fair/
```

正式依赖源码和本地 binding 统一放在：

```text
quantization_graph/baselines/faiss/
quantization_graph/baselines/saq/
quantization_graph/baselines/svs/
quantization_graph/baselines/pyglass/
quantization_graph/baselines/symphonyqg/
quantization_graph/baselines/diskann/
```

版本由 `quantization_graph/baselines/DEPENDENCY_LOCK.json` 固定。正式入口不得
自动读取相邻的 `quantized_hnsw`、独立的 `SymphonyQG` checkout 或 `/tmp` 下的
binding。大规模生成数据和索引不放进源码目录：原始数据仍在
`quantization_graph/data/`，中间结果在 `quantization_graph/work/`，05 正式磁盘
索引写到显式指定的 `--disk-root`。

实验结果放在：

```text
quantization_graph/results/05_disk_system_fair/
```

现有 `03_system_fair`、`04_query_codec_1bit_scan`、Fig. 5 和 Fig. 6 不得覆盖。

## 2. 三层磁盘证据链与重测范围

### 2.1 是否需要重测前两个实验

结论是：前两个实验都需要在最终 NVMe 机器上重新运行，但实验 01 不能简单改成“所有量化码都从 SSD 读取”并替代原结果。

| 原实验 | 原研究问题 | 磁盘方案 | 原因 |
|---|---|---|---|
| 01 quantizer fair | 固定候选上的量化误差和距离计算速度 | 重跑 resident-code 参考，并新增 payload-on-SSD 微基准 | 标准混合磁盘搜索仍会把紧凑导航码放在 DRAM；SSD 模式是额外的载荷/I/O 分析，不应替代原量化计算结论 |
| 02 DiskANN fair | 共享图和搜索循环下比较不同 4-bit payload | 必须重跑共享图磁盘版 | 这是隔离 DB1 初筛、页面读取和图 I/O 的核心机制实验 |
| 03 system fair | 不同完整系统端到端比较 | 必须运行五系统磁盘版 | 用于验证机制收益能否转化为真实端到端收益 |

三层实验必须在同一台独占 NVMe 机器、同一编译配置和同一查询划分上运行。01/02 的旧内存结果仍然保留；新的 resident 参考需要重跑，是因为最终 NVMe 机器的 CPU、NUMA 和编译环境可能与旧结果不同，不能直接混用旧 QPS。

### 2.2 05A：固定候选磁盘量化与载荷微基准

研究问题：在候选 ID 完全相同时，量化误差、距离计算、磁盘页面读取和编码密度分别造成多少开销。

方法集合保持实验 01 当前正式方法不变：

- `PQ_4bit`
- `SQ_4bit`
- `SAQ_B4`
- `Ours_RaBitQ_K1`

每个方法运行两个明确分离的 storage mode：

1. `resident`：量化码全部驻留 DRAM，重测原固定候选距离质量和纯计算速度。
2. `payload_on_ssd`：量化载荷按方法原编码格式写入 4 KiB 页面；仅码本、scale 和查询元数据驻留内存，候选页面通过 `O_DIRECT` 读取，`C=0`。

两个 mode 使用完全相同的：

- query IDs
- fixed candidate IDs 和候选顺序
- exact float32 L2 参考排序
- `k=10`
- candidate-size/search-width 网格
- query preprocessing 和量化参数

磁盘读取可以按页号排序、合并和去重，但距离结果必须重新映射回原候选 ID；不得改变候选集合或压缩距离排序。05A 不进行图遍历，因此其 Recall@10 仍然表示固定候选集合内部的 top-10 overlap，不能表述为 ANN graph recall。

05A 主要指标：

- fixed-candidate Recall@10
- mean/P95 relative distance error
- pairwise flip rate
- resident distance-compute QPS
- payload-on-SSD mean/p95/p99 latency 和 QPS
- code bytes/vector 和 effective bits/dim
- 4 KiB pages/query、bytes/query 和 read amplification
- I/O wait、decode compute、query preparation 分解
- 合并前/后的 I/O 请求数

05A 的 `payload_on_ssd` 是物理载荷微基准，不代表标准线上系统一定把导航码放在 SSD；论文中必须与 `resident` 模式分开标注。

### 2.3 05B：实验 02 的磁盘复现与机制实验

研究问题：把实验 02 的四种正式方法原样迁移到磁盘后，payload 距离、DB1 gate 和页面访问分别产生什么影响。PQ/SQ/SAQ 继续共享 float32 Vamana 图；Ours 因为使用 ExRaBitQ4 对称距离构图，必须保留自己的图，不能强制替换成 baseline shared graph。

方法集合保持实验 02 当前真实 adapter：

- `PQ-DiskANN-Disk`
- `SQ-DiskANN-Disk`
- `SAQ-DiskANN-Disk`
- `Ours-Disk`

统一条件：

- PQ/SQ/SAQ 使用同一份 float32 构建的 Vamana adjacency。
- Ours 使用实验 02/03 共用的正式 ExRaBitQ4-symmetric Vamana 图和正式搜索路径。
- `R=64, L_build=400, alpha=1.2, seed=20260813`。
- PQ/SQ/SAQ 必须记录相同 shared-graph SHA-256；Ours 单独记录自己的 source-graph SHA-256 和 `graph_role=ours_native`，不得冒充 shared graph。
- 四个方法统一使用 4 KiB direct-I/O、DRAM 预算、查询划分和测量口径，但各自保留实验 02 的构图距离、搜索循环、visited/frontier 语义和量化载荷。
- `B=2 GiB`，实际 resident-code bytes 和缓存节点数必须报告。

05B 与 05C 中的 Ours 是同一个方法。二者在 `M=64` 配置下应复用同一份 Ours 图、INT8 query、DB1 gate、full4/residual 距离和 paper-prune 搜索语义；区别只在于 05B 同时提供磁盘机制消融，而 05C 将完整 Ours 与其他完整系统比较。

Ours 必须提供以下消融：

1. `full4-resident/no-gate`：4-bit 导航码驻留，关闭 DB1 gate。
2. `db1-resident/full4-on-ssd`：仅 DB1 和 factor 驻留，通过者读取剩余 3 bit/full4。
3. `db1+coalescing`：在第 2 项基础上启用跨候选页排序、合并和重复页消除。
4. `db1+coalescing+reuse`：继续复用已读取页面进行后续展开和 rerank，作为完整机制版本。

05B 主要指标：

- graph Recall@10–p95 latency
- graph Recall@10–QPS
- visited nodes 和 distance evaluations/query
- adjacency pages/query
- payload pages/query
- total bytes/query 和 I/O requests/query
- DB1 reject/survivor ratio
- page coalescing ratio 和 query-local reuse rate
- I/O wait、distance compute、frontier update 和 rerank 分解

05B 是证明“为什么有效”的主实验；只有它显示 DB1 gate 确实减少页面读取，05C 的端到端提升才能归因于该设计。

### 2.4 05C：五系统端到端磁盘实验

05C 使用各方法自己的正式图、正式量化格式和正式搜索语义，比较 `Ours-Disk`、`SymphonyQG-DiskPort`、`OG-LVQ-DiskPort`、`Glass-NSG-DiskPort` 和 `DiskANN-PQ-Disk`。

05C 回答“整个系统是否更快”，不能单独用于证明具体优化机制。其详细存储布局、移植方法、参数和验收规则见后续章节。

### 2.5 三层结果的逻辑关系

论文中的证据顺序固定为：

```text
05A：量化质量和物理载荷成本
  -> 05B：固定图下的页面过滤与搜索机制
    -> 05C：完整系统端到端收益
```

如果 05A 的编码更小但 05B 没有减少 pages/query，说明页面布局或访问随机性抵消了编码优势；如果 05B 减少 I/O 但 05C 没有提升，说明瓶颈转移到了系统调度、缓存、图质量或 rerank。三层结果必须一起解释，不能只挑有利的一层。

## 3. 公平性定义

### 3.1 数据集和查询

复用当前正式实验的三个数据集：

- AGNews
- GIST
- DBpedia

固定以下设置：

- `k=10`
- 查询文件和 ground truth 文件不变
- validation/test 划分不变
- 随机种子不变
- validation 只用于选择搜索参数
- test 只用于生成最终结果

执行前记录所有数据文件、查询划分文件和源索引的文件大小与 SHA-256，避免磁盘实验与内存实验使用了不同输入。

### 3.2 图和算法配置

05C 复用当前 `03_system_fair` 已选定的正式图配置，不重新选择图结构：

| 方法 | 固定配置 |
|---|---|
| Ours | `R/M=64, L_build=400, alpha=1.2, rerank=100, DB=1bit, query=INT8, residual=4bit` |
| SymphonyQG | `R=64, EF_build=400, iterations=3` |
| OG-LVQ | `LVQ4, R=64, W_build=400, alpha=1.2` |
| Glass-NSG | `SQ4U, R=64, L_build=400` |
| DiskANN-PQ | `R=64, L_build=400, alpha=1.2, PQ≈4 bit/dim` |

05B 中 PQ/SQ/SAQ 强制使用同一份 `R=64, L_build=400, alpha=1.2, seed=20260813` 的 baseline shared Vamana adjacency；Ours 保留实验 02 的 ExRaBitQ4-symmetric 自建图。05A 不包含图。05B 对 Ours 的四级消融在同一份 Ours 图上隔离存储机制；跨方法结果仍包含各自构图方法的影响，不能声称四种方法图拓扑完全相同。

### 3.3 标准混合磁盘驻留

05B 和 05C 采用 DiskANN 常用的混合驻留模型：

- NVMe：图邻接、完整节点载荷和高精度 rerank 数据。
- DRAM：紧凑导航码、码本、入口元数据、查询工作区和受限节点缓存。
- 禁止将整个图、全部完整量化载荷或完整原始向量隐式加载到内存。
- 所有正式读取使用 `O_DIRECT`，不依赖 Linux page cache。

05A 同时报告 `resident` 和 `payload_on_ssd`：前者作为纯量化计算参考；后者故意令量化载荷驻留 SSD、`C=0`，用于测量物理载荷和随机读成本。05A 的 SSD mode 是微基准，不与标准混合驻留的 05B/05C 混合聚合。

### 3.4 DRAM budget

采用 DiskANN 的标准参数表示：

- `B = search_DRAM_budget`：搜索阶段允许索引使用的 DRAM。
- `C = num_nodes_to_cache`：从入口点附近按 BFS 选择并缓存的节点数。

05B 和 05C 主实验统一设置：

```text
B = 2 GiB
```

`B` 包含：

- resident navigation codes
- codebooks/rotator/quantization metadata
- 共享 BFS 节点缓存
- 最大并发数下的查询工作区和 query-local page cache 预留

缓存节点数自动计算：

```text
resident_bytes
+ codebook_bytes
+ worker_scratch_bytes
+ C * cached_node_bytes
<= B
```

同时令：

```text
C <= 10% * N
```

确保至少 90% 的节点仍由 NVMe 提供。每次运行必须报告预算值、实际 resident bytes、cache bytes、`C` 和峰值 RSS。`B` 是索引侧预算，程序运行时、动态链接库等额外内存通过 peak RSS 单独报告。

附加实验：

- GIST：`B ∈ {1, 2, 4} GiB`。
- GIST I/O 诊断：`C=0`，但保留算法必需的导航码和码本。
- 05A `payload_on_ssd`：固定 `C=0`，不参与 BFS cache 预算。

## 4. 统一磁盘格式和 I/O 层

### 4.1 统一文件组成

每个 layer、方法、数据集和配置生成以下文件：

```text
index.pages
resident.bin
index.meta.json
```

`index.pages`：

- 4 KiB 对齐。
- 存放磁盘节点记录。
- 按 DiskANN 官方 disk layout 使用 4096-byte sector。
- 小于或等于 4 KiB 的固定长度节点记录在同一 sector 内按槽位紧凑排列，不跨 sector；页尾无法容纳完整记录的空间留空。
- 大于 4 KiB 的记录占用连续多页，记录起点保持 4 KiB 对齐。
- 固定长度记录不需要额外的随机 offset table，节点 offset 可直接计算。

`resident.bin`：

- 存放允许常驻内存的紧凑导航码、码本和方法元数据。
- 不得包含完整图或完整浮点数据。

`index.meta.json` 至少包含：

- format magic/version
- layer：`05A`、`05B` 或 `05C`
- method/dataset/config ID
- `N/D/R/metric/k`
- entry point/medoid
- node record bytes
- nodes per page 或 pages per node
- resident record bytes
- source index path 和 SHA-256
- page file 和 resident file SHA-256
- quantizer、码本和 scale 信息
- build/export 时间
- nominal/effective bits per dimension

### 4.2 直接 I/O

统一复用官方 DiskANN Linux 读取语义：

- `O_DIRECT`
- Linux native AIO/libaio
- 4 KiB aligned offset、length 和 buffer
- batched read submission
- 总未完成 I/O 请求上限 128

统一读取接口应支持：

- 单页和连续多页读取
- 按页号排序
- 相邻页面合并
- 重复页请求消除
- 查询局部页面缓存
- 05B/05C 的共享只读 BFS 页面缓存
- 逐查询 I/O 计数和耗时

所有方法必须经过同一对齐、队列深度和统计规则；不允许某个方法通过 buffered I/O 获得额外 page-cache 优势。

## 5. 05C 各方法的磁盘移植

### 5.1 Ours-Disk

常驻 DRAM：

- DB 1-bit MSB codes
- 粗筛需要的 factor/scale
- INT8 query 预处理元数据
- centroid/entry point

驻留 NVMe：

- 节点邻接表
- DB 剩余 3 bit 或等价 full-4bit 信息
- residual-4bit codes
- residual scale/factor

搜索流程：

1. 每个查询只做一次 INT8 query 量化和 LUT/因子准备。
2. 读取当前展开节点所在页面，获得邻接表。
3. 对邻居 ID 使用驻留 DB1 codes 进行批量粗筛。
4. 被 DB1 拒绝的候选不读取完整节点页面。
5. 对通过候选按磁盘页号排序，合并同页和相邻页请求。
6. 读取后使用现有 AVX-512 批量内核计算剩余 3 bit/full-4bit 距离。
7. 页面保存在 query-local cache，后续节点展开不得重复读取。
8. residual rerank 复用已读取载荷；只有确实缺失时才补读。
9. 禁止重新计算已经由初筛得到的 MSB 内积。

必须分别统计：

- DB1 checks/query
- DB1 survivors/query
- full4 page reads/query
- residual rerank reads/query
- reused-page count/query

### 5.2 SymphonyQG-DiskPort

根据本地正式源码，SymphonyQG 的节点行格式为：

```text
[raw current vector]
[FastScan codes for the node's neighbors]
[triple_x / factor_dq / factor_vq]
[neighbor IDs]
```

移植方式：

- 保留正式索引中的原图、原入口点、原 rotator、原码和原 factor。
- rotator 和 query LUT 驻留内存。
- 节点行按 4 KiB 对齐放到 NVMe；高维节点允许占连续多页。
- 展开一个节点时一次连续读取该节点行。
- 继续调用官方 LUT+SIMD FastScan 距离核。
- 保持官方 SearchBuffer、visited、候选更新顺序和 tie-breaking。

该布局保留了 SymphonyQG 的核心优势：一次连续读取后，可以顺序扫描当前节点的 64 个邻居码。

### 5.3 OG-LVQ-DiskPort

从正式 SVS 索引导出：

- Vamana adjacency
- entry point
- LVQ4 codes
- scale/bias/quantizer metadata

移植方式：

- LVQ4 紧凑导航码在 `B` 内驻留。
- 邻接表和量化数据规范副本写入 NVMe。
- 展开节点时从 NVMe 读取邻接表。
- 邻居距离继续使用 SVS 官方 LVQ4 距离核。
- 保持原 search-window、候选池和 tie-breaking 行为。

### 5.4 Glass-NSG-DiskPort

从正式 Glass 索引导出：

- NSG adjacency
- navigating/entry node
- SQ4U codes 和量化范围

移植方式：

- SQ4U 紧凑导航码在 `B` 内驻留。
- 图邻接和规范量化副本写入 NVMe。
- 展开节点时读取对应图页面。
- 邻居距离继续使用 Glass 官方 SQ4U 距离核。
- 保持原 NSG 候选更新顺序和 `ef` 语义。

### 5.5 DiskANN-PQ-Disk

使用官方 DiskANN 磁盘构建与搜索代码：

- `R=64`
- `L_build=400`
- `alpha=1.2`
- `k=10`
- `B=2 GiB`
- PQ code length 设为 `ceil(D/2)` bytes/vector，使有效导航码约为 4 bit/dim

完整节点和图驻留 NVMe，PQ codes 和码本驻留 DRAM。若官方 CLI 没有暴露固定 PQ bytes，只增加参数暴露和测量计数，不修改算法或距离计算。

## 6. 正确性和算法保持校验

05B 和 05C 的每个移植系统必须提供两个存储后端：

- `memory-reader`：将转换后的相同节点记录放入内存。
- `direct-disk-reader`：通过 `O_DIRECT` 从 `index.pages` 读取。

05A 必须用同一量化码分别通过内存和 direct-I/O reader 计算距离，确保 storage mode 不改变距离值、top-10 和 fixed-candidate recall。全部校验分三层：

### 6.1 格式单元测试

- header/version 解析
- node ID 到 page offset 映射
- 单页多节点和单节点多页
- 4 KiB 对齐检查
- 末页 padding
- checksum mismatch
- short read/EOF/error propagation
- 重复页面合并
- cache hit/miss accounting
- DRAM budget 计算

### 6.2 小规模逐查询一致性

使用小规模数据和固定参数，对原内存实现、`memory-reader` 和 `direct-disk-reader` 比较：

- top-10 ID 完全一致
- 搜索顺序、visited count 和 distance count 一致
- 浮点分数在既定容差内一致
- 05A 的逐候选压缩距离和 fixed-candidate top-10 完全一致
- 05B 的 PQ/SQ/SAQ 读取同一个 shared-graph SHA-256；Ours 读取固定的 native source-graph SHA-256

### 6.3 正式验证集门槛

正式 validation 上要求：

- 05A resident 与 payload-on-SSD 的 Recall@10 差值为 0，距离值在既定浮点容差内一致
- 05B memory-reader 与 direct-disk-reader 的 Recall@10 差值不超过 `0.001`
- 05B PQ/SQ/SAQ 的 shared graph checksum 完全一致；Ours 的 native graph checksum 与实验 02/03 的正式 Ours 图一致
- 05C 移植版与原正式系统的 Recall@10 差值不超过 `0.001`
- top-10 ID 平均重合率至少 `99%`
- visited/distance count 平均差异不超过 `1%`

不通过时必须修正距离核、候选顺序或 tie-breaking；不得直接进入正式磁盘图表。

## 7. 搜索参数调优

05A 不调图参数，直接复用原实验 01 的 fixed-candidate/search-width 网格；`resident` 与 `payload_on_ssd` 必须运行完全相同的候选点。05B 和 05C 固定图配置，只调搜索阶段参数。

搜索宽度网格：

```text
05A candidate width: 10..30 step 1
05B/05C search width: 1..30 step 1
40..100 step 10
140..580 step 40
```

05B 对应关系：

- PQ/SQ/SAQ/Ours-Disk：统一 search list/`L` 网格；Ours 保留自己的图与 paper-prune 搜索语义

05C 对应关系：

- Ours：search list/`L`
- SymphonyQG：`ef`
- OG-LVQ：search window/`W`
- Glass-NSG：`ef`
- DiskANN-PQ：search list/`L`

异步读取 beam 网格：

```text
{1, 2, 4, 8, 16, 32}
```

调优规则：

- 05A 不按磁盘性能重新选择候选集合，只比较相同 candidate-size 点。
- 只在 validation 上选择 beam、search width 和调度参数。
- 每个方法使用相同的参数尝试数量和重复次数。
- 生成完整 recall–cost Pareto 前沿，不只保留 Recall@10=0.95 的单点。
- 参数锁定后，test 阶段不得再次调整。
- 不向实测 recall 范围之外外插。
- 05A、05B、05C 分别生成 tuning manifest；不得用 05C test 结果反向选择 05B 或 05A 参数。

## 8. 正式运行负载

05A、05B、05C 的正式主结果统一使用 32-worker 吞吐负载。05A 的 `resident` 与 `payload_on_ssd` 必须成对、交错运行；05B 和 05C 使用标准 `B=2 GiB` 混合驻留。GIST 的低并发结果仅作 sensitivity，不与 32-worker 主图混合。

### 8.1 GIST 单请求延迟诊断

- 1 个 query worker。
- 每次只提交一个查询。
- 每个查询内部可以使用异步页面批处理，但不得并发处理其他查询。
- 预热 100 个查询，不计入结果。
- query-local cache 每查询重新创建。
- 05B/05C 的共享 BFS cache 保持预热状态；05A `payload_on_ssd` 固定 `C=0`。

报告：

- Recall@10
- QPS
- mean latency
- p50 latency
- p95 latency
- p99 latency

该负载用于分析单请求延迟，不替代 32-worker 主结果。p95 作为主延迟指标，p99 放入补充材料并标注 query 数量。

### 8.2 并发吞吐

三层、三数据集的主吞吐实验统一：

```text
workers = 32
```

每个 worker：

- 同时最多处理一个查询
- 固定绑定一个 CPU core
- 共享只读 resident codes；05B/05C 共享 BFS cache
- 使用独立查询状态和 query-local cache

05B 和 05C 在 GIST 额外测试：

```text
workers ∈ {1, 2, 4, 8, 16, 32}
```

报告：

- aggregate QPS
- Recall@10
- request p50/p95/p99 latency
- device queue depth
- I/O operations/query

请求延迟从进入搜索调度器开始计时，包含排队、query preparation、I/O、距离计算和 rerank。

### 8.3 重复和运行顺序

- 每个正式设置运行 5 次。
- 查询顺序由固定种子生成确定性乱序。
- 方法顺序使用交错/Latin-square 顺序，避免持续运行顺序偏差。
- 结果报告 5 次运行的中位数。
- 保存逐查询原始延迟，使用 query-level bootstrap 计算置信区间。
- 关键 QPS 或 p95 的重复变异超过 5% 时，先排查系统噪声再重跑。

## 9. NVMe 和硬件控制

正式运行前必须检查：

- `lsblk` 显示目标盘 `ROTA=0`
- 索引位于独占本地 NVMe，而不是当前 workspace 的旋转盘 RAID
- 文件系统支持 `O_DIRECT`
- 可用空间至少 200 GiB
- 没有其他高 I/O 作业
- CPU governor、turbo 状态和 NUMA 拓扑被记录
- 搜索线程固定到 NVMe 对应 NUMA node

当前 `/home/kai3/coco` 位于旋转盘 RAID。本仓库 05 runner 默认使用
`--disk-profile auto`，会把该环境记录为 `hdd_raid` 并执行同一套
O_DIRECT/native AIO 预检。若要复现独占 NVMe 数据，则通过：

```text
--disk-root /mnt/exclusive_nvme/qgraph
--disk-profile nvme
```

显式指定独占 NVMe 路径；若所选 profile 的预检失败，程序必须拒绝生成正式结果。

### 9.1 fio 预检

使用 NVMe 上独立 scratch 文件，在正式搜索前运行：

- 4 KiB random read
- `direct=1`
- queue depth：`1/4/16/32/64/128`
- 记录 IOPS、带宽、mean/p95/p99 device latency

`fio` 不与正式搜索同时运行，其结果写入硬件 manifest，用于解释设备是否达到稳定状态。

因为正式搜索使用 `O_DIRECT`，不依赖操作系统 page cache，不需要在每轮之间执行 `drop_caches`。

## 10. 计时与统计口径

搜索计时包含：

- query INT8/PQ/LVQ/SQ preparation
- LUT 构建
- 05A 的固定候选页面定位、读取和解码
- 候选队列和 visited 操作
- 页面请求提交和等待
- 距离计算
- rerank
- top-k 输出

搜索计时不包含：

- 索引构建或磁盘格式转换
- 进程启动
- 索引文件打开和元数据加载
- resident codes 加载
- BFS cache 构建
- 预热查询

逐查询至少记录以下字段：

```text
layer
storage_mode
dataset
method
config_id
repeat_id
query_id
search_width
beam_width
workers
search_dram_budget_gib
cache_nodes
resident_bytes
cache_bytes
peak_rss_bytes
recall_at_10
fixed_candidate_recall_at_10
mean_relative_error
p95_relative_error
pairwise_flip_rate
latency_us
query_prep_us
queue_compute_us
io_wait_us
distance_compute_us
rerank_us
visited_nodes
distance_evaluations
io_requests
sectors_4k
bytes_read
average_read_bytes
coalesced_requests
duplicate_pages_removed
shared_cache_hits
shared_cache_misses
query_cache_hits
query_cache_misses
```

Ours 额外记录：

```text
db1_checks
db1_survivors
full4_candidates
full4_page_reads
rerank_candidates
rerank_page_reads
```

## 11. 运行接口

总入口：

```bash
python quantization_graph/experiments/05_disk_system_fair/run_disk_suite.py \
  --layers 05a,05b,05c \
  --phase export|validate|tune|run|plot \
  --datasets agnews,gist,dbpedia \
  --disk-root work/05_disk_system_fair/disk_root \
  --disk-profile auto \
  --search-dram-budget-gib 2 \
  --workers 32 \
  --repeats 5 \
  --seed 20260813
```

总入口按 layer 调用三个独立 runner：

```text
run_disk_quantizer_fair.py      # 05A: PQ/SQ/SAQ/Ours fixed candidates
run_diskann_shared_graph.py     # 05B: PQ/SQ/SAQ shared graph + Ours native graph
run_disk_system_fair.py         # 05C: Ours/SymphonyQG/OG-LVQ/Glass/DiskANN-PQ
```

05A 必须显式运行：

```text
--storage-modes resident,payload_on_ssd
```

05B 必须显式运行 Ours 的四个 gate/page-reuse 消融；05C 必须显式运行五个系统。总入口负责生成统一运行顺序，但各 layer 写入独立 CSV 和 tuning manifest。

阶段含义：

- `export`：生成 05A payload pages、05B shared-graph pages 和 05C 正式系统 pages，并生成 manifest。
- `validate`：执行格式、fixed-candidate parity、shared-graph checksum、内存一致性和直接 I/O 一致性测试。
- `tune`：只在 validation 查询上选择参数。
- `run`：使用锁定参数执行 test 查询。
- `plot`：聚合结果并生成论文图。

任一阶段失败时保留日志和 manifest，不得将部分结果静默混入正式聚合文件。

## 12. 结果目录和可复现材料

建议结果结构：

```text
quantization_graph/results/05_disk_system_fair/
├── 05A_disk_quantizer_io/
│   ├── agnews/
│   ├── gist/
│   └── dbpedia/
├── 05B_diskann_shared_graph/
│   ├── agnews/
│   ├── gist/
│   └── dbpedia/
├── 05C_disk_system_fair/
│   ├── agnews/
│   ├── gist/
│   └── dbpedia/
├── manifests/
├── validation/
├── raw/
├── aggregate/
├── figures/
└── README.md
```

必须保存：

- 逐查询原始 JSONL/Parquet
- 每次重复的聚合 CSV
- 最终 Pareto CSV
- validation 参数选择记录
- 05A resident/payload-on-SSD 成对运行记录
- 05B shared-graph SHA-256 和 Ours gate 消融记录
- 05C 五系统移植/parity 记录
- 数据、查询和索引哈希
- 编译参数和 git commit
- 硬件/NUMA/NVMe/文件系统信息
- `fio` 结果
- DRAM budget 和缓存节点数
- 正确性校验报告
- 完整命令行与环境变量

## 13. 图表设计

新增磁盘实验图，不覆盖内存实验图。默认发布图与 `paper/figures/`
里的论文图保持一致：一个实验只保留一个紧凑的 Nature-style 主图，
其余 latency、I/O、memory、ablation 和 sensitivity 视为诊断材料，不在
`results/disk_environment/*/<dataset>/figures/` 默认发布。

### 13.1 默认发布图

- 05A：`disk05a_quantizer_fair_summary`，跨数据集展示
  payload-on-disk matched-recall QPS 和 storage-invariant quantization error。
- 05B：`disk05b_shared_graph_recall_qps`，跨数据集展示 shared-graph
  Recall@10–QPS Pareto 曲线。
- 05C：`disk05c_system_recall_qps`，跨数据集展示端到端 disk-system
  Recall@10–QPS Pareto 曲线。

所有默认图输出 SVG、PDF、PNG 300 dpi 和 TIFF 600 dpi。plot/publish 阶段会清理
旧的默认图像文件，避免历史诊断图残留在 public `figures/` 目录。

### 13.2 诊断材料

以下指标保留在 CSV/manifest 中，只有论文补充材料或排错需要时再单独出图：

- p95 latency、I/O wait、distance compute 和 query preparation；
- 4 KiB sectors/query、bytes/query、I/O requests/query 和 cache hit rate；
- Ours 的 DB1 survivor ratio、page coalescing/reuse 消融；
- GIST `B=1/2/4 GiB`、`C=0` 和 workers=`1/2/4/8/16/32` sensitivity。

资源表汇总：

- SSD index size
- resident navigation-code size
- effective bits/dim
- `B`
- 实际 `C`
- peak RSS
- export/build time

所有 layer 优先报告 Recall@10=`0.90/0.95/0.99`。只有当两个实测点夹住目标 recall 时才允许插值；方法达不到目标时标记 `N/A`，不得向外插值或绘制悬空点。05A 的 recall 是 fixed-candidate recall，不能与 05B/05C 的 graph Recall@10 放在同一坐标轴。

## 14. 论文表述边界

可以写：

> We evaluate the disk setting at three levels: fixed-candidate payload access, shared-graph traversal, and end-to-end systems. This separation distinguishes codec and page-layout effects from graph topology and full-system effects.

> We ported the in-memory graph indexes of SymphonyQG, OG-LVQ and Glass-NSG to a common 4-KiB direct-I/O storage substrate while preserving their graph topology, quantized distance kernels and search semantics. Official DiskANN-PQ-Disk was included as a native disk-resident reference.

不能写：

- “SymphonyQG/OG-LVQ/Glass 官方实现原生支持该磁盘模式”。
- “所有方法都使用完全相同的节点布局”。
- “B=2 GiB 是唯一标准配置”。
- 在未通过内存一致性校验时声称移植不改变算法。
- 把 05A 的 fixed-candidate recall 写成 graph ANN recall。
- 把 05B 的 Ours 写成 shared-float32-graph 受控变体；它实际就是使用自己构图方法的正式 Ours。
- 用 05C 的端到端结果单独证明 DB1 gate 减少 I/O，而不引用 05B 的页面计数。

需要明确说明：05A 统一候选集合；05B 的 PQ/SQ/SAQ 统一图，Ours 因算法要求保留 ExRaBitQ4-symmetric 图，并在自己的固定图上做存储消融；05C 保留各系统完整设计。三层统一 DRAM budget、直接 I/O 规则、页大小、测试负载和测量口径，各算法保留自己的构图、节点记录和量化格式。

## 15. 最终验收条件

只有同时满足以下条件，实验才算完成：

1. 05A、05B、05C 均完成 AGNews、GIST 和 DBpedia。
2. 05A 的 PQ/SQ/SAQ/Ours 均完成 `resident` 与 `payload_on_ssd`，两种 mode 的距离和 fixed-candidate recall 通过 parity。
3. 05B 的 PQ/SQ/SAQ 使用完全相同的 baseline shared-graph SHA-256；Ours 使用实验 02/03 的 native graph SHA-256，并完成四级 I/O 消融。
4. 05C 五个系统全部完成；四个移植系统通过内存结果一致性门槛。
5. 所有正式索引位于独占、非旋转 NVMe。
6. 所有正式读取使用 4 KiB 对齐的 `O_DIRECT`。
7. 索引侧驻留内存不超过指定 `B`，实际使用量完整报告。
8. 三层实验均完成 32-worker 吞吐负载；GIST 完成 1/4/8/16/32-worker sensitivity。
9. GIST 完成 DRAM budget、零缓存和并发度敏感性实验。
10. 逐查询原始数据、manifest、硬件信息和索引哈希齐全。
11. 关键重复实验变异不超过 5%，或已给出可验证解释并重跑。
12. 图中不存在测试范围外插值、悬空点或由测试集反向选择参数的情况。
13. 05A fixed-candidate recall、05B shared-graph recall 和 05C system recall 未被错误混合。
14. 现有内存实验结果和 Fig. 5/Fig. 6 未被覆盖。

## 16. 推荐实施顺序

1. 建立 umbrella `05_disk_system_fair` CLI、三层 manifest schema 和统一统计字段。
2. 实现并测试 4 KiB direct-I/O reader、页面缓存、I/O 计数和预算控制。
3. 完成 05A payload exporter；在当前机器上验证 resident/direct-I/O 距离完全一致。
4. 在独占 NVMe 上跑 05A GIST smoke，确认固定候选页读取与分解计时可信。
5. 完成 05B shared-graph page layout 和 PQ/SQ/SAQ adapter，锁定 baseline shared-graph SHA-256。
6. 将实验 02/03 共用的正式 Ours 图迁移到磁盘，完成 `Ours-Disk` 四级 DB1/page-reuse 消融并通过 parity gate。
7. 跑 05B GIST smoke，确认 DB1 reject 会减少实际 payload pages，而不只是减少距离调用。
8. 完成 05C Ours-Disk、SymphonyQG、OG-LVQ 和 Glass-NSG 的正式索引导出与磁盘移植。
9. 接入官方 DiskANN-PQ-Disk，并为 05C 五系统通过 parity/格式校验。
10. 在独占 NVMe 上执行完整 preflight 和 `fio`，冻结硬件 manifest。
11. 分别完成 05A/05B/05C validation，并冻结三个 tuning manifest。
12. 按 layer、数据集和方法交错顺序运行正式 test。
13. 先聚合 05A 和 05B 验证机制，再聚合 05C 端到端结果。
14. 生成三层图表、资源表、审计报告和论文结论边界说明。
