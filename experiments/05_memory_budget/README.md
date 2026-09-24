# 05：独立内存预算实验

2026-09-23 更新：默认与 03 同步比较 **Ours、SymphonyQG-DiskPort、DiskANN、Starling**。预算沿用通用入口的 0.5/1/2/4/8 GiB 网格；历史 Ours-only 运行和结果保持原样。每个预算用新 run-id，缺少准入或超预算的点不得绘图。
03 使用统一 4 GiB 的完整系统主比较；04 保留 Ours 优化和消融；正式预算扫描单独归入本实验。

## 入口与协议

```bash
python experiments/05_memory_budget/run.py --phase doctor --datasets gist --search-dram-budget-gib 1
# 等价的统一入口：
python scripts/run_disk_experiments.py --layers 05 --phase doctor --datasets gist --search-dram-budget-gib 1
```

05 自动使用 `experiment-group=budget_scan`，固定 32 workers，每个预算使用独立 run-id，依次执行
`export → validate → tune → run → plot`。同一预算的各阶段使用相同参数；validation 选参后锁定，test 不重新选参。
03 不再接受 `--experiment-group budget_scan`；05 不与 01/02/03 放在同一个 run 中。

例如一个预算点的完整命令（替换 SSD 路径、有效 CPU 集合及 NUMA 节点，并先确认 doctor 和方法准入）：

```bash
CPU_LIST=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
for phase in export validate tune run plot; do
  python experiments/05_memory_budget/run.py \
    --phase "$phase" --datasets gist --methods Ours-Disk,SymphonyQG-DiskPort,DiskANN-PQ-Disk,Starling-Disk \
    --search-dram-budget-gib 1 --run-id gist_ram_b1_20260921 \
    --disk-root /path/to/ssd/qgraph --disk-profile nvme \
    --cpu-affinity "$CPU_LIST" --numa-node 0 --workers 32 --repeats 1 || break
done
```

`--cpu-affinity` 使用逗号分隔的 CPU ID；按机器实际可用的 32 个 CPU 修改上例。
其余预算分别传 `2`、`4`、`8` 并使用新的 run-id。方法是否可测沿用端口 registry 和准入状态，新增编号不授予 ready。

资源口径沿用 `disk_ram_budget_rss_20260921`：所有方法使用相同计划 RAM 预算，按整个搜索进程实测 RSS 峰值验收；
不新增 OS 内存限制。RSS 超预算或观测到 swap 的点不进入合格结果。同步报告 I/O、Recall 和 QPS，不能用累计 PCA 方差代替 Recall。

## 输出与实现

```text
results/05_memory_budget/<dataset>/<run-id>/
  manifest.json
  raw/<method>/<phase>/
  manifests/                    # 每个预算自己的选参锁
  tables/
  figures/
results/manifests/<run-id>/      # 公共协议；experiment=05_memory_budget
```

JSON、CSV 和结果清单保留 `experiment=05_memory_budget`。内部 `layer=05c` 仅表示复用完整磁盘系统 native 端口，
不决定论文实验编号。共用原生搜索代码和已导出的索引，不复制一份搜索算法；03/05 的结果和参数锁分开保存。
既有 03 预算记录和 04 消融不移动、不改标签、不自动成为 05 正式结果。

```bash
python scripts/plot_disk_experiments.py --layers 05 --datasets gist \
  --results-root results --run-id gist_ram_b1_20260921
```

当前绘图入口读取指定预算 run 内的 Recall–QPS 等曲线；不同预算的数据保留各自的来源和准入记录。

## Ours 策略与当前完成边界

目标策略：完整 1-bit 能常驻时，余量分配给 hot + dynamic 记录缓存；不能常驻时选择最高可常驻 PCA 前缀，
采用 PCA + 1-bit、无 residual norm、M=32，再用完整维度 4-bit 验证。

PCA 自动分支和 **hot + dynamic 已接入共享实现**，实现标识为 `05c-ours-pca1bit-hot-dynamic-v4`。
保持已有 BFS 图页缓存和查询内缓存后，剩余额度优先放 validation 冻结的热点记录，再给 16 分片 FIFO。
完整 1-bit 与 PCA 分支都适用；余量不足时记录容量可以为 0。test 不重新训练热点。
策略与计账见 [03 共用实现说明](../03_disk_system/README.md#正式-ours-的-hotdynamic2026-09-21)。
原有正式准入、RSS 和独立 parity 验证要求继续保留；预算扫描性能数据尚未重跑。

历史运行范围（不再是当前默认）：Ours-only，1/2/4/8 GiB。0.5 GiB 旧失败记录仅作诊断。为保留已启动的 2 GiB 进度，实际执行顺序为 2→4→8→1；统一状态见 `results/diagnostics/gist_05_ours_1_2_4_8_20260921/status.json`。
