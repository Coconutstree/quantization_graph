# Ours 2 GiB、baseline 各自配置（2026-09-17）

用户最新决定：**Ours 为 2 GiB，其他方法用各自配置，不要求预算或实际内存相等。** 正式仍为 32 个查询 worker，每个配置只跑一次。本轮没有运行全量实验。

## 现在怎样执行

| 项目 | Ours 磁盘主实验 | Baseline 主实验 |
|---|---|---|
| 外层内存限制 | cgroup `memory.max=2147483648`、`memory.swap.max=0` | 默认不额外施加统一 OS 硬限额，保留各自原生配置 |
| 实测指标 | 系统峰值 RSS，以及 cgroup 峰值/events | 系统峰值 RSS；可测分类另报 |
| 验证/测试 | 同一方法内配置固定 | 同一方法内配置固定，允许不同方法不同 |
| cgroup 缺失 | 阻塞 Ours 限额测量，不回退为 RSS 观察 | 不阻塞只测 RSS 的原生运行 |
| 图表 | 标注 Ours 2 GiB | 标注原生配置，并同时报告内存；不称同预算比较 |

所有方法仍须通过版本、输入、原生一致性、计时、CPU/NUMA 和存储条件核验。取消统一预算不代表旧数据、待验收端口或不匹配二进制自动有效。

## 已修改代码

- 新协议 ID：`disk_ours2g_native_baselines_20260917`，与旧统一 4 GiB 协议隔离，不追认旧运行。
- `memory_runner.py` 增加显式 `observe_only` 模式：不需要委派 cgroup，记录系统 `wait4` 峰值 RSS，明确 `memory_enforcement=none`、`memory_limit_bytes=null`、`budget_verified=false`。它不同于 resident 参考模式。
- `protocol.py` 允许 baseline 的观测证据，但明确拒绝 Ours 磁盘测量使用观测模式代替硬限额。原二进制/结果路径/证据哈希校验继续执行。
- `orchestrator.py` 主实验 Ours 默认 2 GiB；只在实际选择 Ours 限额工作项时预检 cgroup。增加可重复的 `--baseline-native-budget METHOD=GIB`，冻结每个 baseline 自己的原生参数；不同方法无需相等。
- 旧 native adapter 参数默认值 4 仅为兼容原有配置保留，**不是 baseline 外层 4 GiB 限额**。它可逐方法覆盖；artifact/CSV 用 `native_config_budget_gib` 与真正的 `memory_limit_bytes` 分开记录。AiSAQ/Starling 官方 CLI 保留 PQ、导航图和缓存等各自参数。
- 完整性校验与绘图允许方法间参数不同；同一方法混入不同配置仍拒绝。图注改成 `Ours cap 2 GiB; baselines native`，不再把所有方法标为共同 cgroup 预算。
- `run_official_disk_baseline.py` 默认不增加内存硬限额，只有显式指定 `--search-memory-gib` 才创建该 baseline 的限额组。正式 artifact 等准入未放宽，仍为诊断入口。

`--search-dram-budget-gib` 现在只表示 Ours 外层预算。`budget_scan` 是单列的 Ours 敏感性实验，不强迫其他方法随之改预算。

## 核验结果

| 检查 | 结果 | 日志 |
|---|---|---|
| 新方法内存政策 | 7/7 通过 | [method_memory_tests.log](method_memory_tests.log) |
| 原资源/统计/绘图协议 | 26 通过、1 跳过 | [protocol_tests.log](protocol_tests.log) |
| native contract | 10/10 通过 | [contract_tests.log](contract_tests.log) |
| 官方 CLI 参数/格式/准入 | 6/6 通过 | [official_cli_tests.log](official_cli_tests.log) |

合计 **49 项通过、1 项跳过**。真实 baseline 观察测试分配 24 MiB，系统 RSS 可以超过传给测试 artifact 的原生配置提示值，仍被正确接受；把同一份无硬限额证据标作 Ours 时会被拒绝。配置矩阵测试同时接受 Ours=2、DiskANN=6、Starling=12，并拒绝同一方法在测试时偷偷改参数。这里的数值是规则测试输入，不是正式 baseline 调参结果。

宿主重新尝试了 Ours 的 **2 GiB** cgroup 预检，仍返回 `Permission denied`。`numactl` 在 PATH 与 dpkg 中均不存在；当前会话 `sudo -n -l` 要求密码，没有可用的非交互管理权限。详见 [host_ours2g_check.json](host_ours2g_check.json)。没有修改系统服务、全局挂载或其他进程的限额。

因此，目前的 cgroup 阻塞仅针对 Ours 2 GiB（或另行显式限额的测量），不再是所有 baseline 的统一要求。五个既有二进制与登记哈希不匹配、部分原生端口准入和 NUMA 工具缺失仍独立存在；本轮没有自动重写哈希或提高端口状态。

## 文档与证据

- 当前本地规则：[完整实验协议](../../plans/DISK_EXPERIMENT_PROTOCOL_20260917.md)、[baseline 实验方案](../../plans/DISK_BASELINE_FAIR_COMPARISON_20260916.md)、[Ours 布局计划](../../../OURS_DISK_LAYOUT_OPTIMIZATION_SUGGESTIONS.md)。
- 飞书目标：[AGNews 分析](https://my.feishu.cn/docx/AhoKdQxEBogmWoxfk6xcxHKjnsg)、[实验方案](https://my.feishu.cn/docx/BpvBdhAILonRVkxT0Jicnwf4nfH)。只做局部修改，保留原表、图片和历史数据。本轮已同步并回读验证：AGNews revision **354 → 360**（6 处修改，三张图片保留），实验方案 revision **104 → 138**（34 处修改）。完整证据见 [verification.json](verification.json)；发布中出现的网络超时通过回读确认实际状态后恢复，没有重复追加正文。
- `before/` 保留本轮修改前工作区快照，不是干净 Git HEAD。发布脚本使用版本检查，每次更新后重新读取；网络失败与实际版本单独记录。
