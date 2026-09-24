# 自适应常驻 PCA routing

目标是**在整进程预算内保留最高 route 维度**。先扫描可用整数维度，再把剩余空间交给 record cache；这不是“自动选择最高 QPS 的维度”。Recall 和搜索 width 在 validation 上单独验证。

当前入口针对 GIST 1M × 960、32 workers、已有 R64 图，PCA shortlist 统一 M=32/64。使用已冻结的 base-only PCA 与原始图、完整 4-bit、residual 重排数据。新的二进制和结果放在独立目录，既有实验不修改。

```bash
# 只打印预算方案，不加载 codes。
python3 experiments/04_ours_memory_budget/gist_adaptive_resident/policy.py --budget-mib 538

# 独立编译；仅当选定维度尚无资产时，生成该维度的 sidecar。
python3 experiments/04_ours_memory_budget/gist_adaptive_resident/prepare.py --budget-mib 538

# 先 validation M=32/64、width=100/180/260，再锁定达标点运行 test800 两轮。
python3 experiments/04_ours_memory_budget/gist_adaptive_resident/benchmark.py --budget-mib 538

# 非标准维度的真实加载、内存和搜索检查；只使用 train100。
python3 experiments/04_ours_memory_budget/gist_adaptive_resident/benchmark.py --budget-mib 542 --smoke

python3 -m unittest discover -s experiments/04_ours_memory_budget/gist_adaptive_resident -v
python3 experiments/04_ours_memory_budget/gist_adaptive_resident/report.py
python3 experiments/04_ours_memory_budget/gist_adaptive_resident/audit.py
```

## 内存规则

令原始维度为 D、保留 PCA 维度为 d，实际 bit 数为 `DD = next_power_of_two(ceil(d/64)*64)`。本机 native codec 的最小块是 64 bit；默认最低保留维度为 64。

每个 PCA 候选的准入开销是：

```
fixed + reserve
+ N * DD / 8                # 1-bit codes
+ N * 20                    # native factors
+ 4 * D * d + 4 * D         # PCA basis + mean
+ 16 * DD * DD              # conservative rotation reservation
+ workers * 16 * D          # query scratch reservation
+ mandatory certificate bytes (currently 0)
+ explicit minimum record-cache bytes (default 0)
```

当前固定项为 426 MiB，安全 reserve 为 64 MiB。它们是继承的保守准入额度，不是额外申请的两块内存。实际 `VmPeak`、`VmRSS` 与 RLIMIT_AS 另行记录和验收；账本不是分配器逐字节内存追踪。

原始 960 维能常驻时直接使用原方法，不建 PCA、不改变原方法搜索路径。它没有 PCA 额外开销，因此各维度成本不全局单调；选择器遍历全部候选而不是盲目二分。若最低维度也放不下，则明确拒绝，不悄悄退回分页。

预算改变后需要选择相应的**预编码 sidecar**。本方法不是查询中截断已有 960-bit 文件；不同维度对应独立的低维量化器。离线生成成本不计入 QPS。尚无资产的维度会生成一次；编码后的 sidecar 会记录 hash。

`policy.py --cache-floor-mib` 可预留强制 cache 底线。默认维度优先，因此有些预算会剩不下 record cache。这种选择不保证最高吞吐；要评估该权衡，需要独立的等 Recall 对照，不能把维度目标悄悄改成性能目标。

## hard prune 的边界

线上仍为 `低维 1-bit 排序 → top-M → 原始完整 4-bit 验证 → 原 residual 重排`，没有低维 hard prune。完整推导与剩余验证要求见 [安全剪枝分析](../../../results/04_ours_memory_budget/gist_adaptive_resident/hard_prune.md)。

`hard_prune_reference.py` 是精确有理数离线参考，证明的是同一 4-bit 打分系统的重建误差证书。它没有 GIST 实际证书、native SIMD 浮点误差保证或线上性能数据。调用者不提供数值误差证书时始终不剪枝。
