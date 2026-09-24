# GIST 自动策略：默认256页共享在途上限

2026-09-21：旧命令入口已升级。执行 `gist_fixed_factors_budget/run.py` 或
`gist_width40_test100/run.py` 时，默认转到已编译验证的 `gist_auto_inflight256`。
不再从这些命令入口默认启动冻结64页版本。

- 放不下完整1-bit codes：共享在途上限为 `min(256, routing_capacity_pages)`。
- codes可常驻：保持原常驻／静态热点＋动态记录缓存策略。
- worker候选窗口及scratch仍按64计账；验收检查实际峰值不超过计划在途上限和页槽容量。
- `--routing-max-inflight-pages 64` 是原生参数，可用于明确的64页对照。

## 使用已有入口

```bash
# 两个旧入口均执行新版预检，不启动性能实验：
python experiments/04_ours_memory_budget/gist_fixed_factors_budget/run.py --preflight-only
python experiments/04_ours_memory_budget/gist_width40_test100/run.py --prepare-only
# 不带参数也只执行预检。显式 --run 才测性能：
python experiments/04_ours_memory_budget/gist_width40_test100/run.py --run
```

默认性能配置沿用用户已确定范围：单轮、40 widths × test100；
538/616 MiB分页、resident参考、768/1024/1536 MiB记录缓存。无第二轮，无自动800查询。
`--budgets`和`--widths`可显式指定；首次测量锁定配置，恢复时不同配置会拒绝混用结果。
本入口只运行Ours，不增加DiskANN扫描。

新二进制：`work/ours_memory_budget/gist_auto_inflight256/ours_gist_auto_inflight256`。
新版结果：`results/04_ours_memory_budget/gist_auto_inflight256/`。

## 统一2 GiB地址空间上限入口

之前准备但未运行的 `gist_common_as2g_test100/run.py` 也已切换新版Ours：

```bash
python experiments/04_ours_memory_budget/gist_common_as2g_test100/run.py --preflight
# 后续明确恢复性能任务时才执行：
python experiments/04_ours_memory_budget/gist_common_as2g_test100/run.py --run
```

该入口包括6条Ours和1条DiskANN；双方RLIMIT_AS soft=hard=2 GiB。
结果另存 `results/04_ours_memory_budget/gist_common_as2g_test100_inflight256/`。
Ours规划额度不等于硬上限，2 GiB地址空间上限不等于RSS上限。

## 历史与校准

历史二进制、校准和报告保持原状。旧模块内部实现仍可供历史审计代码导入，
但命令行默认使用新版。`prepare.py` 与 `audit.py` 仍属于历史构建/审计工具，
不会把历史64页曲线改标为256页。
新版继承原fixed/reserve账本，运行时仍要求实际VmPeak通过原验收门槛。

依据：[联合优化报告](../../../results/04_ours_memory_budget/gist_joint_optimization/report.md)。
该证据针对GIST、L100、32 workers，不代表新版全40-width性能曲线已经完成。
本次仅更新入口并预检，性能实验继续暂停。
