# 磁盘实验框架与文档修复（2026-09-17）

用户确认范围：修复代码和文档，先不跑全量实验；随后明确每个配置只运行一次，不做五次重复取中位数。本次没有重跑 AGNews/GIST/DBpedia 全量曲线，没有把旧实验或待验收 baseline 升级为正式结果。

后续实际核验见 [AGNews 核验记录](../agnews_verification_20260917/README.md)：进一步确认宿主当前账号没有 cgroup 委派、缺少 numactl，发现二进制哈希不匹配，并补修 Ours/共享图 PQ 与 DiskANN Rust 入口的批次 Recall 计时。下文的测试数与飞书版本保留为上一轮记录；最新写入与验收状态见后续记录。

已更新飞书：

- [AGNews 磁盘环境 32 线程实验分析](https://my.feishu.cn/docx/AhoKdQxEBogmWoxfk6xcxHKjnsg)：revision 316 → 350，34 处局部修改（含最后按用户要求改为单次运行），逐次回读核对，原三张图片保持不变。
- [32 线程磁盘实验方案](https://my.feishu.cn/docx/BpvBdhAILonRVkxT0Jicnwf4nfH)：revision 78 → 104，26 处修改（含单次运行协议）；写清代码实施状态、发布目录与尚未完成的正式验收。
- 本地完整协议：[DISK_EXPERIMENT_PROTOCOL_20260917.md](../../plans/DISK_EXPERIMENT_PROTOCOL_20260917.md)。

## 实际修改

| 范围 | 修复内容 |
|---|---|
| `memory_runner.py`（新增） | 原生进程在 exec/加载索引前进入独立 cgroup v2；`memory.max` 与禁用 swap 读回校验；记录 `memory.peak/stat/events`、退出码、OOM、真实 `wait4` 峰值 RSS、采样 VmPeak；CPU affinity 读回验证，可通过 numactl 指定 NUMA 内存节点。缺少委派或接口时拒绝执行，不回退 RLIMIT_AS。 |
| `protocol.py`（新增） | 核对资源 sidecar 哈希、原生二进制与 result-json 路径；保留 `.native.json` 和 native 自报 RSS，另写真实峰值及其生命周期范围。按用户最终要求只跑一次（repeat_id=0），保留实测值；不计算跨运行中位数/IQR/CV，不生成 median 文件；重复配置/来源失配/非有限值拒绝。 |
| `native_contract.py` | 正式重复数按用户最终要求设为 1；接入外部资源证据；完整性校验按显式方法、存储模式、预算、缓存与线程条件执行，取消隐式 GIST 条件。新协议不强制缓存节点 10% 上限；原生算法、计时与完整内存归因等准入条件仍需满足。 |
| `orchestrator.py` | 默认 4 GiB / 32 workers；预算扫描与线程扫描显式分组；验证、调参与测试条件一致；05A 使用独立测试集；CPU/NUMA 与运行配置冻结；失败输出不可覆盖；按新协议/运行 ID 隔离发布；仅选择其他方法时不再用 Ours DB1 内存下界阻塞。 |
| `plot_05_disk_suite.py` | 方法筛选之前核验源 artifact、资源记录、CSV 哈希与单次配置；拒绝旧协议/FAST 绕过；从单次实测值绘制前沿，预算与缓存明确标注；公开 CSV 单独不能代替证据。 |
| `run_official_disk_baseline.py` | AiSAQ/Starling 的官方 CLI 搜索步骤接入 cgroup，建索引预算分离；继续记录 `formal_ready=false`，不以整个进程 wall time 冒充搜索 QPS。 |
| Glass / SymphonyQG / OG-LVQ C++ 端口 | Recall 计算和逐查询 RSS 采样移出吞吐计时；Glass/Symphony 单查询 latency 不再包含 Recall 计算；不独立测量的 distance/queue 时间为 null，遍历 wall time 单列；Recall 计算不再标为 rerank。未改建图、搜索队列或距离核来迎合结果。 |

同一 native 进程扫描多个 width 时，共享整次生命周期内存峰值，不解释成逐点内存。RSS、cgroup peak、分类字节不是可相加的三个指标。

## 文档结论修正

AGNews 原文中“还有约 1 GiB 可用缓存”“同 Recall 下 I/O 更低”“32 线程已打满设备”“顺序化达到 200–350 MB/s”等结论缺乏相应证据，已撤回或改为待测假设。“Ours 的 Recall 最高”也已纠正：旧表 SymphonyQG 的观测值 0.9992 高于 Ours 的 0.9944，但这些都不是方法的理论上限。

旧峰值 QPS 对应不同 Recall，跨 width 的均值不是同 Recall 对比。2 GiB 参数不代表硬限额已执行；旧端口的 `peak_rss_bytes` 中有对象字节求和，不能据此推算缓存余量。保留旧表与图用于追溯，新协议结果单独发布。

## 验证

| 检查 | 结果 | 记录 |
|---|---|---|
| 新协议/资源/统计/绘图准入回归 | 27 项中 26 通过、1 跳过 | [protocol_single_tests.log](protocol_single_tests.log) |
| 原 native contract 检查 | 10/10 通过 | [contract_single_tests.log](contract_single_tests.log) |
| 官方 CLI 命令/转换/registry 检查 | 6/6 通过 | [official_cli_tests.log](official_cli_tests.log) |
| 原实验框架单元检查 | 20/20 通过 | [existing_suite_tests.log](existing_suite_tests.log) |
| Glass/Symphony 极小原生查询 | 2/2 通过，256 vectors、4 queries；计时字段与 Recall 汇总核对，不属于正式性能测量 | [native_timing_tests.log](native_timing_tests.log) |
| 三个 C++ 端口 | 编译通过 | [native_build.log](native_build.log) |
| Python 语法与差异空白检查 | 通过 | `py_compile`、`git diff --check` |
| 真实 cgroup 预检 | 正确返回 blocked_environment；当前挂载只读 | [cgroup_check.json](cgroup_check.json) |

合计 **64 项检查通过、1 项跳过**。跳过的是实际 cgroup OOM 集成检查；cgroup 成功限额路径不能由 mock/静态检查冒充已在当前机器验证。已测真实子进程 RSS、CPU affinity、超时与非 OOM 错误分类。没有运行全量实验。

## 正式运行前仍须完成

1. 提供已启用 memory controller、可写且支持 `memory.peak` 的委派 cgroup；在该环境执行 `QG05_TEST_CGROUP_PARENT=<路径> python experiments/05_disk_system_fair/tests/test_measured_protocol.py`，以及实际 CPU/NUMA 绑定与小规模搜索预检。此环境内核接口不足时需要在合适宿主环境执行，不能用 AS 上限替代。
2. 按原生实现准入分别完成 AiSAQ/Starling 正式 artifact、计时、参数锁定与资源分类接入。Glass/OG-LVQ 的 blocked 状态和其他端口的 cache/parity/计时门槛未自动放宽。Starling 在 AGNews/DBpedia 上的页容量限制仍单独记录。
3. 本轮重新编译了三个 C++ 端口；没有自动改 registry 的二进制哈希或 ready 状态。重新核验与批准准入后才能固定新哈希，避免直接复用旧结果。
4. 新增真实测量时使用新 run-id，遵守验证/测试分离；当前代码修复不是任何新性能排名的证据。

源码哈希、飞书版本与资源保留验证见 [verification.json](verification.json)。`code_before/` 只备份本轮涉及文件修改前的工作区内容，不代表干净 Git HEAD；已有用户修改没有回滚。Feishu 发布脚本及 before/after 快照保留在本目录。
