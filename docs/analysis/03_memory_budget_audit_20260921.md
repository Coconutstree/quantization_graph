# 03 的 2 GiB 预算一致性核查

后续共同预算预检已将新版 03 主实验统一改为 **4 GiB**，见
[选择依据及真实 RSS 预检](03_common_budget_20260921/report.md)。本文件以下保留原 2 GiB 核查时的规则及历史证据，
不代表新版默认预算；旧结果没有被重新标注。

后续更新：Ours 已接入 [hot+dynamic 正式策略](ours_formal_hot_dynamic_20260921.md)，
下文“尚未接入”描述的是本次初始核查时的状态。历史测量和 baseline 状态不因此改变。

核查日期：2026-09-21。范围：当前 03 入口、方法注册、资源测量与验收代码，以及
`results/03_disk_system/*/test_L_400_w_32/raw/*/memory_measurement.json` 的现有记录。
本次运行协议回归测试，没有重新运行全量数据集搜索。

结论：**当前共享入口统一使用 2 GiB 规划预算和进程峰值 RSS 验收；尚不能声称所有方法都已经实测通过。**
这不是操作系统强制的 2 GiB 上限。历史地址空间限额结果也不是当前 RSS 协议的实测结果。

## 当前入口的实际规则

- 03 的 primary/thread_scaling 固定 `--search-dram-budget-gib 2`，即 2,147,483,648 bytes。
  预算扫描属于独立 05；03 拒绝其他预算。建图/导出不属于搜索预算验收。
- 每个方法的原生命令接收同一个预算；单独指定 baseline=4 GiB、共同预算=2 GiB 会被拒绝。
- validation/test 都经过相同 `run_measured(..., rss_budget=True, budget_bytes=2147483648)`。
  03 的七个方法定义均为 `hybrid_disk`，没有借用不受预算验收的 resident reference 分支。
- 搜索进程不设置 cgroup 或 RLIMIT_AS 上限；启动时要求 RLIMIT_AS unlimited。
  `memory_enforcement=none`、`memory_limit_bytes=null`，不能写作“2 GiB 硬限制”。
- 观测范围包括加载、warmup、查询及同一进程的其他阶段。峰值取 wait4 和采样 RSS/HWM 的最大值；
  RSS 超过 2 GiB 或采样发现 swap，结果不准入。超额进程可以先运行完，再被拒绝。
- 这是单个搜索进程的 RSS 口径，不汇总子进程，也不等于包含系统文件缓存与内核内存的 cgroup 总量。
  资源证据绑定二进制哈希、命令、结果路径和采样文件；旧协议和无预算 observation 不能进入新协议。

代码：[预算与证据校验](../../src/disk_bench/protocol.py)、
[统一命令与测量](../../src/disk_bench/orchestrator.py)、
[进程测量器](../../src/disk_bench/memory_runner.py)。

## 方法覆盖情况

| 方法 | 当前注册状态 | 03 的 2 GiB 规则 | 仍需区分的情况 |
|---|---|---|---|
| Ours-Disk | ready | 统一规划及 RSS 验收 | ready 表示端口可调用，不代表全量正式准入通过 |
| DiskANN-PQ-Disk | ready | 同上 | 同上 |
| SymphonyQG-DiskPort | ready，补充移植方法 | 同上 | 不属于默认主方法集合 |
| Glass-NSG-DiskPort | ready，补充移植方法 | 同上 | 不属于默认主方法集合 |
| OG-LVQ-DiskPort | blocked | 规划规则相同，但注册检查拒绝运行 | 官方距离实现一致性未完成 |
| AiSAQ-Disk | pending | 共享正式入口尚未接通 | 独立 adapter 已默认 2 GiB RSS 验收，仍缺完整正式证据 |
| Starling-Disk | pending | 同上 | 独立 adapter 已默认 2 GiB RSS 验收，仍缺完整正式证据 |

后续已将 AiSAQ/Starling 独立 adapter 改为默认 `--memory-policy rss --search-memory-gib 2`。
`--memory-policy observe` 和 `--memory-policy cgroup` 保留为显式独立诊断；后者才使用 cgroup 上限。
这是内存验收入口的修复，不是全量官方搜索已满足预算的证据，也不会自动调整原生缓存/工作区。
默认 03 主方法集合包含两者，因此当前全方法正式运行会在注册检查时阻止继续。

依据：[方法注册](../../src/disk_bench/ports.local.json)、
[注册验收](../../src/disk_bench/native_contract.py)、
[官方独立适配器](../../experiments/03_disk_system/adapters/run_official_disk_baseline.py)。

## 历史结果是否统一

GIST 的 `test_L_400_w_32` 五个已有端口记录均显示命令预算为 2.0、
`rlimit_as_bytes=2147483648`，且 `hard_limit_verified=true`。
因此这一批五个端口的**地址空间限额**一致，但不是新的 RSS 预算结果。

| 方法 | 历史 RLIMIT_AS | 外部 wait4 峰值 RSS / MiB | formal_ready |
|---|---:|---:|---|
| Ours-Disk | 2 GiB | 583.40 | false |
| DiskANN-PQ-Disk | 2 GiB | 977.59 | false |
| SymphonyQG-DiskPort | 2 GiB | 159.46 | false |
| Glass-NSG-DiskPort | 2 GiB | 175.50 | false |
| OG-LVQ-DiskPort | 2 GiB | 178.93 | false |

数值来自各方法的 `memory_measurement.json.kernel_wait4_peak_rss_bytes / 2**20`，
不是原生程序可能重置过的内部峰值。原始记录目录：
[GIST raw](../../results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw)。

同范围内 AG News 的五个端口、DBpedia 的 Ours/SymphonyQG 共另外七条已测记录也均为 2 GiB RLIMIT_AS。
AG News、DBpedia 的 Starling 记录为 `status=not_measured`；GIST 没有 AiSAQ/Starling 的对应内存记录。
不能用这些缺失记录推断所有方法受到了相同约束。上述已有结果均不能直接升级为新协议正式点。

## 相同预算不等于相同缓存，也不等于用满预算

Ours 将 codes/factors、量化器与线程工作区等计入规划，余量进入现有 BFS 图页缓存；
DiskANN 预留 PQ/码本与线程工作区，余量进入其 BFS 节点缓存。两者静态缓存都有最多 10% 节点的限制。
SymphonyQG/Glass 的预算预检主要检查每 worker 的 4 MiB query cache；完整分配仍依赖外部 RSS 验收。
它们不会自动把剩余预算全部转为共享缓存。因此不能从“均设置 2 GiB”推断缓存优化程度也相同。

Ours 正式路径尚未接入 04 的 hot+dynamic；当前还有内存参考 parity 与搜索同进程的情况，
整进程 RSS 会把该参考阶段也计入。后续低预算正式测量需独立完成 parity 验收，不能通过忽略峰值来放行。

## 本次修正与验证

- 修正 orchestrator 两处过时的 `memory_comparison=ours_capped_baselines_native`，
  统一为 `shared_ram_budget_rss`。这只修正新运行的元数据，不修改搜索算法与历史结果；
  配置不同的既有 run-id 仍被拒绝复用。
- 扩展预算协议回归检查至全部七个 03 方法定义，覆盖 validation/test 相同预算及不匹配预算拒绝。
  共享测量器测试使用真实小内存 Python 进程，在 2 GiB 预算下验证资源证据；
  它不是七个原生搜索引擎的性能测试，也不会将 pending/blocked 方法升级为 ready。
- 内存协议 9 项、native contract 10 项、实验目录/03与05隔离 10 项，共 **29 项通过**。
  包含超预算进程正常退出后仍不准入、旧协议拒绝、pending 端口拒绝等检查。

正式可用的描述应为：“各已接入方法共享 2 GiB 搜索 RAM 预算，以整进程峰值 RSS 验收，无额外 OS 内存上限。”
全方法同预算 Recall–QPS 比较仍须补齐 pending 方法和当前协议的全量测量。
