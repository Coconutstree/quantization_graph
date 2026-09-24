# 32 线程磁盘实验方案

## 1. 实验目标

本实验目标是在统一的磁盘查询环境下评估 Ours 与现有 ANN 方法的性能差异。正式主结果固定使用 32 个查询 worker；05B/05C 的 search-index DRAM budget 固定为 2 GiB，05A 的 payload-on-SSD 主结果记录同一预算，而 05A resident 仅作为不受该预算约束的纯计算参考并单独报告 resident bytes。重点比较不同方法在 Recall@10、QPS、尾延迟、索引大小、内存占用和磁盘 I/O 开销上的表现。

所有正式结果统一存放在：

```text
results/disk_environment
```

本轮实验包含三类：

| 实验 | 核心问题 | 主要用途 |
|---|---|---|
| 05A Quantizer Fair | 比较 nominal 4-bit 量化器的精度、计算和物理 I/O 开销 | 支撑量化方法有效性 |
| 05B Controlled Graph & Storage | PQ/SQ/SAQ 共用 baseline 图；Ours 在自有图上做存储机制消融 | 分析 payload、DB1 gate、I/O 合并和 page reuse |
| 05C System Fair | 比较完整磁盘 ANN 系统 | 正式报告主表和主图 |

## 2. 数据集

实验分为 3 个 core formal 数据集和 5 个 scale extension 数据集。Core formal 是当前验收范围；extension 只有在 fixed candidates、SAQ 产物、baseline shared graph、Ours graph 和五系统索引全部通过 doctor 后，才能进入正式汇总。

| 数据集 | 范围 | N | 维度 | 当前目录大小 | 作用 |
|---|---|---:|---:|---:|---|
| `agnews` | core formal | 769,382 | 1024 | 3.0 GiB | 中等规模文本 embedding |
| `dbpedia` | core formal | 990,000 | 1536 | 5.8 GiB | 高维文本 embedding |
| `gist` | core formal | 1,000,000 | 960 | 3.6 GiB | 经典视觉检索 benchmark |
| `sift10m` | extension | 10,000,000 base + 10,000 query | 128 | 正式三件套约 4.85 GiB；另有约 1.2 GiB 的 exact-GT `uint8` 辅助输入 | UCI SIFT10M（Caltech-256/VLFeat），不是 BIGANN/SIFT1B 子集 |
| `deep1B` | extension | 9,990,000 | 96 | 7.3 GiB | Deep1B 的约 10M 子集 |
| `msmarco` | extension | 113,520,750 | 1024 | 436 GiB | 大规模文本检索 embedding |
| `bigann10m` | extension | 10,000,000 | 128 | 4.9 GiB | 标准 BIGANN/SIFT 10M benchmark |
| `cohere10m` | extension | 10,000,000 | 768 | 29 GiB | 现代文本 embedding benchmark |

所有数据集必须在 manifest 中记录 `N/D/metric/k`、是否归一化以及输入 SHA-256。当前实现使用 exact float32 squared L2；文本 embedding 若要代表 cosine 检索，必须先验证 base/query 已归一化并将该状态写入 manifest。

UCI SIFT10M 上游只提供 11,164,866 条描述子，没有官方 ANN query/ground-truth 划分。本项目固定按上游 HDF5 行号划分：`[0, 10,000,000)` 为 base，`[11,154,866, 11,164,866)` 为 query，中间 1,154,866 行保留不用；base/query 不重叠。对应真值必须针对这 10M base 重新精确计算 squared L2 top-1000，不能复用 BIGANN 真值。

## 3. 统一实验设置

| 参数 | 设置 |
|---|---|
| workers | 32 |
| search-index DRAM budget | 05B/05C 主结果固定 2 GiB，约束 resident code/codebook/worker scratch/cache；05A resident 是不受该预算约束的纯计算参考，必须单独报告 resident bytes |
| query split | 策略默认值：AGNews/GIST/SIFT10M/MSMARCO 为 200 validation；DBpedia/Deep1B/BIGANN10M/Cohere10M 为 1000 validation。必须满足 `0 < validation < total`，validation/test 均非空，split 文件及 SHA-256 固定到 immutable run |
| process peak RSS | 单独报告，不与 search-index DRAM budget 混为同一指标 |
| repeats | pilot = 1；formal = 1，不要求五次重复 |
| seed | 20260813 |
| page size | 4096 bytes |
| disk profile | auto |
| disk root | `work/05_disk_system_fair/disk_root` |
| output root | `results/disk_environment` |
| run order | doctor -> export -> validate -> tune -> run -> plot |

I/O 统一约束为 `O_DIRECT`、4 KiB 对齐页、最大 128 个 in-flight I/O 和逐查询计数。官方 DiskANN 使用 `io_uring`，其他 disk port 使用 libaio；两者都属于 direct asynchronous I/O，但不应表述为完全相同的 I/O backend。

每个数据集分两轮运行：

| 阶段 | repeats | 作用 | 是否进入正式结果 |
|---|---:|---|---|
| pilot | 1 | 检查数据、端口、I/O 和参数范围 | 否 |
| formal | 1 | 生成单次正式测量结果 | 是 |

run id 命名格式：

```text
disk_<dataset>_w32_pilot_YYYYMMDD
disk_<dataset>_w32_formal_YYYYMMDD
```

## 4. 数据存放方式

实验数据分为原始输入、磁盘索引、运行产物和正式结果四类。正式报告只引用 `results/disk_environment` 下发布出来的 CSV 和图。

| 类型 | 存放位置 | 内容 | 用途 |
|---|---|---|---|
| 原始输入数据 | `data/<dataset>/` | base 向量、query 向量、ground truth | 所有方法共用，不能在实验中修改 |
| 磁盘索引工作区 | `work/05_disk_system_fair/disk_root/` | native port 导出的图页、payload 页和系统索引 | 查询阶段实际从这里按 4 KiB page 读取 |
| 单次运行产物 | `results/disk_environment/.formal_runs/runs/<run-id>/` | 每个 phase 的 artifact、query trace、terminal log、manifest | 审计、复现和失败排查 |
| 正式发布结果 | `results/disk_environment/{01_quantizer_fair,02_diskann_fair,03_system_fair}/<dataset>/` | 聚合 CSV、日志、图和元数据 | 正式报告和画图使用 |

每个数据集的输入文件约定为：

| 文件 | 含义 |
|---|---|
| `data/<dataset>/<dataset>_base.fvecs` | base vectors |
| `data/<dataset>/<dataset>_query.fvecs` | query vectors |
| `data/<dataset>/<dataset>_groundtruth.ivecs` | ground truth nearest neighbors |

正式发布目录结构：

```text
results/disk_environment/
  01_quantizer_fair/<dataset>/{csv,logs,figures,manifests}/
  02_diskann_fair/<dataset>/{csv,logs,figures,manifests}/
  03_system_fair/<dataset>/{csv,logs,figures,manifests}/
```

其中：

| 子目录 | 内容 |
|---|---|
| `csv/` | 聚合后的实验结果表 |
| `logs/` | native port 的终端日志 |
| `figures/` | 由正式结果生成的图 |
| `manifests/` | 输入 hash、binary hash、run id、preflight 和完整性检查记录 |

## 5. 05A: Quantizer Fair

05A 只比较量化器本身，不比较完整图搜索系统。所有方法使用同一数据集、查询、fixed candidate IDs/顺序和 nominal 4-bit 设置。不同编码的 factor、scale、残差和对齐填充会导致实际 bytes/vector 不同，因此必须同时报告 effective bits/dim 和 read amplification，不宣称物理存储预算完全相同。

| 项目 | 内容 |
|---|---|
| Ours | `Ours_RaBitQ_K1` |
| Baselines | `PQ_4bit`, `SQ_4bit`, `SAQ_B4` |
| 主结果模式 | `payload_on_ssd`，payload 放在 SSD 上并按需读取 |
| 参考模式 | `resident`，payload 常驻内存，只用于检查量化误差，不进入主表 |
| 扫描参数 | fixed-candidate rerank/search width |
| 比较指标 | fixed-candidate Recall@10、QPS、量化误差、code bytes/vector、effective bits/dim、index size、resident bytes、bytes/query、read amplification |

05A 用来回答：

```text
在相同 nominal 4-bit 设置下，Ours 的距离估计是否更准确？
当 payload 放在磁盘上时，Ours 是否仍能保持更高 QPS 和更低 I/O 开销？
```

05A 输出：

| 输出 | 路径 | 说明 |
|---|---|---|
| 原始结果 CSV | `01_quantizer_fair/<dataset>/csv/formal_test_rows.csv` | 每个方法、搜索宽度、repeat 的完整结果 |
| Pareto CSV | `01_quantizer_fair/<dataset>/csv/formal_test_frontier.csv` | Recall-QPS Pareto frontier，用于画曲线 |
| 正式主表 CSV | `01_quantizer_fair/<dataset>/csv/formal_test_rows.csv` | 单次正式测量结果，`repeat_id=0` |
| 日志 | `01_quantizer_fair/<dataset>/logs/*.terminal.log` | native 量化器端口运行日志 |
| 元数据 | `01_quantizer_fair/<dataset>/manifests/*.json` | 输入、run id、hash、preflight 等记录 |
| 图 | `01_quantizer_fair/<dataset>/figures/disk05a_quantizer_fair_summary.*` | 量化器 QPS 与误差汇总图 |

05A CSV 重点字段：

```text
method, storage_mode, search_width, recall, qps,
mean_relative_error, p95_relative_error, pairwise_flip_rate,
code_bytes_per_vector, effective_bits_per_dim, read_amplification,
index_size_mb, resident_bytes, bytes_read_per_query,
workers, repeat_id, run_id
```

## 6. 05B: Controlled Graph and Storage Ablation

05B 保留实验 02 的真实算法语义：PQ/SQ/SAQ 使用同一张 float32 baseline Vamana graph；Ours 因 ExRaBitQ4-symmetric 构图要求使用自己的图。Ours 的四级 ablation 全部在同一张 Ours graph 上运行，用于隔离 DB1 gate、I/O 合并和 page reuse 的贡献。跨方法结果仍包含构图与搜索语义差异。

| 项目 | 内容 |
|---|---|
| Ours | `Ours-Disk` |
| Baselines | `PQ-DiskANN-Disk`, `SQ-DiskANN-Disk`, `SAQ-DiskANN-Disk` |
| 控制变量 | PQ/SQ/SAQ 同一 graph SHA-256；Ours ablations 同一 Ours graph SHA-256；全部同一查询集、32 workers、2 GiB budget 和 I/O 计数口径 |
| 主结果模式 | `hybrid_disk` |
| 参考模式 | `disk_payload`，只用于分析 payload-on-disk 的单独影响，不进入主表 |
| 扫描参数 | search width |
| 比较指标 | Recall@10、QPS、p50/p95/p99 latency、visited nodes、distance evaluations、I/O requests/query、bytes/query |

05B 用来回答：

```text
PQ/SQ/SAQ 在 shared baseline graph 上的 payload 编码差异如何？
Ours 在自己的固定图上，DB1 gate、coalescing 和 reuse 是否逐步减少磁盘读取？
Ours 的 DB1 gate 是否减少 full 4-bit payload 访问？
```

Ours ablation 含义：

| ablation | 含义 | 用途 |
|---|---|---|
| `full4-resident/no-gate` | full 4-bit payload 常驻内存，不使用 DB1 gate | 作为无磁盘 payload 读取的上界参考 |
| `db1-resident/full4-on-ssd` | DB1 gate 常驻内存，full 4-bit payload 放在 SSD | 衡量 DB1 gate 对 full payload 读取的过滤效果 |
| `db1+coalescing` | 在 DB1 gate 基础上，合并同一 query 内相同或相邻 page 的读取请求 | 衡量 I/O 合并的贡献 |
| `db1+coalescing+reuse` | 在上一项基础上，同一 query 内重复访问的 page 只读一次 | Ours 正式配置，用于主结果对比 |

`db1+coalescing+reuse` 可以理解为 Ours 的完整磁盘查询优化：先用 1-bit DB1 近似距离过滤候选点，再把需要读取 full 4-bit payload 的请求合并成更少的 4 KiB page 读取；如果同一个 query 再次访问已经读取过的 page，则直接复用，避免重复 I/O。

05B 输出：

| 输出 | 路径 | 说明 |
|---|---|---|
| 原始结果 CSV | `02_diskann_fair/<dataset>/csv/formal_test_rows.csv` | 每个方法、ablation、搜索宽度、repeat 的完整结果 |
| Pareto CSV | `02_diskann_fair/<dataset>/csv/formal_test_frontier.csv` | Shared graph 下的 Recall-QPS Pareto frontier |
| 正式主表 CSV | `02_diskann_fair/<dataset>/csv/formal_test_rows.csv` | 单次正式测量结果，`repeat_id=0` |
| 日志 | `02_diskann_fair/<dataset>/logs/*.terminal.log` | 05B native shared-graph 端口运行日志 |
| 元数据 | `02_diskann_fair/<dataset>/manifests/*.json` | graph、输入、artifact hash、完整性检查记录 |
| 图 | `02_diskann_fair/<dataset>/figures/disk05b_shared_graph_recall_qps.*` | baseline shared-graph 与 Ours fixed-native-graph Recall-QPS 曲线；文件名为兼容旧结果保留 |

05B CSV 重点字段：

```text
method, ablation, storage_mode, search_width, recall, qps,
latency_p50_us, latency_p95_us, latency_p99_us,
visited_nodes, distance_evaluations,
db1_checks, db1_survivors, full4_page_reads,
io_requests_per_query, bytes_read_per_query,
index_size_mb, peak_rss_bytes,
workers, repeat_id, run_id
```

## 7. 05C: System Fair

05C 比较完整磁盘 ANN 系统。每个系统使用自己的正式索引结构、payload 编码、距离计算 kernel 和磁盘查询路径。DiskANN-PQ 是官方 native disk path；SymphonyQG、OG-LVQ 和 Glass-NSG 是保留官方图/量化核心的研究性 disk ports，不宣称其上游官方实现原生支持该磁盘模式。

| 项目 | 内容 |
|---|---|
| Ours | `Ours-Disk` |
| Baselines | `DiskANN-PQ-Disk`, `SymphonyQG-DiskPort`, `OG-LVQ-DiskPort`, `Glass-NSG-DiskPort` |
| 控制变量 | 同一数据集、同一 query/ground truth、同一 workers、同一 2 GiB budget、同一 I/O 口径 |
| 主结果模式 | `hybrid_disk` |
| 扫描参数 | 各系统等价的搜索强度参数，如 search width / ef / beam |
| 比较指标 | Recall@10、QPS、p50/p95/p99 latency、index size、peak RSS、I/O requests/query、bytes/query |

05C 用来回答：

```text
在完整系统对比中，Ours 是否能在相同 Recall@10 下达到更高 QPS？
Ours 是否降低尾延迟、磁盘读取量和内存占用？
Ours 的索引大小是否具备优势或竞争力？
```

05C 输出：

| 输出 | 路径 | 说明 |
|---|---|---|
| 原始结果 CSV | `03_system_fair/<dataset>/csv/formal_test_rows.csv` | 每个系统、搜索参数、repeat 的完整结果 |
| Pareto CSV | `03_system_fair/<dataset>/csv/formal_test_frontier.csv` | 完整系统 Recall-QPS Pareto frontier |
| 正式主表 CSV | `03_system_fair/<dataset>/csv/formal_test_rows.csv` | 单次正式测量结果，`repeat_id=0` |
| 日志 | `03_system_fair/<dataset>/logs/*.terminal.log` | 05C 各系统 native port 运行日志 |
| 元数据 | `03_system_fair/<dataset>/manifests/*.json` | binary hash、实现指纹、输入 hash、preflight 等记录 |
| 图 | `03_system_fair/<dataset>/figures/disk05c_system_recall_qps.*` | 完整系统 Recall-QPS 曲线，作为正式报告主图 |

05C CSV 重点字段：

```text
method, storage_mode, search_param, search_width, beam_width,
recall, qps, latency_p50_us, latency_p95_us, latency_p99_us,
index_size_mb, peak_rss_bytes,
io_requests_per_query, bytes_read_per_query,
visited_nodes, distance_evaluations,
workers, repeat_id, run_id,
direct_io, native_aio, page_size, formal_ready
```

## 8. 搜索参数

主实验只扫描搜索强度，其他条件保持固定。

| 参数 | 设置 |
|---|---|
| workers | 32 |
| DRAM budget | 05B/05C 主结果固定 2 GiB；05A payload-on-SSD 记录 2 GiB，05A resident 参考不受该预算约束 |
| 05A candidate width | 10..30 step 1；40..100 step 10；140..580 step 40 |
| 05B/05C search width | 1..30 step 1；40..100 step 10；140..580 step 40 |
| 05A 主存储模式 | `payload_on_ssd` |
| 05B 主存储模式 | `hybrid_disk` |
| 05C 主存储模式 | `hybrid_disk` |

### 统一 DRAM 分层策略（05B/05C）

`search_dram_budget_gib` 不是“只要不超上限即可”，而是每个端口必须按同一顺序分配的**索引 DRAM 总额**。`resident_bytes`、`codebook_bytes`、所有 worker scratch 与跨查询缓存均须计入；`peak_rss_bytes` 必须是实际进程采样值，不能用估算值代替。

1. **C0（无跨查询缓存）**：不保留任意图页、量化页或节点行到下一条 query；允许同一 query 内去重和必需的 query/rotator/codebook scratch。用于验证索引在极低可选 DRAM 下能否直接从盘搜索。
2. **B（预算受限）**：先保留算法正确执行所必需且能完整放入预算的压缩路由码、码本和入口/导航数据；不能完整放入时，必须退化为按页直接读取并使用同一 query 内去重，不能静默把全量码常驻。随后以剩余字节数建立跨查询的 4 KiB 页缓存。
3. **缓存规则**：所有方法使用相同的 byte cap、LRU 逐出规则和 warm-up/query order。缓存对象只能是后续 query 实际可能使用的图页或量化页；禁止为了“预取”或计费而读取尚未决定展开/重排的候选节点。缓存命中、未命中、逐出、实际缓存字节及每类页的占用必须写入 trace/artifact。

SymphonyQG 的邻居近似距离由当前节点行中随边保存的 FastScan payload 计算。候选 `v` 在尚未决定展开或最终重排前不得读取其整行；若该布局无法支持该规则，必须改为把必要 payload 分离存储，不能以候选预读替代搜索策略。

正式比较必须包含 C0 及至少三档 B（从“仅够必需常驻数据”到预算能够缓存热点图页）。同一张主图只能比较同一 budget/cache policy 下的结果；不同 budget 只用于画 memory-sensitivity 曲线。预算不足时应报告 `minimum_required_bytes` 与 disk-streaming fallback，而不是缩小数据集或把未计入的内存当作免费资源。

## 9. 结果使用规则

| 用途 | 文件 |
|---|---|
| 正式报告主表 | `formal_test_rows.csv`（单次正式测量） |
| Recall-QPS 曲线 | `formal_test_frontier.csv` |
| 单次运行排查 | `formal_test_rows.csv` |
| 运行日志 | `logs/*.terminal.log` |
| 实验审计 | `manifests/*.json` |
| 正式报告图 | `figures/*.svg`, `figures/*.pdf`, `figures/*.png`, `figures/*.tiff` |

正式结果必须满足：

```text
workers = 32
repeats = 1
repeat_id = 0
metric_compatible = true
validation_queries > 0
test_queries > 0
05B/05C accounted search DRAM <= 2 GiB
05B/05C cache policy and byte cap are identical across methods for a given run
05B/05C peak_rss_bytes is a measured process value and >= accounted search DRAM
disk-backed mode: direct_io = true and io_backend is audited
05A resident mode: io_backend = resident and direct_io = false
page_size = 4096
formal_ready = true
```

每个 operating point 只要求一次正式测量，`repeat_id=0`，主表使用可追溯到该次原始 artifact 的 `formal_test_rows.csv`。不再要求 `{0,1,2,3,4}` 五次重复或 `median_of_5` 聚合；不得把单次结果命名为 `formal_test_median.csv` 或伪装成多次运行的中位数。历史多次测量文件保留其原始含义，不改写为本轮结果。

单次运行报告 Recall、QPS 和查询延迟分位数，并明确 `repeats=1`。不要求跨运行 IQR、CV 或 bootstrap 95% CI，也不得由一次运行伪造跨运行不确定性。单次运行中的 p50/p95/p99 是查询延迟分布的分位数，不是重复实验的统计量；p95 作为主要尾延迟，p99 暂作诊断。若额外计算查询级 bootstrap，必须注明重采样单位，不能将其解释为跨运行稳定性。不得通过反复重跑并只保留“稳定”批次来消除波动。MSMARCO 当前只有 1,677 个 query；在 p99 进入主表前，必须扩充独立 test queries 或将 validation 规模降低到能保留足够 test queries。

不满足上述条件的结果仅作为诊断结果，不进入正式报告主表或主图。`repeat_id=0` 本身不再构成降级理由，但缺少原始 artifact、仅使用 validation 划分、未按规范预热或未通过内存预算及实现一致性验收的结果仍为 legacy/diagnostic，不能仅修改标签升格为 formal。正式主表和曲线必须来自同一 immutable run ID 下的独立 test 测量。

2026-09-11 按用户要求将正式重复次数改为 1。runner 的单次重复门禁、参数点去重及绘图入口已同步为 `formal_test_rows.csv`，不再生成五次中位数；相应契约测试通过。完整内存计量与实现一致性仍须独立验收，不能因取消重复次数而跳过。当前后台 validation 查询不因本次规范修改自动成为正式结果。

## 10. Revision 审计与修复记录

Revision 36 已修正 Cohere10M 维度、05B 图关系和 2 GiB 预算定义，但审计发现数据来源、metric、query split、预算可行性、Doctor 路径和统计契约仍有问题。Revision 41 加入数据集策略和正式运行门禁；Revision 45 进一步统一预算适用范围、split 默认值和 SIFT10M 占用口径。以下记录是正式执行前必须保留的完整问题清单。

| 编号 | 审计问题与风险 | 修复方式 | 当前状态 |
|---|---|---|---|
| R01 | 早期 `bigann10m` 曾硬链接复用 `sift10m`，不能证明是独立官方 BIGANN 输入。 | 从 TexMex BIGANN/SIFT1B 官方 base/query/`idx_10M` 重新转换，保留独立文件并在 formal manifest 固定 SHA-256。 | 已修复；当前 base 与 SIFT10M inode 不同。 |
| R02 | 早期 `cohere10m` 曾从本地 MSMARCO/Cohere 数据派生，不是官方 benchmark。 | 从 OpenSearch Benchmark 官方 `documents-10m.hdf5` 的 `train/test/neighbors` 生成三件套，保留下载缓存和转换脚本。 | 已修复数据来源。 |
| R03 | Cohere10M 表中维度曾写错。 | 以官方 HDF5 shape 和 fvecs header 为准，固定为 `D=768`。 | Revision 36 已修复。 |
| R04 | 文档曾暗示 05B 四种方法完全共享图，掩盖 Ours 的构图语义差异。 | 明确 PQ/SQ/SAQ 共用 float32 baseline Vamana graph；Ours 使用独立 ExRaBitQ4-symmetric graph，Ours ablation 仅在自己的固定图内比较。 | Revision 36 已修复。 |
| R05 | Cohere10M 官方 ground truth 按 inner product 排序，L2/cosine 对抽查 top-100 均有 49 次顺序违例。L2 runner 直接运行会产生无效 Recall。 | formal admission 检查 `source_metric/runner_metric/normalized`；当前阻止 Cohere10M。后续必须实现并验证 IP/MIPS native ports，不能简单归一化后沿用官方真值。 | 门禁已修复；IP/MIPS 能力待实现。 |
| R06 | 旧默认 `--val-queries=1000` 会把 AGNews/GIST 的 1000 条 query 全部分给 validation，得到空 test。 | 使用数据集策略自动选择 validation 数量，强制 `0 < validation < total` 并校验 query/GT 行数；AGNews/GIST 为 `200+800`，DBpedia 为 `1000+9000`。 | 已修复并已生成 core split。 |
| R07 | 2 GiB 被机械套到不可能的数据集：MSMARCO DB1 下界约 13.53 GiB，Cohere full4-resident 下界约 3.58 GiB。 | 在 fio/export 前计算不可避免的 resident-code 下界；超预算立即拒绝。05B/05C 主结果固定 2 GiB，05A resident 仅作无预算纯计算参考。 | 门禁已修复；MSMARCO 05B/05C 当前禁止。 |
| R08 | Doctor 查 `results/03_system_fair/...`，split helper 写 `results/disk_environment/03_system_fair/...`，导致同一产物被误报缺失。 | Doctor 与 helper 共用 `query_split_candidates()`，canonical 路径统一到 `results/disk_environment/03_system_fair/<dataset>/csv/_query_splits`，并验证完整性与条数。 | 已修复。 |
| R09 | Core formal 曾缺少三套 shared graph 和三套 Ours graph，Doctor 无法放行。 | 用固定 `R=64/Lbuild=400/alpha=1.2/seed=20260813` 重建，统一安装到 `results/graph/<dataset>/{shared_graph,Ours}/`；旧的 `results/<dataset>/indexes/02_diskann_fair/` 仅保留兼容软链接。SIFT10M 完成后不再构建 BIGANN10M/Deep1B 的 02/03 图，只为 AGNews、GIST、DBpedia、SIFT10M 准备 03/05C 五系统各自的查询就绪图索引；Ours 复用 02 图，DiskANN 复用 canonical shared graph，SymphonyQG 使用更新后的实现。当前阶段只做 export/build，不运行 query、tune、plot、formal 或 worker scaling。 | Core 6/6 已完成；UCI SIFT10M Ours 图继续后台构建，四数据集 03 图索引队列等待中。 |
| R10 | 旧验收条件无条件要求 `direct_io=true/native_aio=true`，会错误拒绝合法的 05A resident 行。 | 改为条件契约：disk-backed 必须 `direct_io=true` 且 backend 经审计；05A resident 必须 `io_backend=resident/direct_io=false`。 | 已修复并有 contract test。 |
| R11 | 文档定义 `pilot=1` 和 pilot run ID，但 CLI 没有独立 `pilot` phase。 | 二选一：正式定义 `QG05_FAST=1 + repeats=1 + diagnostic output` 为 pilot，或增加显式 `pilot` phase；在实现前不得把普通 `run` 称为 pilot。 | 待实现。 |
| R12 | “固定 32 workers”与 GIST 自动运行 `1/4/8/16/32` 不一致。 | 明确 w32 是主结果，GIST 的 `1/4/8/16/32` 仅为额外 scaling；完整性检查按主结果和诊断矩阵分别处理。 | 文档已修复，代码行为保留。 |
| R13 | SIFT10M 表曾把正式输入、下载缓存和错误复用的 legacy SymphonyQG 索引混在一起。 | 分开记录 UCI 官方 ZIP/MAT 下载缓存、正式 base/query/GT 三件套和 exact-GT `uint8` 辅助输入；图索引只放在 `results/`，不放进 `data/`。 | 本地规范已修复；新 UCI 产物正在后台重建。 |
| R14 | 旧 dataset/formal manifest 没有完整记录 metric、归一化状态和输入 SHA-256。 | 新 input manifest 使用 schema 2，记录 `N/D/source_metric/runner_metric/k`、归一化状态及 base/query/GT 的 size 和 SHA-256；旧 manifest 继续标记 legacy。 | 新 formal run 已实现；旧文件不追认。 |
| R15 | 历史五次重复规范只实现部分 IQR/CV；AGNews/GIST 800 条 test query 在 w32 下也不足以稳定解释 p99。 | 2026-09-11 改为单次正式测量，不再强制跨运行 IQR/CV/CI；p95 作为主要尾延迟，p99 暂作诊断。最短测量时长或 query epoch 属于测量时长设计，不等于独立重复，不得伪造重复次数或挑选性保留低波动批次。 | 单次运行门禁、聚合及绘图入口已同步；尾延迟测量时长仍待验证。 |
| R16 | 旧的本地 `sift10m` 三件套实际来自 BIGANN/SIFT1B 前 10M：base SHA-256 与 `bigann10m` 相同，query 也是 BIGANN query 的前 1000 条，因此不能代表独立的 UCI SIFT10M。 | 保留正确的官方 `bigann10m`；将错误 SIFT 别名及旧 manifest 归档。从 UCI DOI `10.24432/C5S603` 重新下载 11,164,866 条描述子，固定 `[0,10000000)` 为 base、最后 10,000 行为 query、中间行保留不用，重算 exact squared-L2 top-1000 GT，并重建 split、fixed candidates、SAQ 和图索引。 | 归档已完成；UCI 下载与完整重建流水线正在后台运行。 |

Revision 审计的放行原则是：文档修正不等于算法能力已经具备。Cohere10M 和 MSMARCO 目前是被正确门禁阻止；只有 metric 实现、预算设计和全部正式产物真正通过 Doctor 后，才能从 extension 升格为 formal。
