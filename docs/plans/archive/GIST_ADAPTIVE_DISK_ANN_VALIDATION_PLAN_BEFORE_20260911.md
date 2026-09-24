# 历史存档：GIST 上的内存预算自适应降维 Disk ANN 验证方案

> 本版已被 2026-09-11 修订方案替代，不作为当前执行协议。原始正文保留，其中实现状态、查询划分及预算规则可能过时或相互矛盾。当前入口为 [修订方案](../GIST_ADAPTIVE_DISK_ANN_VALIDATION_PLAN.md)。

## 1. 目标与结论边界

本实验用于验证一种**根据可用内存自适应选择降维表示**的 Disk ANN 方法。核心问题是：在 Ours 的固定 Vamana 图和固定磁盘索引上，能否根据预算选择合适的路由维度/1-bit 码长，在严格内存约束下保持 Recall 并减少磁盘读取。beam、缓存和 I/O pipeline 在主实验中固定，只能作为后续消融，不能混入“降维自适应”的主收益。

本文件以 GIST-1M 作为 Phase-1 pilot，但方法定义不绑定 GIST。通过 pilot 后，必须在 `agnews`、`dbpedia` 和可行的 scale-extension 数据集上按同一协议复验；每个数据集独立训练投影、独立选择维度并独立报告预算可行性。

1. 固定图和单份磁盘索引能否支持多个内存预算，而不重新建图或重新导出。
2. 启动时预算自适应能否接近同预算下人工调出的最佳静态配置。
3. 不同内存预算下，自动选择的降维维度能否接近该预算的最佳固定维度。
4. 256 MiB 至 4 GiB 的严格 cgroup 预算能否得到可复现、无 OOM 的运行结果。

GIST 只能作为机制、正确性和 1M 规模的第一阶段验证。仅凭 GIST 不得宣称方法已经解决 100M 数据集、4 GiB 内存约束或 NVMe 上的规模扩展问题；跨数据集结果必须单独汇总，不能用 GIST 的投影或维度配置替代其他数据集的训练和校准。

## 2. 原方案核验后的关键修正

### 2.1 自适应对象是降维维度

- Vamana 图只构建一次，图拓扑与查询预算无关。
- 多个预算共用同一 graph SHA-256 和同一磁盘索引 SHA-256。
- 预算主要决定启动时选择的 `d_route` 和对应的 1-bit 常驻码长度。
- 主实验的 beam、缓存替换策略、I/O depth 和查询顺序固定；但 cache **容量**必须随 `d_route` 重新计算，不能让降维释放的内存闲置。容量变化属于同一总预算下的内存分配，不作为额外 cache policy 创新。
- 若加入查询内升级，只能作为独立扩展：低维码先筛选，歧义候选再升级到更高维码或完整 DB1；该升级规则必须在 validation 冻结。

### 2.2 纯磁盘节点必须携带邻居导航码

节点记录不能只保存“当前节点的 4-bit 表示 + 邻接表”。这种布局读取父节点后仍无法给新发现的邻居排序，会退化为逐邻居随机读取。

本方案采用如下记录：

```text
NodeChunkV3
  = Header
  | Neighbor IDs
  | Neighbor inline-32 codes and factors
  | Current-node fine4 payload
  | 4-KiB alignment padding
```

父节点记录中的 `Neighbor inline-32` 允许搜索在一次节点读取后对所有出邻居做粗排序。若记录跨越多个 4 KiB 页，必须按实际页数计入 I/O，不能继续声称“一次扩展等于一次 I/O”。该布局借鉴 AiSAQ 将邻居导航码内联到存储记录的思路，但导航编码、逐级升级和预算控制由本方案单独定义。

### 2.3 GIST 的 L2 距离不能使用裸 Hamming 硬剪枝

GIST 使用 squared L2。短二值前缀只能用于候选排序、推迟读取和查询难度估计，不能把裸 Hamming 距离当作原空间 L2 下界。

- 完整 960-bit DB1 使用 RaBitQ 式非对称距离估计，并保存所需 norm、scale 或 factor。
- 只有经过验证的完整 DB1 保守下界可以执行硬剪枝。
- `{32,64,...,256}` bit 前缀只使用 validation 查询校准经验置信区间；区间不确定时必须升级到完整 DB1。
- 4-bit 表示承担精细排序，最终 Recall 由 ground truth 检验。

### 2.4 2 GiB 和 4 GiB 不是 GIST 的压力预算

GIST 的主要原始载荷约为：

| 组件 | 估算大小 |
|---|---:|
| 1M × 960-bit DB1 | 114.4 MiB |
| 1M × 960 维 fine4 | 457.8 MiB |
| 当前共享图文件 | 229.0 MiB |
| 合计，不含内联码、factor 和对齐 | 约 801 MiB |

因此 2 GiB 和 4 GiB 很可能进入 cache-rich 或近似全缓存状态，不能作为 GIST 的主要受限内存证据。本实验将 512 MiB 设为主约束、256 MiB 设为压力档；1/2/4 GiB 用来观察模式切换和性能饱和。

### 2.5 现有方法带来的增量设计

本方案不把已有方法的单个组件重新包装成创新，而是明确吸收其可验证的系统经验：

| 已有路线 | 已知机制 | 本方案的处理 |
|---|---|---|
| DiskANN | `search_DRAM_budget`、节点缓存和 beam width 已经是成熟基线 | 作为基线；不再宣称“按预算选码长”本身具有新颖性 |
| AiSAQ | 将压缩导航信息放入 SSD 记录，减少 DRAM 依赖 | 采用父记录内联邻居短码 |
| ADSampling / RaBitQ | 逐步增加距离计算精度；完整码可提供误差边界 | 采用 progressive escalation；短前缀只排序/延迟，完整码才硬剪枝 |
| PipeANN | 搜索宽度与在途 I/O 宽度应分离，并利用异步完成队列 | 新增独立的 `pipeline_width`；第一阶段保留 lockstep，第二阶段再启用 async |
| Starling / PageANN | 页面级布局、页面搜索和物理页利用率决定磁盘收益 | 增加 page-search 消融、useful-records/page 和 read amplification 指标；不改变图拓扑 |
| VeloANN / OctopusANN | record-level buffer pool、动态宽度和页级组合优化 | 将 NodeChunk 缓存从单一 page-LRU 扩展为 page/record/hybrid 三种模式 |
| SPANN | 查询感知地选择磁盘 posting lists，但其索引架构是倒排而非 Vamana | 作为架构对照，不混入固定图机制实验 |

因此，本文真正要验证的是：**同一固定 Vamana 图上的预算感知降维选择和 1-bit 路由码**。渐进式候选升级、record/page 缓存和异步 pipeline 只作为控制变量或附加消融，不属于主创新。

`page_shuffle` 不列入核心方案：它会改变物理记录顺序，且对 Vamana 的长程边未必稳定；若后续加入，只能作为独立的 layout variant，在相同图拓扑下比较，不能与查询阶段自适应的主结果混报。

### 2.7 从近期降维方法吸收的设计

近期方法给出的可迁移原则，而不是可以直接照搬的代码：

| 方法 | 可迁移原则 | 在本方案中的落地 |
|---|---|---|
| Matryoshka Representation Learning | 一个表示包含多个嵌套维度，按资源切换而无需维护多套独立模型 | 学习一个嵌套投影 `P`，其前缀对应 64/128/256/512/960 维 |
| Matryoshka-Adaptor / SMEC | 通过多维度联合目标和自适应维度选择降低截断损失 | validation 只选择预算可行的维度；投影训练阶段同时约束多个前缀 |
| MPAD | 降维目标应直接保持近邻关系，而不是只最大化全局方差 | 用 base-only 的近邻/非近邻对训练近邻保持投影；PCA 仅作基线 |

GIST 的向量已固定，不能声称重新训练了 embedding 模型。因此主方法命名为 **NNP-Matryoshka-1bit（nearest-neighbor-preserving nested projection）**：它是对上述原则的离线线性投影移植，不宣称复现原论文的端到端训练结果。当前第一版实现采用 PCA 基底加 base-only 近邻分离的轻量排序近似；只有在该版本通过 smoke/Recall 验证后，才考虑实现完整的多维 margin 优化器。

### 2.6 SymQG 的纳入决策

SymQG 不纳入本方案的主实验矩阵，保留为单独的 native-system 对照。核查当前端口后，实际行为是：读取当前节点 `u` 的行时，利用行内随边保存的 FastScan payload 为邻居 `v` 计算近似分数；只有 `v` 被真正展开或进入最终重排集合时才读取 `v` 的整行。因此问题不是把“纯缓存读取”误当成距离计算，而是它使用了主方案没有的边内联 payload、不同的搜索循环和不同的记录布局。

此外，当前端口的产物曾将 `cache_bytes` 写为 0、`peak_rss_bytes` 以估算值代替实际采样，且行缓存是查询过程内部状态，尚未证明满足统一的总预算。因此把它和主方案放在同一自适应结论中会破坏公平性。

重新纳入主矩阵必须同时满足以下门槛，否则只报告为“native-system secondary baseline”：

1. 使用与主实验相同的搜索事件协议：候选只有在本次请求中完成距离计算后才能进入 frontier；主实验禁止 cache-only prefetch。
2. 每次读取 `v` 必须对应当前展开、候选最终重排或已声明且不计搜索收益的预取；预取未被使用的页全部计为 wasted I/O，不能增加 Recall 或减少访问计数。
3. 将行缓存、线程 scratch、I/O buffer 和 metadata 纳入同一 cgroup 总预算，`memory.peak` 必须实测，`cache_only_reads=0` 必须由 trace 检查。
4. 若保留边内联 payload，必须把它明确标为独立 layout variant；若要进入“同策略”主表，则应改为与 NodeChunkV3 相同的存储布局和候选升级路径。

因此本轮采取的明确选择是：**主实验删除 SymQG，二级实验修复后保留；不为迁就 SymQG 改变主方案的统一策略。**

## 3. 数据集与固定资产

### 3.1 数据集

| 项目 | 固定值 |
|---|---|
| 数据集 | GIST-1M |
| Base | `data/gist/gist_base.fvecs` |
| Query | `data/gist/gist_query.fvecs` |
| Ground truth | `data/gist/gist_groundtruth.ivecs` |
| 向量数 | 1,000,000 |
| 维度 | 960 |
| 查询数 | 1,000 |
| Ground-truth 深度 | 100 |
| 距离 | squared L2 |
| 目标 K | 10 |

数据文件的尺寸、维度和查询数量以以下 manifest 为准：

```text
results/disk_environment/dataset_artifacts/gist/manifests/
  gist_dataset_manifest.json
```

### 3.2 查询划分

沿用 `experiments/05_disk_system_fair/dataset_policy.py` 的既有契约：

- validation：前 200 条查询，只用于校准和选参。
- test：后 800 条查询，只用于冻结配置后的正式测量。
- validation 和 test 内部分别使用 `seed=20260813` 生成固定查询顺序。
- test 结果生成前不得查看 test 上的配置优劣并重新调参。

### 3.4 多数据集复验协议

核心复验集沿用仓库的数据集策略：

| 阶段 | 数据集 | 作用 |
|---|---|---|
| Phase-1 pilot | GIST-1M | 快速验证 NNP-Matryoshka-1bit 的实现和内存模型 |
| Phase-1 replication | AGNews、DBpedia | 验证不同维度和文本分布下的泛化 |
| Phase-2 scale extension | SIFT10M、Deep1B 子集、BIGANN10M、Cohere10M | 验证规模、维度和数据分布变化；只有通过 doctor 才运行 |

对每个数据集分别执行：

1. 用该数据集的 `projection_train` base 子集学习 `P_dataset`，不共享 GIST 的投影矩阵；
2. 在该数据集 validation 上冻结 `d_route(B)`、阈值和候选宽度；
3. 在相同预算集合 `{256 MiB,512 MiB,1 GiB,2 GiB,4 GiB}` 上运行 test；
4. 单独记录 `Recall@10`、`pages/query`、`memory.peak`、最小可行维度和 `NoFeasibleProfile`；
5. 只有当至少两个 core 数据集通过同一验收条件，才把方法表述为“跨数据集有效”。

不同数据集的维度、距离度量、归一化状态和 base/query/ground-truth hash 必须来自各自 manifest。不能把 GIST 上的 `d_route`、投影参数或 Recall 阈值未经校准直接复制到其他数据集。

### 3.3 固定图（仅限受控消融）

对于 Ours 的内部受控消融，复用现有共享 FP32 Vamana 图：

```text
results/graph/gist/shared_graph/
  diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin
```

图参数固定为：

```text
R=64
Lbuild=400
alpha=1.2
seed=20260813
metric=FP32 squared L2
```

实验开始前记录图文件 SHA-256。Ours 的不同预算和自研变体必须使用同一图哈希。

这条规则不适用于原生系统对照。DiskANN、SymQG 或其他外部方法如果必须使用自己的原生图和布局，应进入 `native_system_comparison` 线，单独报告 graph build 参数、图大小、索引大小和最小可行内存；不能把“共用同一图”写成跨方法公平性的必要条件，也不能与 Ours 的同图消融直接合并排名。两条实验线共享数据集、查询顺序、目标 Recall、预算和硬件，但图/布局差异必须显式标注。

## 4. 固定磁盘索引

### 4.1 降维发生位置与运行时驻留对象

本方案的降维不是只对“已经放入内存的 1-bit 数据”再做一次处理，而是在离线导出阶段作用于整个 base 数据集：

```text
完整 base 向量（逐批从磁盘读取）
        |
        |  z = P_dataset^T x
        v
全量 d_route-bit route code（写入独立 sidecar）
        |
        |  启动时按预算选择一个 d_route
        v
该 d_route 的全量 route code 常驻内存
```

运行时的驻留对象只有：

- 选定维度的全量 1-bit route code；
- 与 route code 对齐的每向量 asymmetric scale/norm；
- 数据集专属投影 metadata（`P_dataset` 的必要参数、均值、顺序和 hash）；
- 每个 query 的临时投影 `qP`、固定线程 scratch 和候选队列。

完整 DB1、fine4、原始向量和需要精确重排的 payload 继续留在磁盘；只有候选进入精排集合时才读取。若未来为了减少磁盘容量而删除完整向量，则无法执行本方案的 exact/fine rerank，不属于当前实验设计。

从上述共享图一次性流式导出一份、与预算无关的索引：

```text
index_manifest.json
graph.bin
node_chunks.bin
full_db1.bin
full_db1_factors.bin
fine4_metadata.bin
route_dims.u16
checksums.json
```

其中：

- `full_db1.bin` 保存完整 960-bit 数据库码。
- `route_dims.u16` 固定嵌套位顺序，使所有常驻前缀都来自同一码。
- `node_chunks.bin` 保存邻接表、邻居 inline-32 导航码及当前节点 fine4。
- 所有读取以 4 KiB 页为物理计量单位。
- 100M 版本未来必须使用流式两遍导出；GIST 版本也沿用同一流式接口，避免形成仅适用于 1M 的实现。

索引导出后设为只读。不同预算只生成轻量 `BudgetProfile`，不得复制或改写主索引。

## 5. 两层自适应控制器

### 5.1 严格内存模型

查询服务及其子线程放入独立 cgroup v2，约束为：

\[
B \ge M_{fixed} + T M_{thread} + M_{resident} + M_{cache} + M_{reserve}.
\]

其中安全余量固定为：

\[
M_{reserve}=0.10B.
\]

预算必须覆盖：

- 可执行文件和匿名内存；
- offset、码本和投影元数据；
- 1-bit 路由投影的固定 seed、稀疏结构描述和查询侧临时投影 buffer；
- 全部线程 scratch、visited 和候选队列；
- 常驻导航码及 factor；
- 低维 route code 及每向量 asymmetric scale/norm；
- NodeChunk 与 Full1 应用级页缓存；
- NodeChunk record cache、Full1 page cache 和 hot-entry navigator；
- in-flight I/O buffer 和缓存哈希表开销。

不得仅按码文件理论大小判断可行性。正式结果要求 `memory.peak <= B`，且 `memory.events` 中 `oom=0`、`oom_kill=0`。

### 5.2 一比特降维路由码

`resident_bits` 不是把原始 960 维向量直接截断，而是对原向量先做固定降维映射，再保存每个投影分量的 1 bit 符号：

\[
z=P^T x,\qquad b_j=\mathbf{1}[z_j\ge 0],\quad P\in\mathbb{R}^{960\times d_{route}}.
\]

本轮固定 `d_route ∈ {64,128,256,512,960}`。投影训练只使用独立的 `projection_train` base 子集及其 base-only 近邻对；不得使用 test query 或 test ground truth。`P` 的投影 seed、列顺序和缩放因子写入只读 metadata；query 侧只保留一次 `qP` 临时 buffer，不把整套浮点投影矩阵重复放入每个 worker。`n × d_route` 个 1-bit 路由码的实际字节数、投影 metadata、`qP`、线程 scratch 和缓存都计入同一预算。

路由评分必须保持与 DB1/RaBitQ 一致的非对称形式：query 保留投影后的实值/INT8 分量，database 侧使用 1-bit sign code 及每向量 scale/norm 计算 asymmetric score。query 不得二值化后改用 Hamming。该低维非对称分数只用于候选排序、延迟升级和读取优先级，不能作为 GIST squared-L2 的硬下界。候选需要通过完整 DB1 或 fine4 路径确认，任何投影参数必须只在 validation 上冻结，test 阶段不可重新拟合。

对单个 query，低维路由的候选排序不得重构浮点向量。令 `b_i∈{-1,+1}`、`x̂_i=s_x b_i`，则去掉 query 常数项后使用：

\[
score(x,q)=d_{route}s_x^2-2s_x\langle q,b\rangle.
\]

C++ 实现必须直接对 bit-packed sign code 计算 `⟨q,b⟩`：AVX-512 可使用 mask/压缩加载，AVX2 使用分块 bit-expand 或 query-specific byte lookup；都不能逐维生成 `float x̂` 再计算 L2。每向量 `scale` 使用 FP16/FP32 读取并计入 route resident bytes。`‖q‖²` 只在需要输出真实距离时恢复，候选排序阶段不计算。

降维自适应的主控制器只做一件事：给定预算 `B`，从候选集合
`d_route ∈ {64,128,256,512,960}` 中选择满足内存约束且 validation Recall 达标的最大维度。选择目标按“先满足 `Recall@10 ≥ target`，再最小化 pages/query，最后最大化 QPS”排序；如果多个维度均达标，优先选择内存更小的维度，保留剩余内存用于固定的必要 cache/scratch。每个预算只冻结一个 profile，test 阶段不得按查询结果重新挑维度。

推荐的实际流程如下：

1. **离线学习投影，不直接截断原维度。** PCA/旋转投影作为基线；主方法使用 base-only 的近邻保持嵌套投影 `P`。训练时对 `d∈{64,128,256,512,960}` 的多个前缀同时施加近邻 margin loss 和正交/尺度约束，使低维前缀优先保留 ANN 相关几何。投影和阈值一旦冻结，validation/test 阶段不得重新训练。
2. **生成低维 1-bit 路由码。** 对每个向量计算 `z=P^T x`，保存 `sign(z)`；不要把 960 个原始维度逐位保留。`d_route=128/256` 时，1M 向量的路由码分别约为 16/32 MiB。
3. **保留磁盘精排兜底。** 低维 1-bit 码和 per-vector scale 只负责非对称导航、候选排序和读取优先级；候选进入 top-L 后，从磁盘读取完整 DB1/fine4 做重排。低维近似分数不能直接作为 squared-L2 的硬剪枝下界。
4. **按预算选择最小可行维度。** 对每个预算实际测量 `d_route={64,128,256,512,960}` 的 Recall、pages/query 和内存峰值，选择满足目标 Recall 的最小维度；若没有维度达标，报告 `NoFeasibleProfile`，不能静默使用更高预算。
5. **采用分层保底而不是盲目增维。** 若 `d_route=64/128` 的 Recall 不够，优先增加少量固定的高频节点/入口导航码或扩大候选 `L`，再考虑升到 256/512；所有额外结构必须计入预算并作为独立消融报告。

该设计的关键是：**内存中的 1-bit 码只做粗路由，磁盘上的完整表示负责纠错**。这样即使低维投影存在碰撞，也不会把近邻永久硬剪掉。

### 5.3 启动时预算自适应

候选常驻码长固定为：

```text
resident_bits ∈ {0,32,64,96,128,160,192,256,960}
```

- `resident_bits=0`：不保存全局导航数组，使用父记录内 inline-32。
- `0 < resident_bits < 960`：启动时从完整 DB1 流式提取最终常驻前缀数组。
- `resident_bits=960`：完整 DB1 及其 factor 常驻。

扣除固定开销、线程开销、常驻码和 10% reserve 后，剩余内存在两类缓存间分配：

```text
Full1 page cache : NodeChunk page cache
  ∈ {25:75, 50:50, 75:25}
```

validation 选择出的配置写入：

```json
{
  "schema_version": 1,
  "dataset": "gist",
  "budget_bytes": 0,
  "workers": 0,
  "resident_bits": 0,
  "full1_cache_bytes": 0,
  "node_cache_bytes": 0,
  "search_L": 0,
  "beam_min": 0,
  "beam_max": 0,
  "pipeline_min": 0,
  "pipeline_max": 0,
  "confidence_quantile": 0.999,
  "io_depth": 0,
  "navigator_mode": "none",
  "node_cache_mode": "hybrid",
  "page_search": false,
  "io_mode": "lockstep",
  "graph_sha256": "",
  "index_sha256": "",
  "validation_query_sha256": "",
  "seed": 20260813
}
```

冻结后的 profile 在 test 阶段不可修改。

### 5.4 逐查询自适应

每条查询采用以下状态机：

```text
inline32 / resident prefix
          |
          | coarse score interval intersects current top-L boundary
          v
batched full 960-bit DB1 read
          |
          | full bound cannot prune candidate
          v
NodeChunk and fine4 refinement
```

具体规则：

1. 使用常驻前缀；没有常驻前缀时，使用当前父记录中的 inline-32 给邻居排序。
2. 粗分数区间明显优于当前边界的候选直接进入 NodeChunk 读取队列。
3. 区间与边界相交的候选合并为 Full1 页读取，计算完整 DB1 距离和保守下界。
4. 粗分数明显较差的候选先放入 deferred 队列，不立即删除；只有完整 DB1 下界允许硬剪枝。
5. 搜索结束条件不稳定时，按粗分数顺序重新检查 deferred 候选，防止短码误差造成不可恢复的 Recall 损失。
6. 最终候选用 fine4 精细排序，输出 top-10。

定义当前一轮新候选中的歧义比例：

\[
u_t=\frac{N_{ambiguous}}{\max(1,N_{fresh})}.
\]

动态 beam 为：

\[
beam_t=\operatorname{clamp}
\left(
beam_{min}+\left\lceil u_t(beam_{max}-beam_{min})\right\rceil,
beam_{min},beam_{max}
\right).
\]

简单查询保持小 beam，困难查询扩大并行读取宽度。所有 I/O buffer 必须预分配，不允许逐查询扩容突破预算。

### 5.5 分离搜索 beam 与 I/O pipeline

`beam_t` 只表示本轮逻辑上扩展多少个 frontier 节点；`pipeline_width_t` 表示同时允许多少个未完成的物理读取。二者不得共用一个参数或一个结果列。

定义最近一轮读取中“完成后仍被候选池使用”的比例：

\[
h_t=\frac{N_{useful\ reads}}{\max(1,N_{completed\ reads})}.
\]

在硬件允许的 `io_depth` 内，采用带滞后的 pipeline 控制：

```text
if h_t >= 0.90: pipeline_width += 1
if h_t <= 0.50: pipeline_width -= 1
pipeline_width = clamp(pipeline_width, pipeline_min, pipeline_max)
```

`h_t` 过低说明预取过于投机，应减少在途读取；`h_t` 较高且 SSD 队列未饱和时才扩大 pipeline。第一版先实现可复现的 lockstep batch-and-wait，第二版再实现跨轮次 async completion；不能把 batch-and-wait 结果描述为计算-I/O 重叠。

### 5.6 页级搜索与 record-level cache

一次 4 KiB 页读取后，扫描该页内所有完整 NodeChunk record，提取其中可用于当前查询的候选，再决定下一批读取。页面内的候选去重和排序在内存中完成，不能只使用触发该页读取的一个 record。

NodeChunk 缓存比较三种模式：

```text
page_lru   : 以物理页为缓存对象
record_lru : 以完整 NodeChunk record 为缓存对象
hybrid     : page directory + hot record slots
```

`hybrid` 是推荐默认值：冷数据按页读，validation 中高频访问的 record 可独立保留在固定 slot 中。缓存命中率、useful-records/page 和 read amplification 必须分别报告。

可选的 `entry_navigator` 只保存入口点及其高频 frontier 的 ID 和常驻短码，内存上限为 `min(0.05B, 8 MiB)`。它是 Starling/OctopusANN 类“内存导航器”的受限版本，必须作为独立消融，不能把它的收益混入逐查询码长收益。

## 6. 实验矩阵

### 6.1 预算、并发和缓存状态

| 总 cgroup 预算 | 角色 | 预期用途 |
|---:|---|---|
| 256 MiB | 压力档 | 检验 reduced/inline 路径与不可行配置拒绝 |
| 512 MiB | 主结果 | 保持明显磁盘压力，比较自适应收益 |
| 1 GiB | 转换档 | 观察完整 DB1 与缓存之间的分配变化 |
| 2 GiB | cache-rich 对照 | 观察收益是否开始饱和 |
| 4 GiB | 上界 | 检查接近全缓存后的性能上限 |

每个预算执行：

```text
workers ∈ {1,32}
cache_state ∈ {cold,steady}
formal_repeats = 1  # 当前验证；论文统计阶段可选扩展到 5
K = 10
target_recall ∈ {0.90,0.95,0.99}
```

- `workers=1` 用于单查询 latency 和 I/O 路径诊断。
- `workers=32` 是吞吐主结果。
- cold：重启查询进程、清空应用级缓存并使用 O_DIRECT；不依赖全局 `drop_caches`。
- steady：启动后用固定的 100 条 validation 查询预热，再测 test 查询。
- 预热查询和启动开销不计入 QPS，但必须单独记录。

### 6.2 对照方法

| 名称 | 作用 |
|---|---|
| `Ours-Disk-current` | 当前磁盘实现基线 |
| `DiskANN-PQ-Disk` | 外部 DiskANN 磁盘基线 |
| `BestFixed` | validation 选择的同预算最佳静态配置 |
| `PCA-1bit-Fixed` | PCA 投影后固定 `d_route` 的 1-bit 路由码基线 |
| `NNP-Matryoshka-1bit-Fixed` | 近邻保持嵌套投影后固定 `d_route` |
| `InlineOnly` | `resident_bits=0`，固定 beam |
| `StartupAuto` | 只做启动时预算自适应 |
| `StartupAuto+Full1` | 增加逐查询 Full1 升级，beam 固定 |
| `DimAdaptive-core` | 自适应选择 `d_route` 和剩余 cache 容量，beam/cache policy/I/O depth 固定 |
| `DimAdaptive+Escalation` | `DimAdaptive-core` + 歧义候选升级到更高维/完整 DB1 |
| `FullAdaptive-core` | 非主线扩展：再加入动态搜索 beam |
| `FullAdaptive-system` | 非主线扩展：再加入 pipeline、hybrid cache 和 page search |
| `FullDB1` | 完整 DB1 常驻，预算允许时作为上界 |

所有自研变体必须使用同一图、同一磁盘索引和同一测试查询顺序。原生系统对照使用独立目录和独立 manifest，不与同图消融混排；AiSAQ 可在论文扩展阶段加入外部系统比较，但不作为本轮 GIST 可行性验证的阻塞项。

`SymphonyQG-DiskPort` 不出现在上述主矩阵；它单独进入 `secondary_native/` 结果目录，并标注“不同布局/不同搜索策略，不参与同策略排名”。同样，DiskANN-PQ 作为成熟 native disk baseline 单独报告；主结论只来自共享 NodeChunkV3、共享查询协议和共享预算的自研变体。

Phase-1 的推荐主线是 `PCA-1bit-Fixed` → `NNP-Matryoshka-1bit-Fixed` → `DimAdaptive-core`：先证明近邻保持投影优于普通 PCA，再证明同一嵌套投影可以按预算自动选维度。若 NNP 投影训练成本或 Recall 无法接受，退回 PCA 版本并如实报告，不得把复杂投影的失败隐藏在自适应结果中。

消融按增量顺序执行，而不是把所有组件一次性打开：

```text
A0  BestFixed
A1  A0 + StartupAuto
A2  A1 + dimension selection                 = DimAdaptive-core
A3  A2 + Full1/dimension escalation          = DimAdaptive+Escalation
A4  A3 + entry_navigator
A5  A3 + record/page hybrid cache
A6  A3 + page_search
A7  A3 + async pipeline
A8  A4+A5+A6+A7                             = FullAdaptive-system (extension)
```

每一步固定其他参数并复用同一 validation 配置预算，才能区分“码长收益”“缓存收益”“页级收益”和“异步收益”。当前 AGNews 验证只运行 1 次；若后续需要置信区间，再对冻结后的最终配置追加重复运行，不回头改变 profile。

### 6.4 统一公平协议与预算矩阵

主实验的所有方法必须共享以下条件：

- 相同 GIST base/query/ground truth、validation/test 划分、查询顺序、`K=10`、worker 数和重复次数；
- 相同固定图 hash、NodeChunkV3 布局、4 KiB 页、`O_DIRECT`/I/O depth 规则和 cgroup 总内存上限；
- 相同预算矩阵 `{256 MiB, 512 MiB, 1 GiB, 2 GiB, 4 GiB}`，每个预算都单独启动并重新记录 `memory.peak`；
- 相同的 cache cap 计算、LRU/hybrid 替换规则、预热规则和 cold/steady 状态；任何方法不得把未计入的常驻数组、行缓存或 worker scratch 作为“免费内存”；
- 相同的物理页计数、逻辑 cache-hit 计数、wasted-read 计数和 Recall/QPS 选择规则。

搜索参数不要求名称相同，但必须在 validation 上按统一的工作量约束冻结：记录 `visited_nodes`、`distance_evaluations`、`pages_read` 和 `inflight_reads`，test 阶段禁止重新调参。主实验不允许仅为某一方法增加候选预取；异步 pipeline 只在独立消融中开启，且未使用读取不获得任何搜索信用。

若某方法在某预算下无法满足固定布局或最小 scratch，输出 `NoFeasibleProfile`，不得改用更高预算后填补该单元。最终表同时给出所有方法的共同可行预算交集，以及各方法的最小可行内存。

### 6.5 实验阶段边界

本轮正式实验只执行 **Phase-1：降维自适应**：

```text
固定 beam
固定 cache policy；cache 容量 = 总预算 - 固定开销 - route code/scale - reserve
固定 lockstep I/O
自适应变量：d_route 与由剩余预算决定的 cache 容量
```

Phase-1 的正式方法只有：`BestFixed`、`DimAdaptive-core` 和可选的 `DimAdaptive+Escalation`。主表不得加入动态 beam、cache policy 或异步 I/O 的变化；但每个 `d_route` 必须使用同一总预算下重新计算的剩余 cache 容量。

以下内容全部延后到 Phase-2，只有 Phase-1 通过 Recall、内存和可复现实验门槛后才允许启动：

| Phase-2 组件 | 单独研究问题 | 当前状态 |
|---|---|---|
| 动态 beam | 搜索宽度自适应是否减少访问而不损失 Recall | 后续消融 |
| page/record/hybrid cache | 跨查询缓存是否减少 pages/query | 后续消融 |
| async pipeline | 计算与 I/O 重叠是否提高 QPS | 后续消融 |

Phase-2 每次只打开一个组件，固定 `d_route` 和 Phase-1 的最优 profile；不得把 Phase-2 的收益回填到“降维自适应”的主结论中。

### 6.3 参数空间

```text
resident_bits = {0,32,64,96,128,160,192,256,960}
L             = {20,40,80,120,200,320,480}
beam_fixed    = {1,2,4,8,16,32}
beam_min      = {1,2,4}
beam_max      = {8,16,32}, beam_max >= beam_min
confidence    = {0.99,0.999,0.9999}
io_depth      = {32,64,128}
cache_split   = {25:75,50:50,75:25}
navigator_mode = {none,entry_navigator}
node_cache_mode = {page_lru,record_lru,hybrid}
page_search   = {off,on}
io_mode       = {lockstep,async}
pipeline_min  = {1,2}
pipeline_max  = {4,8,16}
```

禁止直接执行完整笛卡尔积。调参采用固定阶段：

1. 根据实际内存模型剔除不可行配置。
2. 固定 `L=120, beam=2–8, confidence=0.999, io_depth=64`，搜索 resident bits 和缓存划分。
3. 保留 validation Pareto 前三名，搜索 `L` 和 beam。
4. 固定 `io_mode=lockstep`，单独比较 page search、cache granularity 和 entry navigator。
5. 只对决赛配置搜索 confidence、I/O depth 和 pipeline width。
6. 每个方法获得相同的 validation 查询数和配置试验次数。
7. 写入 `tuning.lock.json` 后再进入 test。

## 7. 运行阶段与接口

新增独立套件，避免改变 05 的“只改变存储、不改变搜索算法”契约：

```text
experiments/06_adaptive_disk_ann/
  run_adaptive_disk_suite.py
  doctor.py
  export_index.py
  validate_index.py
  tune_profiles.py
  plot_results.py
```

结果目录固定为：

```text
results/disk_environment/06_adaptive_disk_ann/gist/<run_id>/
```

每次运行使用唯一 `run_id`，并在 suite 根目录持有排他锁。图和导出的主索引只读共享，CSV、日志、profile 和图表不得写入其他正在运行任务的目录。

统一命令：

```bash
python experiments/06_adaptive_disk_ann/run_adaptive_disk_suite.py \
  --phase all \
  --dataset gist \
  --budgets 256MiB,512MiB,1GiB,2GiB,4GiB \
  --workers 1,32 \
  --cache-states cold,steady \
  --target-recall 0.90,0.95,0.99 \
  --repeats 5 \
  --seed 20260813 \
  --formal
```

阶段顺序不可跳过：

```text
doctor -> export -> validate -> tune -> run -> plot
```

### 7.1 `doctor`

检查：

- dataset manifest、metric、GT 深度和文件哈希；
- graph manifest、图哈希和节点数；
- O_DIRECT、4 KiB 对齐、cgroup v2 和无 swap；
- 剩余磁盘空间和输出目录隔离；
- CPU、内存和块设备的空闲基线；
- 正在运行的 graph build、ANN benchmark 或 fio 任务。

`--formal` 的并发门槛固定为连续 60 秒：

```text
平均 CPU busy <= 10%
目标块设备平均 %util <= 5%
目标块设备平均 aqu-sz <= 0.1
无其他 ANN/建图/磁盘压测进程
```

任一条件不满足时，formal 必须拒绝运行；`--pilot` 可以运行正确性检查，但结果不得进入正式性能表。

### 7.2 `export`

- 从固定 GIST 图导出一次 NodeChunkV3、完整 DB1 和 fine4。
- 记录每个文件的 schema、大小、页数和 SHA-256。
- 导出过程不接收预算参数。

### 7.3 `validate`

- 检查所有记录边界、页对齐、offset、checksum 和短读。
- 检查不同 resident prefix 与完整 DB1 的位前缀一致。
- 使用小查询集比较内存 reader 与 O_DIRECT reader。
- 对每个预算做内存可行性 dry-run。

### 7.4 `tune`

- 只读取 200 条 validation 查询。
- 生成每个预算和 workers 组合的 Pareto 表。
- 在满足 Recall 和内存上限后选择最高 QPS；相同 QPS 时选择较低 p99，再选择较少 pages/query。
- 冻结 `BudgetProfile` 和 test 查询顺序哈希。

### 7.5 `run`

- 只读取冻结的 profile 和 800 条 test 查询。
- 五次重复采用轮换后的方法顺序，避免时间漂移总偏向同一方法。
- 每个重复使用独立进程和空的应用缓存。
- 任何 OOM、短读、checksum 错误或图哈希变化使整个配置失败，不只删除异常重复。

### 7.6 `plot`

生成：

1. Recall@10–QPS 曲线。
2. Recall@10–p95/p99 曲线。
3. 预算–QPS、预算–peak RSS 曲线。
4. 预算–resident bits/cache bytes 阶梯图。
5. pages/query、bytes/query 和 I/O rounds 对比。
6. route/Full1/fine4 升级比例和 beam 分布。
7. pipeline width、useful-read ratio、useful-records/page 和 read amplification。
8. cold 与 steady-state 的成对结果。

目标 Recall 点使用“实际 Recall 不低于目标的最高 QPS 配置”，不得对未测到的 Recall 点外插。

## 8. 测量指标

### 8.1 主要指标

- Recall@10。
- QPS。
- 单查询 p50、p95、p99 latency。
- cgroup `memory.current` 和 `memory.peak`。
- 唯一物理 4 KiB pages/query 和 bytes/query。
- read amplification = bytes read / useful record bytes。

### 8.2 诊断指标

- resident code bytes、factor bytes、两类 cache bytes。
- NodeChunk 和 Full1 cache hit/miss。
- logical expansions、visited count、distance evaluations。
- route、Full1、fine4 候选数量。
- ambiguous、deferred、promoted 和 hard-pruned 数量。
- I/O batch 数、round 数、in-flight 去重数和平均队列深度。
- useful-records/page、page-search CPU overhead 和 prefetch useful ratio。
- `beam_width` 与 `pipeline_width` 的时间序列，不能只报告平均值。
- 每查询实际 beam 的均值、分位数和直方图。
- CPU distance time、queue time、I/O wait 和 fine rerank time。

每个配置报告五次重复的 median、IQR 和 95% bootstrap confidence interval，并保留全部原始 query-level 数据。

## 9. 测试与验收

### 9.1 单元测试

- NodeChunkV3 序列化、跨页记录和 checksum。
- 嵌套前缀与完整 DB1 bitwise 一致。
- 内存规划器计入线程、cache metadata、I/O buffer 和 10% reserve。
- 不可行配置在启动前返回 `NoFeasibleProfile`，而不是运行后 OOM。
- 短前缀路径永不执行 hard prune。
- deferred 候选能在结束边界不稳定时被重新检查。
- dynamic beam 始终位于 `[beam_min, beam_max]`。
- 同一物理页的并发请求只保留一个 in-flight read。
- page-search 不得读取或越界解析相邻页的残缺 record。
- pipeline width 不得超过 `io_depth`，取消的预取必须计入 wasted-read。

### 9.2 集成正确性

固定 Full 路径的 memory reader 与 O_DIRECT reader 必须满足：

```text
max Recall@10 delta <= 0.001
mean top-10 overlap >= 0.99
visited count delta <= 1%
distance count delta <= 1%
short_read = 0
checksum_error = 0
```

所有预算和方法必须满足：

```text
Ours 受控消融的 graph_sha256 相同
Ours 受控消融的 index_sha256 相同；native_system_comparison 单独记录各自 hash
memory.peak <= budget
oom = 0
oom_kill = 0
swap = 0
```

### 9.3 研究假设验收

**H1：启动自适应可替代人工静态配置。**

在相同 Recall 目标下，`StartupAuto` 相对 `BestFixed` 的 QPS regret 不超过 5%。

**H2：降维自适应产生可测收益。**

在 GIST、512 MiB、workers=32、Recall@10 >= 0.95 时，`DimAdaptive-core` 与同预算下 validation 选出的 `BestFixed` 维度比较。主方法必须满足：

```text
Recall@10 不下降超过 0.005
median QPS regret <= 5%
pages/query 不增加
```

只有在主方法通过后，才单独评估 `DimAdaptive+Escalation` 以及包含动态 beam/cache/pipeline 的扩展系统。扩展系统必须同时满足：

```text
median QPS 提升 >= 10%
QPS 提升的 95% CI 下界 > 0
pages/query 降低 >= 10%
p99 latency 退化 <= 5%
prefetch useful ratio >= 0.60
总 I/O 数相对 lockstep 不增加 > 5%
```

如果 `DimAdaptive-core` 通过而 `DimAdaptive+Escalation` 或 `FullAdaptive-system` 未通过，结论仍只保留降维自适应结果，并将升级、页面和异步组件标记为未验证，不得把它们合并报告。

**H3：压力预算仍能安全退化。**

256 MiB 下应达到 Recall@10 >= 0.90 且无 OOM。若固定开销使所有 profile 都不可行，必须报告 `NoFeasibleProfile` 和实际内存下界，不能提高预算后仍把结果标记为 256 MiB。

正确性门槛通过但 H1/H2 未通过时，结论应写为“系统实现可行，但当前自适应策略未优于最佳静态配置”，不得宣称性能收益。

## 10. 并发运行与硬件约束

索引导出、单元测试和小规模 parity 可以与不访问相同输出目录的 CPU 任务并行，但正式性能实验不能与以下任务同时运行：

- Vamana/HNSW 图构建；
- 其他 ANN 调参或吞吐测试；
- 对同一块设备的大文件转换、下载、校验或 fio；
- 会改变 CPU 频率、NUMA 带宽或磁盘队列的批处理任务。

原因是独立 cgroup 只能保证本方法的内存上限，不能隔离共享 CPU、内存带宽和块设备队列。后台任务不会直接计入本实验 cgroup 的 `memory.peak`，但会显著污染 QPS、尾延迟和 I/O 指标。

当前工作目录位于 `ROTA=1` 的块设备上。因此：

- 可以在当前机器生成索引、验证正确性和获得标记为 HDD/RAID 的 pilot 结果；
- 不得把当前结果表述成 NVMe/SSD 性能；
- 若需要 SSD 论文结论，formal run 必须在 `ROTA=0` 的本地 NVMe 上重复，并记录 CPU、内存、NUMA、SSD 型号、文件系统和 I/O scheduler。

## 11. 与已有方法的关系和创新边界

已有 DiskANN 已经支持按搜索内存预算选择压缩表示、缓存节点和 beam；AiSAQ 已经证明压缩导航数据可以完全放在存储侧；PipeANN、Starling、PageANN、VeloANN 和 OctopusANN 分别覆盖了异步 pipeline、页面级布局/搜索、record cache 和组合式 I/O 优化。因此本方案不得声称“首次提出预算自适应”“首次提出动态 beam”或“首次把导航码放到磁盘”。

本方案可主张的范围收窄为：在**同一固定 Vamana 图、同一份多层磁盘索引和严格总 RSS/cgroup 预算**下，将嵌套短码、Full1 保守升级、fine4 精排、预算感知 cache 以及独立 I/O pipeline 组合成一个查询阶段控制器，并在 GIST 上通过固定图哈希、内存峰值、Recall、页级 I/O 和尾延迟共同验证。

其中 `page_search`、`entry_navigator`、`hybrid cache` 和 `async pipeline` 都是可关闭组件，必须通过消融证明收益来源，不能把外部方法的已知组件直接计入核心创新。

## 12. 预期交付

完成本方案后应得到：

```text
docs/plans/GIST_ADAPTIVE_DISK_ANN_VALIDATION_PLAN.md
experiments/06_adaptive_disk_ann/
results/disk_environment/06_adaptive_disk_ann/gist/<run_id>/
  manifests/
  profiles/
  raw/
  csv/
  logs/
  figures/
```

正式报告必须同时给出正结果、负结果、不可行 profile 和硬件限制，确保结论不超过 GIST 实验能够支持的范围。

## 13. 当前实现与测试入口

Phase-1 的预处理实现位于：

```text
experiments/06_adaptive_disk_ann/adaptive_projection.py
experiments/06_adaptive_disk_ann/route_rerank.py
experiments/06_adaptive_disk_ann/test_adaptive_projection.py
```

当前实现已经覆盖投影拟合、嵌套 1-bit 编码、预算 profile 选择和 brute-force route/rerank smoke path；`route_rerank.py` 仅用于验证文件格式、bit packing 和 Recall 计算，不作为正式 QPS 结果。正式实验前还需将 `codes_d*.bin` 接入生产 C++ 图遍历，使低维 1-bit 码只参与候选排序、完整 DB1/fine4 负责精排。Phase-1 的验收命令和 GIST/多数据集运行示例见同目录 `README.md`。

## 参考资料

1. Microsoft DiskANN, “Usage for SSD-based indices.” <https://github.com/microsoft/DiskANN/blob/cpp_main/workflows/SSD_index.md>
2. Tatsuno, K. et al. “AiSAQ: All-in-Storage ANNS with Product Quantization for DRAM-free Information Retrieval.” arXiv:2404.06004. <https://arxiv.org/abs/2404.06004>
3. Gao, J. and Long, C. “RaBitQ: Quantizing High-Dimensional Vectors with a Theoretical Error Bound for Approximate Nearest Neighbor Search.” SIGMOD 2024. <https://arxiv.org/abs/2405.12497>
4. Gao, J. and Long, C. “High-Dimensional Approximate Nearest Neighbor Search: with Reliable and Efficient Distance Comparison Operations.” <https://arxiv.org/abs/2303.09855>
5. Guo, H. and Lu, Y. “Achieving Low-Latency Graph-Based Vector Search via Aligning Best-First Search Algorithm with SSD (PipeANN).” OSDI 2025. <https://www.usenix.org/conference/osdi25/presentation/guo>
6. Wang, M. et al. “Starling: An I/O-Efficient Disk-Resident Graph Index Framework for High-Dimensional Vector Similarity Search on Data Segment.” arXiv:2401.02116. <https://arxiv.org/abs/2401.02116>
7. Kang, D. et al. “Scalable Disk-Based Approximate Nearest Neighbor Search with Page-Aligned Graph (PageANN).” arXiv:2509.25487. <https://arxiv.org/abs/2509.25487>
8. Zhao, W. et al. “Optimizing SSD-Resident Graph Indexing for High-Throughput Vector Search (VeloANN).” arXiv:2602.22805. <https://arxiv.org/abs/2602.22805>
9. Li, L. et al. “I/O Optimizations for Graph-Based Disk-Resident Approximate Nearest Neighbor Search: A Design Space Exploration (OctopusANN).” arXiv:2602.21514. <https://arxiv.org/abs/2602.21514>
10. Chen, Q. et al. “SPANN: Highly-efficient Billion-scale Approximate Nearest Neighbor Search.” arXiv:2111.08566. <https://arxiv.org/abs/2111.08566>
11. Kusupati, A. et al. “Matryoshka Representation Learning.” arXiv:2205.13147. <https://arxiv.org/abs/2205.13147>
12. Yoon, J. et al. “Matryoshka-Adaptor: Unsupervised and Supervised Tuning for Smaller Embedding Dimensions.” EMNLP 2024. <https://aclanthology.org/2024.emnlp-main.576/>
13. Zhang, B. et al. “SMEC: Rethinking Matryoshka Representation Learning for Retrieval Embedding Compression.” EMNLP 2025. <https://aclanthology.org/2025.emnlp-main.1332/>
14. Fu, J. and Zhao, D. “MPAD: A New Dimension-Reduction Method for Preserving Nearest Neighbors in High-Dimensional Vector Search.” arXiv:2504.16335, 2025. <https://arxiv.org/abs/2504.16335>
