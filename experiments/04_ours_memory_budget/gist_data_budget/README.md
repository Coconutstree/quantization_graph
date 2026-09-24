# GIST 数据预算实验（修正预算口径）

控制变量为 `--data-cache-budget-bytes D`，不再是整进程内存。

D 覆盖：常驻 1-bit codes、factors、routing 页缓存（含页元数据）、可选精细记录缓存（含其元数据）。**codes 与 factors 分列；本实验以完整 routing=codes+factors 为常驻边界。**

不占 D：centroid、线程栈、每 worker 查询缓存、visited、评分 scratch、routing 调度器以及其他进程开销。这些在独立的整进程安全检查中计账。本轮固定所有组 RLIMIT_AS soft=hard=2 GiB，不将它当作横轴或数据驻留条件；继承同查询缓存实现的 421 MiB 非数据安全包络，仅用于预计进程峰值验收。任何实际峰值超过计划都停止，不降线程、不静默切换策略。

完整 routing 阈值 T 从实际 sidecar 头读取：C+F；当前 GIST 为 148,000,000 字节（141.143799 MiB）。codes 自身为128,000,000字节，factors为20,000,000字节，不能混称同一个阈值。

- D<T：优先 factors 常驻，余下 D 用于 CLOCK codes 页缓存；factors+最小页槽放不下则全部分页。
- D=T：全量 codes+factors 常驻，无精细记录缓存。
- D>T：全量 routing 常驻，剩余 D 给原静态优先 hot_dynamic；剩余额度不足以建立缓存时保留为空，动态容量允许为零。
- 进程上限不足时独立拒绝；不能据此说 1-bit 数据放不下。

实验档位：64、128 MiB、T−1字节、T、T+16 MiB、256、512 MiB。精确边界用整数字节，不能把 T 先四舍五入再测试。

使用原 BFS 索引、beam1、workers32、独立 test 集原顺序前100条、L100/300/580、每档100条预热、两轮，第二轮反转预算顺序。每轮有同查询集无记录缓存的常驻参考；逐查询结果ID/recall/访问/剪枝/精算必须完全一致。统一O_DIRECT预读，不宣称冷设备缓存。旧实验结果保留，不通过改标签混入本轮。

```bash
python experiments/04_ours_memory_budget/gist_data_budget/prepare.py
python experiments/04_ours_memory_budget/gist_data_budget/run.py
python experiments/04_ours_memory_budget/gist_data_budget/report.py
```

源码/二进制：`work/ours_memory_budget/gist_data_budget/`。结果：`results/04_ours_memory_budget/gist_data_budget/`。

当前仍是 GIST 入口；不会声称支持任意数据集。数据字节取自真实头，GIST记录缓存最小布局与32线程约束仍沿用该实现。迁移数据集必须校准非数据运行内存并替换相应布局约束。
