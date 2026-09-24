# GIST 低内存：PCA 1-bit routing → 原维度 4-bit verification

这是隔离的搜索实验。目标是验证低维 1-bit 能否改善图导航的 Recall / I/O / QPS；不把低维分数当作完整距离的数学下界，也不自动替换现有搜索入口。

## 搜索步骤

1. 用 base 中固定的 32768 条样本拟合 PCA，分别生成 128 / 256 / 512 维的投影和对应 native RaBitQ codes / factors。PCA 拟合不使用 validation / test。
2. 按 538 MiB 预算核算 codes、factors、PCA 矩阵、量化器旋转、worker 缓冲、页缓存及 record cache；由账本决定低维 codes 常驻或分页。原始 960 维作为基线。
3. 查询时同时准备原始 960 维和投影后的查询；两套量化器使用独立的线程局部查询状态。
4. 每次扩展图节点，对尚未验证的邻居读取低维 1-bit codes，计算近似距离，按距离及 ID 排序。
5. 每次保留前 M 个邻居读取原始 960 维 4-bit 记录并验证；候选队列使用原维度验证距离。暂未入选的邻居可在后续扩展中再次考虑。M=0 表示全部验证的对照。
6. 保留原来的 residual 重排。低维分数不与 tau 比较，两个低维 hard-prune 入口均禁用；原维度 4-bit 验证重新计算，不复用低维 short_ip。
7. 只在 validation 上选择 M 和 width，锁定后运行独立 test800 两轮，报告 route / 非 route / 总读页、缓存命中、Recall 和 QPS。

前 M 筛选仍是近似搜索，会影响 Recall；“不用下界剪枝”不等于“导航无损”。原始 960 基线保留已有统计 gate 和 BFS route 分页；因此必须同时检查新增的 4-bit 读取是否抵消 route I/O 节省。

## 固定比较规则

- 主比较：validation Recall ≥ 0.95，选择每个维度达标且最快的配置。
- 补充比较：在首次 test 开始前声明，以基线 width=100 的 validation Recall 0.961 为门槛，仍只从同一 validation 网格选参。
- 两组 test 分开汇总，配置不同的结果不混合平均。test 未达标和降维反而变慢的结果均保留；不根据 test 重新选参。
- 128 维常驻带来的效果同时包含内存重新分配的收益；本轮不能将全部收益归因于 PCA 的排序质量。

## 文件和复现

- `prepare.py`：从冻结的 PCA 实验 native 副本构建隔离的 ranking-only 二进制，并记录源码及二进制哈希。
- `run.py`：正确性对照、validation 网格、主比较锁定和 test。
- `confirm_matched.py`：读取预声明的 `matched_protocol.json`，执行补充比较。
- `report.py` / `audit.py`：生成分组结果，并审计预算、数据划分、选参时间和文件哈希。
- `benchmark.py`：当前机器的运行包装；只针对已确认的 MSMARCO 建图 PID 暂停并在 finally 中恢复。迁移机器时须重新识别竞争进程。

依赖的 PCA 编码资产位于 `work/ours_memory_budget/gist_pca_budget`；原始图及 4-bit / residual 数据不重建。`build.json`、`protocol.json` 和各次 `acceptance.json` 记录实际使用的版本，不能用重新构建后的二进制覆盖已验收结果。

结果见 [报告](../../../results/04_ours_memory_budget/gist_pca_routing/report.md)。此前下界诊断独立保存在 `gist_pca_budget`，不作为本轮导航实验的前置要求。
