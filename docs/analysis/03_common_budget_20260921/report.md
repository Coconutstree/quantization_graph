# 03 共同主预算：核查与选择

> 后续核查：4 GiB 的选择仅解决部分方法可运行性，不能作为内存受限实验结论。Ours 在 GIST/AGNews 的 2 GiB 下已有全记录总容量；详见 [记录缓存容量核查](../03_cache_capacity_20260921/report.md)。
2026-09-21。**当前默认 GIST、AGNews、DBpedia 主实验统一选择 4 GiB、32 workers。**
这个选择只统一资源条件，不授予所有方法正式准入；已经修改公共入口、预算检查及官方独立 adapter。
05 仍独立扫描 0.5/2/4/8 GiB，01/02 默认仍为 2 GiB。

## 为什么选择 4 GiB

| 候选 | 现有证据 | 决定 |
| --- | --- | --- |
| 2 GiB | Starling GIST/32 workers 新 validation RSS 为 2.050 GiB，当前配置不能通过 | 保留在 05；不作为新版 03 主预算 |
| 4 GiB | 同一 Starling 配置通过 RSS 验收；当前三数据集 Ours 完整 1-bit 规划均可行 | 选为所有方法共同主预算 |
| 8 GiB | 当前默认数据集没有证据表明，升到 8 GiB 可解决已知的额外方法准入问题 | 不因假设收益而继续增加预算；保留在 05 |

选择依据是内存可行性，不是观察哪档 QPS 对 Ours 更有利。
其他方法未完成的适配和验证继续单独处理；4 GiB 不承诺每个方法/配置都能通过。
若某配置仍超预算，应记录为不合格，不能只给该方法放宽。

## 新的真实 RSS 预检

复用官方 Starling GIST R48 索引与固定二进制，完整 100 万个 960 维向量。
仅使用已冻结的 **200 条 validation 查询**；未用 test 调参。固定 32 workers、32 个 node0 CPU，
width 为 40/100/580，beam=1，节点缓存为 0，保留原生导航索引和工作区。
搜索前完整顺序 O_DIRECT 读取实际分区索引；搜索进程不设 RLIMIT_AS/cgroup 限额。

- 整进程 RSS 峰值：**2,201,047,040 bytes = 2.049885 GiB**。
- 预算：4,294,967,296 bytes；零观测 swap；进程正常退出；`budget_admitted=true`。
- 返回 ID 全部合法、top-10 无重复；距离和原始 FP32 向量的 float64 重算一致，最大绝对误差约 5.59e-7。
- validation Recall@10：width 40/100/580 分别为 0.523/0.713/0.934。这不是相同 Recall 的性能对照；
  若目标为 0.95，仍需在 validation 扩展搜索参数范围，不能拿 0.934 的 QPS 当达标结果。
- 这是 **内存诊断**，`formal_ready=false`、`performance_sample=false`。官方 CLI 的预热、计时与逐查询指标
  尚未完成正式适配，本报告不引用其 QPS。

首个尝试要求 NUMA 内存绑定，但环境缺少 `numactl`，未启动搜索；失败记录保留。
随后明确按内存诊断运行，保持 CPU affinity，NUMA 内存策略使用系统默认。
正式性能运行仍要求原有 CPU/NUMA 和硬件 preflight，不能用本次诊断替代。

证据：[原始预检](../../../results/diagnostics/03_common_budget_20260921/starling_gist_validation_rss4g_retry/preflight.json)、
[资源测量](../../../results/diagnostics/03_common_budget_20260921/starling_gist_validation_rss4g_retry/resources.json)、
[运行脚本](../../../scripts/preflight_03_starling_budget.py)。
本次只运行了 4 GiB 预检；2 GiB 不适配的判断来自实测 RSS 和历史证据，不伪造第二次测量。

## 其他方法的证据边界

下表为旧二进制、2 GiB **地址空间限额**运行中观测到的整进程 RSS（GiB），只辅助检查数量级。
不能把这些值视为新实现已经通过 4 GiB 验收，也不能把旧 QPS 移入新主表。

| 方法 | GIST | AGNews | DBpedia |
| --- | ---: | ---: | ---: |
| Ours | 0.570 | 0.527 | 0.696 |
| DiskANN | 0.955 | 0.866 | 未完成 |
| SymphonyQG | 0.156 | 0.154 | 0.221 |
| Glass | 0.171 | 0.168 | 未完成 |
| OG-LVQ | 0.175 | 0.175 | 未完成，且官方一致性被阻断 |
| AiSAQ | 缺全量证据 | 缺全量证据 | 缺全量证据 |

当前 Ours 原生规划器在 32 workers 下，GIST/AGNews/DBpedia 的完整 1-bit 固定规划量分别约
611.767/572.668/895.770 MiB，均低于 4 GiB；余量仍按照既有 hot+dynamic 策略分配。
规划值不是 RSS，缓存配置和实测峰值仍须在新运行中核验。

Starling 在 AGNews/DBpedia 的已有 FP32 节点格式分别需要至少 4356/6404 bytes（R64），
超过 4096-byte 页。**4 GiB 或 8 GiB RAM 都不会解决这个格式限制。**
GIST 原 R64 也超页，因此此次沿用已验证的原生 R48 索引，不改变图或重新构建。
AiSAQ/Starling 的完整正式适配、OG-LVQ 官方距离核一致性仍按原状态阻断，不能靠提高预算放行。

## 范围与执行规则

本次主预算依据覆盖当前默认三个数据集，不代表所有可用数据集都适合 4 GiB。
例如当前 MS MARCO 有 113,520,750 个 1024 维向量：按现有 Ours 规划器，仅最低 64-bit codes、
factors 和 32 workers 预留合计已约 6.718 GiB，尚不含其他规划量，4 GiB 无法通过该规划。
若将其纳入主数据集，应在跑主实验前重新确定对所有方法一致的新预算；不能临时给 Ours 单独加内存。

- `experiments/03_disk_system/run.py` 的 primary/thread_scaling 默认且要求共同 4 GiB。
- 统一入口选择 `--layers 03`、`05c` 或包含 03 的多层集合时同样使用 4 GiB。
- `--baseline-native-budget METHOD=GIB` 仍必须等于共同预算，不能形成方法例外。
- 独立 AiSAQ/Starling adapter 默认也为 4 GiB RSS 验收；其显式其他预算仅用于独立诊断/预算实验。
- 01/02 单独入口默认 2 GiB；05 默认扫描点 2 GiB，原网格不变。
- 使用新的 run-id、validation 选参和 test；不覆盖旧结果，不把原 2 GiB 结果改标签。

全部原始路径、哈希、数据规模、旧 RSS 和各候选下 Ours 规划见 [audit.json](audit.json)。
本轮预算/入口相关回归：预算策略 10、官方 adapter 8、资源协议 26（1 条件跳过）、目录隔离 10 项通过。
尚未执行新 4 GiB 主实验的完整多方法 Recall–QPS 曲线。
