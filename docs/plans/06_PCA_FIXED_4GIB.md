# 06：固定 4 GiB 的 PCA 对照

2026-09-23 用户要求：当前 05 完成后，再比较相同 4 GiB 下使用与不使用 PCA。先做 GIST，复用原图和完整维度 4-bit / residual 数据。

## 旧实验核验

`results/04_ours_memory_budget/gist_pca_routing/figures/recall_qps_by_width.svg`
来自历史 validation100、width=60/100/180、M=16/32 的结果。预算为
538 MiB，32 workers、beam=1，使用 RLIMIT_AS/VmPeak 口径。
历史完整实验另有 test800 两轮确认点，但不是 4 GiB 下的完整 test 曲线。
保留该历史实验，不改编号，也不作为新 06 结果。

## 新实验约定

- 固定 4 GiB 的整进程 Peak RSS 预算；32 workers、beam=4。
- 对照：原始 960 维 1-bit；显式启用 PCA 128/256/512 维 1-bit。
- 使用同一个图、完整维度 4-bit 验证与 residual 数据、查询划分、CPU/NUMA 和 I/O 协议。
- 沿用当前各分支搜索语义，PCA 分支 M=32；这是完整 PCA 搜索分支的消融，不能把全部差异归因于线性投影本身。
- 所有配置使用同一缓存分配政策，剩余预算按该政策分配；报告实际图页、hot、dynamic 容量及命中数，明确 PCA 释放内存带来的影响。
- PCA 仅在 base 上拟合；热点来自 validation。参数和资产冻结后，test800 只扫描 60/100/180 三个宽度（2026-09-23 用户确认），不运行九宽度扫描。
- 正确性参考独立进程；性能测量重新启动干净进程，PCA 查询投影计入查询时间。
- 报告 Recall–QPS、尾延迟、读取请求/字节、候选验证数、Peak RSS；同宽度用于诊断，主结论看同召回。
- 每个配置新 run-id；原始结果、来源哈希和失败证据完整保留。

## 执行状态

等待 `results/diagnostics/gist_cache_selection_20260923/status.json`
的 validation 选择与冻结 test 核查完成。06 尚未启动；四种表示统一采用该队列 4 GiB 的 cache_selection.lock.json 选中策略，不按维度另行选择缓存策略。
当前正式路由在 4 GiB 会自动选择完整 1-bit；实施前需新增独立消融入口，
允许显式 PCA 维度并绑定参数锁和准入证据，不能用降低声明预算冒充 4 GiB PCA。
使用独立编译产物和配置，避免更改正在运行的 05 二进制或资产。


### 本轮执行入口

已实现独立参数 `--ours-fixed-route-dimension`，可选 full 原维度或 PCA128/256/512；固定维度仍通过真实预算账本检查，不降低声明预算。自动路由默认保持不变。

` scripts/continue_06_after_selection.py ` 等待缓存选择队列成功结束，随后在独立产物目录编译并运行 PCA 规划测试，启动 `scripts/run_06_resident_dimensions.py`。06 状态：`work/06_resident_20260923/status.json`（启动前/构建），以及 `results/diagnostics/gist_06_resident_20260923/status.json`（测量阶段）。维度依次 960、512、256、128，串行执行；构图复用，PCA 基复用，每种表示的 validation 热点单独冻结，参考与测量分进程。
