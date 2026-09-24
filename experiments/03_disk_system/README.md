> 2026-09-24：03 当前方案为 **Ours、DiskANN、Starling、AiSAQ**；统一 4 GiB 峰值 RSS、32 workers、beam=4、单轮，九档宽度分别独立进程测量。取消性能选优。完整定义见 [本轮方案](../../docs/plans/03_FIXED_OFFICIAL_BEAM4.md)。下方日期章节保留历史实现背景，若与本轮方案冲突以本轮方案为准。

正式入口默认登记表为 `src/disk_bench/ports.fixed03.local.json`，使用独立新构建，不覆盖 05/06 二进制。官方 CLI 桥接位于 `src/disk_bench/official_system03.py`。执行例子（SSD_ROOT 必须是已核验 SSD/NVMe）：

```bash
python scripts/run_03_trusted_queue.py --tag NEW_SSD_RUN_ID \
  --ports src/disk_bench/ports.fixed03.local.json --disk-root "$SSD_ROOT" \
  --cpu-affinity "$CPU_LIST" --numa-node 0 --execute
```

不带 `--execute` 只生成队列；`--prepare-only --execute` 只准备索引。完整队列先做 validation 资源/正确性准入并冻结配置，再串行测量。报告重验每个来源及九档覆盖后生成单轮曲线、匹配 Recall 表和缺席说明。设备为 HDD 或无法核验时拒绝正式测量。Starling 的 AGNews/DBpedia 记录官方 FP32 页格式不支持。

# 03 完整磁盘系统

- `run.py`：本层运行入口。
- `native/`：Glass、SymphonyQG、OG-LVQ 补充磁盘端口、CMake 目标和相关 C++ 测试。
- `native_diskann/src/main.rs`：官方 DiskANN 磁盘端口。
- `native_diskann/src/cached_reader.rs`、`query_cache.rs`：对应 reader/cache。
- `adapters/run_official_disk_baseline.py`：官方 AiSAQ/Starling 命令与资源观测。
- `adapters/run_starling_existing_layout.py`、`recover_starling_search.py`：历史布局诊断与恢复。
- `adapters/svs_run.py`：OG-LVQ 需要的 SVS worker。

Ours 共用 `experiments/02_disk_shared_graph/native/` 和 `src/graph_core/`；不在本层复制相同算法。DiskANN 端口由 `baselines/diskann/diskann-disk/Cargo.toml` 注册；C++ 由公共 native 工程纳入本层 CMake 构建。统一构建见根 README。

```bash
python experiments/03_disk_system/run.py --phase doctor --datasets gist
```

正式准入、诊断结果与移植补充实验的界限保持原样。AiSAQ/Starling pending 不因目录调整变为 ready。

## 自动存储预处理（2026-09-19）

03 的 validate/test 磁盘搜索现在默认执行 `03_direct_sequential_search_files_v1`：
索引已导出且参数/输入检查完成后，搜索进程启动前，对实际磁盘搜索文件进行一次完整顺序 O_DIRECT 读取。
实现位于 `src/disk_bench/storage_precondition.py`；统一 orchestrator、官方 AiSAQ/Starling adapter 和 Starling 恢复入口共用它。无需为 Ours 单独传预读开关。01/02、导出阶段和 resident 参考路径不增加该操作。

| 方法 | 预读文件 |
|---|---|
| Ours BFS 布局 | graph_compact.pages、residual.pages |
| Ours 普通磁盘布局 | shared_graph.pages、ours_full4_residual.pages |
| DiskANN | diskann_pq_R64_L400_A1.2_disk.index |
| SymphonyQG | node_rows.pages |
| OG-LVQ | graph.pages、lvq4.pages |
| Glass | graph.pages、sq4u_codes.pages |
| Starling 官方 CLI | --disk_file_path 指定的实际分区索引 |
| AiSAQ 官方 CLI | prefix_disk.index、根据索引元数据选择的普通/重排 PQ 文件 |

不扫描原始 base/query/groundtruth、建图中间文件或只在内存加载的码本；预读顺序即表中顺序。
索引缺失或直接I/O失败时停止，不回退 buffered 读取，也不临时建索引后继续计时。
每次搜索进程前只执行一次，非逐查询或逐L档执行。原查询预热数量保持原样。

预读耗时在查询 QPS 之外单独记录。原生运行保存 `*.storage_precondition.json`，并在结果及CSV中保存协议、记录路径和哈希；官方适配器保存同名侧录，主清单/测量结果引用路径。日志记录每个文件的绝对路径、偏移/长度、字节数、SHA-256、inode、mtime、耗时以及失败原因。只有具备匹配协议及校验通过侧录的新结果才可被03复用。

此协议只统一操作，不保证控制器缓存命中率或所有索引都能驻留；`device_cache_controlled=false`。原有内存、算法等正式准入要求保持不变。旧结果不被改写或自动归为新协议。04历史诊断入口自己的none/direct开关仍保留，不属于03默认入口。

## 统一RAM预算协议（2026-09-21）

共享入口对Ours、DiskANN、SymphonyQG及其他磁盘方法使用相同
`--search-dram-budget-gib`。预算预检后，03 的 primary/thread_scaling 统一改为 **4 GiB**；0.5/2/4/8 GiB 的预算扫描已单独设为
[05 内存预算实验](../05_memory_budget/README.md)，03 不再接受 `--experiment-group budget_scan`。
预算用于原生编码/缓存配置；搜索启动时不增加OS内存上限，结束后按整个进程的RSS峰值验收。
RSS超预算或观测到swap均保留证据并拒绝作为合格点。VmPeak只用于诊断。
`--baseline-native-budget`若指定必须等于共同预算，不再允许只对Ours限额。
共享入口不接受`--cgroup-parent`；需要cgroup对照时使用独立显式测量入口。

协议ID为`disk_ram_budget_rss_20260921`。资源证据包含planned_budget_bytes、budget_admitted、
process_peak_rss_bytes、VmPeak、swap和分阶段RSS采样；memory_limit_bytes为空，memory_enforcement为none。
预算验收成功不会自动授予算法正确性或formal_ready。旧结果与新协议隔离，不修改历史文件。

[2 GiB 一致性核查](../../docs/analysis/03_memory_budget_audit_20260921.md)：共享入口规则一致，
但 AiSAQ/Starling 仍 pending；其独立 adapter 现默认 `--memory-policy rss --search-memory-gib 4`，
仅补齐预算验收，不代表计时、逐查询证据与算法一致性已经通过。显式 `observe` 或 `cgroup` 属于独立诊断。
此参数只验收实际 RSS，不自动改变官方 PQ 长度、缓存容量或工作区分配；超预算仍可能发生。
不能表述为所有方法均已实测通过。
历史 2 GiB RLIMIT_AS 结果与当前进程 RSS 预算协议分开报告。

4 GiB 的选择依据见 [共同预算预检](../../docs/analysis/03_common_budget_20260921/report.md)：
官方 Starling 的完整 GIST 索引、200 条 validation、32 workers、width 40/100/580，
新协议峰值 RSS 为 2,201,047,040 bytes（约 2.050 GiB），零 swap。
该内存诊断未绑定 NUMA 内存，不是正式性能结果；其余方法仍需按相同 4 GiB 和正式协议重跑。
入口拒绝在 03 primary 中显式保留 2 GiB，也拒绝单独给某 baseline 不同预算。
01/02 默认仍为 2 GiB；05 默认扫描点仍为 2 GiB。包含 03 的多层调用共用 4 GiB。
旧 run-id、参数锁和 2 GiB 曲线不重标为 4 GiB；历史脚本中的显式 2 GiB 不能当作新版 03 命令。
Starling 的高维节点页限制、AiSAQ/Starling 接入、OG-LVQ 官方一致性不会因预算增加而自动解决。

本次不改变PQ长度、图或距离核，也未新增SymphonyQG共享缓存策略；预算未用满如实报告。
低预算失败需要另行调整编码/缓存配置，不能仅凭配置参数声称RSS已满足预算。
GIST专用的RSS校准及缓存扫描见`experiments/04_ours_memory_budget/gist_same_ram/README.md`。

## Ours 低内存主方案：PCA + 1-bit（2026-09-21）

03/05 共用的 Ours-Disk 实现使用 `resident_full_else_pca1bit_m32_no_residual_v1`；预算切换的正式测量归入独立 05：

1. 完整原维度 1-bit、factors 和运行预留能同时常驻时，使用原来的搜索路径。
2. 放不下时，选择**预算内最高可常驻 PCA 维度**，采用 **PCA + 1-bit，不加 residual norm，M=32**。
3. 若最小前缀仍放不下，返回预算不足；正式分支不自动改回分页。分页及 residual norm 版本保留在 04 作为消融。

这里 M 是每次扩展的候选 shortlist 大小，不是图度数 R，也不是搜索 width。
PCA 仅用 base 样本拟合；不使用 validation/test queries 或 groundtruth 拟合。
对每次扩展，先用低维 1-bit 的 epsilon=0 分数排序取 top32，再用 epsilon=1.9 经验门控筛选；
随后读取完整维度 4-bit 并重算距离，最后保留原来的 residual 重排。未进入 top32 的 ID 可以在后续扩展中重新考虑。
低维 short_ip 不用于完整维度 4-bit 计算。**该门控不是经过证明的安全 hard prune**，Recall 仍需实测。

内存规划覆盖原始量化器、codes、20 bytes/点 factors、PCA mean/basis、低维旋转及查询缓冲、
每 worker 的 visited/缓存/工作区、未拆分查询文件的预留和 32 MiB 进程余量；不加载 residual norm。
原维度优先，否则遍历全部 PCA 前缀，按 native 补齐至 64/128/256/512/1024 bit 的实际开销选维度。
剩余预算先保留已有 BFS 图页缓存，再交给下面的 hot+dynamic 记录缓存。
规划值不等于 RSS，运行仍按整个进程 RSS 峰值和零 swap 验收。

现有 GIST 索引、32 workers 的**规划检查**结果：512 MiB 选择 128 维，预留合计 506.147 MiB，
剩余约 5.853 MiB；2 GiB 保留原始 960 维（1024-bit codes）。这不是新的性能测量，
也不能把旧 538 MiB 地址空间限额实验的 QPS 当成正式 512 MiB RSS 结果。

正式入口会在计时前准备/校验 PCA 资产，并保存 `.route_plan.json`；结果 JSON/CSV 记录维度、M、
policy、内存分项和资产哈希。validation 选 width/beam，test 校验路由规划及资产与 validation 一致。
当前实现标识为 `05c-ours-pca1bit-hot-dynamic-v4`，旧结果不升级为新方案结果。
02 的四组固定算法消融保持原定义，不自动引入 PCA。

实现：`src/disk_bench/ours_pca.py` 负责离线准备；共享 native 的 `pca.rs` 负责预算规划、编码及查询投影；
`ours_port.rs` 负责 M32 筛选和完整 4-bit 验证。PCA 与原维度量化器的线程内查询缓冲各自独立。

验证：预算边界及双量化器缓冲隔离单测通过；真实 O_DIRECT 小数据端到端测试中，完整与 PCA 分支
各 8 个查询均与内存参考保持 Top10、Recall、visited 和距离计算次数一致，并拒绝被改动的 PCA 文件。
此验证不授予新的 `formal_ready`；原有内存、存储和算法准入要求仍适用。全量正式 Recall–QPS 曲线尚未重跑。
现有 native 的内存参考 parity 会加载完整 payload，不能把该参考阶段也算作低内存搜索的 RSS 证据；
正式低预算测量的 parity 需独立验收，不能通过跳过 parity 或改写 `formal_ready` 冒充通过。
2026-09-21 后续已为 Ours/DiskANN 接入独立参考准备进程：预算内搜索结果和准备进程 direct 结果逐查询比对，
再结合准备进程的 direct/memory parity、整进程 RSS、存储操作证据与 contract 决定准入。
分类记账未知和控制器缓存未控制仍如实保留；历史结果不自动升级。
实现、真实小数据验证与各 baseline 剩余工作见 [正式准入修复](../../docs/analysis/03_formal_admission_20260921.md)。

## 正式 Ours 的 hot+dynamic（2026-09-21）

03/05 的 Ours-Disk、`cache-mode=standard` 默认启用 `validation_hot_then_dynamic_fifo16_v1`：

1. 按原策略选择完整 1-bit 或预算内最高 PCA 维度；不为缓存主动降低维度。
2. 扣除路由、工作区、查询/进程预留和已有 BFS 图页缓存，余额作为记录缓存的共同上限。
3. 计时前，用 validation 查询单独收集精细记录访问；每个 width/beam 的访问次数归一化后相加，
   按分数降序、ID 升序冻结正分热点。计数数组和排序空间只存在于离线准备进程。
4. 静态热点优先占用余额；剩余容量使用从 04 迁入的 16 分片 FIFO 动态记录缓存。
   缓存包含 compact 4-bit 与 residual 重排数据，不是 PCA residual norm。
5. 每个 width/beam 开始清空动态内容，保留静态热点；完成原 warmup 后只重置计数。
   查询中的查找、复制、插入和淘汰均计入 QPS 时间。

热点 manifest、排名文件及其哈希进入 validation 结果与 tuning lock；test 只复用冻结资产，
不使用 test 查询训练热点。资源预算、路由、图或二进制变化时必须重做匹配的 validation。
准备过程另存于 `*.hot_profile/`，其吞吐不可用作性能结果；正式搜索仍有独立存储预处理与 RSS 验收。

缓存计账包括静态/动态数据、密集 ID 映射、槽位、有效位、锁和分配余量；余额不足时容量可为 0，
不超配，也不强行用满预算。`c0` 和 02 固定消融保持原路径。
当前正式索引中 compact/residual 同记录存放，所以一次实际读取已有两部分时同时填入动态缓存；
04 分离页布局的按需填充思想仍保留，不新增补齐 residual 的 I/O。

JSON 的 `ours_record_cache_stats` 按 width/beam 记录静态命中、动态命中/未命中、插入/淘汰、
容量、有效数据量及预留字节；CSV 导出相同分项。`cache_bytes` 是 BFS 与记录缓存合计。

实现：[缓存与热点准备](../../src/disk_bench/ours_records.py)、
[原生缓存](../02_disk_shared_graph/native/src/record_cache.rs)、
[04 FIFO 的正式副本](../02_disk_shared_graph/native/src/dynamic_records.rs)。
验证与当前限制见 [接入记录](../../docs/analysis/ours_formal_hot_dynamic_20260921.md)。
这次接入不自动授予 `formal_ready`，也不将 04 历史 QPS 作为新版性能数字。

### SymphonyQG 跨查询页缓存

`SymphonyQG-DiskPort` 的 `standard` 模式使用同一索引、同一搜索 width 内共享的 16 分片 LRU 页缓存；每次切换 width 重新创建，预热后保留内容，测量计数仅包含测量查询。查询内的 4 MiB LRU 仍作为一级缓存。读取顺序为查询缓存、共享缓存、原 O_DIRECT 合并读取；不改变候选队列、距离公式或搜索顺序。并发缺页允许重复读取，不持锁等待 I/O。

共享缓存固定预分配，包括页数据、哈希表、LRU 元数据和锁/对象开销。自动额度从共同进程预算扣除当前 RSS、64 MiB 进程余量及每 worker 的 16 MiB 与队列预留后计算；这只是保守规划，整进程 RSS 验收仍决定是否超预算。原生参数 `--shared-page-cache-bytes 0` 可关闭以作对照，正值必须不超过剩余额度；`c0` 禁用共享缓存。结果记录 `shared_page_cache_policy`、`cache_bytes`，逐查询记录共享命中/未命中和物理 I/O。

该缓存只改变存储访问，继续沿用 v4 索引布局指纹，并由独立的缓存策略字段及二进制 SHA 区分搜索实现。缓存测试通过不会自动把 `formal_ready` 改为 true。诊断比较入口：`scripts/preflight_symphony_shared_cache.py`（GIST validation、4 GiB、32 workers、两轮反序、width 40/100/580；输出目录必须不存在）。

验证报告：[GIST 共享缓存两轮对照](../../results/diagnostics/symphony_shared_cache_20260921_verified/report.md)。逐查询有序 ID、Recall、访问节点数和距离计算次数一致；正式准入状态保持不变。

### 九档搜索宽度（2026-09-21）

03/05 的五个本地原生端口默认扫描 `10 / 20 / 40 / 60 / 100 / 160 / 240 / 400 / 580`。01/02 原有扫描不变。AiSAQ/Starling 待接入正式流程后也需遵守这一配置，不能把未运行点当作已覆盖。

2026-09-21 用户确认本轮 GIST 使用 `--fixed-beam 4`：Ours、DiskANN 的热点采集（Ours）、validation 和 test 均只使用 beam=4，各跑九档 width；参数写入协议并与 test 参数锁核对。Glass、SymphonyQG 保留各自搜索机制和 beam=1 标记，不宣称各方法实际 I/O 并发相同。05 队列沿用这一控制设置以比较预算变化。旧的多 beam 扫描及 beam=8 test 留作诊断，不混入本轮论文对比。省略该显式参数的其他运行仍保留原扫描行为。

后续用户进一步确认：固定参数模式取消 validation 性能扫描与选参。`--method-pipeline --fixed-beam 4` 仅从 validation 查询采集 Ours 热点（可校验后复用），写入 `fixed_parameters.lock.json`，直接执行各方法九档 test；test 的独立参考、存储与 RSS 验收保留。锁文件明确 `parameter_source=user_fixed`，没有 validation Recall/QPS 选优记录，不能称为 validation 选出的最佳配置。历史 validation 性能记录不作为本轮参数选择依据。
