# 低维内存码：读盘前预筛选加速

后续范数/尺度修正试验已完成，未发现进一步收益，默认不启用。
详见 [范数修正对照结果](NORM_CORRECTION_RESULTS.md)。本报告原有阈值版本继续保留。

## 第二轮：低维阈值减少后期无效读取

新增 `--adaptive-route-ratio`，默认 0 关闭。候选池填满后，计算池内
所有成员的最大低维重建 squared-L2，以其乘以 ratio 作为新邻居的
读盘准入阈值，然后再应用 keep 上限。比较双方都在低维空间，且都
包括 query 平方范数；绝不拿低维分数直接比较原空间候选池尾距离。
原空间 full4 精算和 residual rerank 不变，查询未二值化。

原因：完整 DB1 不驻内存后，原剪枝门失效；上一轮 keep16 虽限制
每轮读取量，但后期仍会读取最多 16 个不够好的邻居。
前 100 条旧 keep16 约 805 个 full4 候选、853 次 I/O/query，差额主要
是图页读取。新阈值 ratio=1 后约 307 个 full4 候选、356 次 I/O/query。
I/O 是程序记录的请求数，不等于底层 SSD 控制器命令数；4 KiB 页读取
仍会有对齐开销。本次未靠改 I/O 统计口径或增加缓存实现提速。

这依然是**经验性近似筛选，不是无损下界**。ratio=0.9 在前 100 条
使 Recall 降至 0.940，淘汰。ratio=1.0 的 keep16/keep32 继续验证。
配置名 `d256_k32_t100` 表示 dim=256、keep=32、ratio=100/100。

本轮前 100 条扫描结果：
`results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260910_000730/summary.csv`。
后 900 条结果目录：
`results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260910_000818/`。
后 900 条已用于上一轮实验，本轮仅保证没有用它们选择本轮阈值，
不能称其为完全未使用的最终测试集。

### 第二轮 900 条结果（全部完成）

| 方法 | Recall@10 | QPS | I/O/query | 峰值 RSS MiB |
|---|---:|---:|---:|---:|
| 原有 Ours | 0.9896 | 50.77 | 590.5 | 319.4 |
| 上版 d256/keep16，无阈值 | 0.9830 | 18.73 | 850.3 | 259.7 |
| d256/keep16/ratio1.0 | 0.9830 | 70.05 | 345.0 | 206.0 |
| d256/keep32/ratio1.0 | 0.9891 | 50.09 | 515.2 | 248.1 |

本轮推荐保留 **keep32/ratio1.0 作为保守候选**：比原有 Ours 的
Recall 低约 0.044 个百分点，吞吐基本持平，实测峰值内存减少约 22%。
keep16/ratio1.0 是速度候选：比原有 Ours 快约 38%，Recall 低约
0.66 个百分点。它相对同码长 keep16 无阈值版本，I/O 减少约 59%，
本轮 Recall 一致；不能由此推广为对所有数据/查询无损。

注意：无阈值 keep16 本轮只有 18.73 QPS，上一轮为 32.47 QPS，且本轮
I/O 等待 P95/尾延迟偏高，说明存在显著运行波动。上面的 QPS 是单次
观测，不把 70.05/18.73 的倍数作为稳定加速比，也不证明预算缩放规律。
本轮没有做用户不需要的五次重复。所有自适应配置码常驻量为 27.4 MiB，
完整 DB1 常驻量为 0；缓存仍相同，0.5 GiB 仍仅为记账约束。

release 编译、现有两项数值测试、Python 编译检查与 diff 空白检查通过。
比对已执行查询的 Recall 是集成验证，不替代跨预算/跨数据集测试。
默认 ratio=0，未全局启用经验筛选；通过显式参数启用。

复现第二轮验证：

```bash
python legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py --queries 900 --query-offset 100 --budget-gib 0.5 --variants baseline,d256_k16,d256_k16_t100,d256_k32_t100
```

## 实现与边界

真实 05C O_DIRECT/native AIO 路径新增 `--adaptive-route-keep K`：
每次展开图节点，用非对称低维距离排序新邻居，仅读取前 K 个候选的
full4 记录参与原空间精算，再沿用原来的候选池和 residual rerank。
Query 只投影一次，未二值化；低维距离没有混入原空间距离池。
自适应路径不加载完整 DB1 sidecar。

这是近似筛选，不是有证明的距离下界。被丢弃的邻居仍记作 visited，
不会从后续节点重新尝试，因而存在 Recall 损失。`K=0`（默认）关闭
筛选，恢复上一版全量 full4 验证。未改图、磁盘布局、缓存策略、beam
或异步 I/O；原有 Ours 不启用这个开关。

## 测试条件

AGNews 769382×1024，Ours 自己的 R64/Lbuild400 图和既有磁盘索引。
width=49、beam=1、workers=32、Recall@10、0.5 GiB 记账预算、standard
BFS cache、无 warmup、每配置一次，串行运行。预算不是 cgroup 硬约束。
前 100 个查询用于配置筛选；后 900 个查询用于独立于本轮调参的验证。
此前这些查询可能用于项目其他实验，因此不称作从未接触的最终测试集。
目前都是 `formal_ready=false` 的 integration pilot，不是正式论文曲线。

前 100 条全配置结果：
`results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260910_000140/summary.csv`。
每种配置都有 `.json` 汇总、`.queries.jsonl`、`.argv.json` 和 `.log`。

筛选结论：d64/k8 虽达到 40.98 QPS，但 Recall 仅 0.809，不能作为成功方案。
d256/k16 达到 Recall 0.982、29.51 QPS，相比不筛选 d64 的
Recall 0.992、18.49 QPS 有速度/召回取舍；仍慢于原有 Ours 的 42.96 QPS。
选取 d256/k16 和更保守的 d128/k32 继续验证，不能凭这 100 条宣布胜出。

## 后 900 条验证结果（已全部完成）

| 方法 | Recall@10 | QPS | I/O/query | Codec MiB | 峰值 RSS MiB |
|---|---:|---:|---:|---:|---:|
| 原有 Ours | 0.9896 | 49.96 | 590.5 | 108.6 | 318.8 |
| d64，不筛选 | 0.9896 | 19.33 | 1838.9 | 9.1 | 474.4 |
| d256，keep=16 | 0.9830 | 32.47 | 850.3 | 27.4 | 260.2 |
| d128，keep=32 | 0.9873 | 23.36 | 1402.3 | 15.2 | 355.0 |

原始文件：
`results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260910_000318/`。
`summary.csv` 是全部配置汇总；`pilot_config.json` 记录查询切片、预算和配置。
全部子进程正常退出；release 编译、两项数值测试、Python 编译检查和
`git diff --check` 通过。数值单测验证距离公式/FP16，并不证明筛选保召回。

结论：d256/keep16 相比先前 d64 全读版，QPS 提升约 68%，I/O 减少
约 54%，峰值 RSS 减少约 45%；Recall 下降约 0.66 个百分点。
这里同时改变了码长与筛选，不能将全部增益归因于某一个因素。
相对原有 Ours，它的峰值 RSS 低约 18%，但 QPS 仍低约 35%。
因此加速已实现，尚未达到“低内存且不损速度/召回”的目标。
d128/keep32 的 Recall 更高，但 RSS 仍高于原有 Ours，并非更好的
整体内存方案。保留这两个显式候选配置，不自动把 keep16 设成默认。

后续正式比较仍需多预算、Recall–QPS 曲线和统一临时内存约束；
单次短程 QPS 受机器状态影响。这里没有新增自动预算选维器，也没有
证明跨数据集通用性，不能将这次筛选优化称为完整自适应方案完成。

## 复现

```bash
cargo build --manifest-path src/graph_core/Cargo.toml --release --bin qgraph05_shared_graph_port
cargo test --manifest-path src/graph_core/Cargo.toml --bin run_diskann_fair adaptive_numerics_tests
python legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py --queries 100 --budget-gib 0.5 --variants baseline,d64,d64_k8,d64_k16,d64_k32,d128_k8,d128_k16,d128_k32,d256_k8,d256_k16,d256_k32,d512_k16
python legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py --queries 900 --query-offset 100 --budget-gib 0.5 --variants baseline,d64,d256_k16,d128_k32
# 将下面参数替换为运行时输出的目录：
python legacy/06_adaptive_disk_ann/summarize_disk_pilot.py RESULTS_DIRECTORY
```

原始索引不重建、不覆盖；结果写入独立时间戳目录。无需启用筛选即可
恢复旧控制路径。不要将 artifact 继承的 `ablation=db1+coalescing+reuse`
误读为自适应版本仍有 DB1 gate；实际以 `adaptive_route_dim`、
`adaptive_route_keep`、`db1_checks=0` 和 resident 统计为准。
