# GIST：只限制 codes 容量

`--codes-cache-budget-bytes B` 只用于：

- 分页时的 **codes 页数据缓冲**（每页4096字节，包含页内对齐空间）；
- 常驻时的全量 codes，剩余 B−C 给 hot_dynamic 记录缓存（该可选记录缓存的索引等元数据也在这份剩余额度内计账）。

**factors 始终全量常驻、在 B 之外另计；routing 页元数据也在 B 外。** centroid、查询预处理数据、线程缓存/visited/候选池/栈、评分 scratch、routing 服务及其他进程开销均不占 B。它们仍计入实际 VmPeak/RSS 和独立的整进程安全检查，不是“免费内存”。

GIST文件头给出 C=128,000,000字节（122.0703125 MiB），F=20,000,000字节（19.073486 MiB）。

```text
B < C：factors驻留，codes分页；页数据槽数=floor(B/4096)
B = C：codes恰好全驻留，无可选记录缓存
B > C：codes全驻留，B−C给hot_dynamic；不足建缓存时可以为空
```

不再以 C+F 作为 codes 的阈值；无论改变 F 还是独立进程上限，只要进程安全检查通过，同一个 B 的代码驻留判断不变。所有实测组RLIMIT_AS soft=hard=2GiB。

实际评分流程：读取图邻居→查visited得到fresh→对查询预处理→用1-bit codes、候选factors和预处理查询计算内积估计与距离下界→与候选池阈值比较剪枝→读取幸存者compact 4-bit记录计算更细距离→更新候选池→最终读取residual重排。所需数据与占用B的范围不能混为一谈。

本轮只改存储预算，不改以上计算内核/候选顺序。使用原BFS索引、beam1、workers32、独立test顺序前100条、L100/300/580、每L预热100条、两轮反转预算顺序；逐查询对照同期常驻参考。沿用O_DIRECT预读，不宣称冷设备缓存。

```bash
python experiments/04_ours_memory_budget/gist_codes_budget/prepare.py
python experiments/04_ours_memory_budget/gist_codes_budget/run.py
python experiments/04_ours_memory_budget/gist_codes_budget/report.py
```

结果目录：`results/04_ours_memory_budget/gist_codes_budget/`。旧整进程和C+F共享额度实验均保留、标明口径，不将旧数据改标签冒充本轮结果。

当前仍为GIST/32workers专用入口，阈值取自实际codes文件头；其他数据集要校验实际stride与布局、单独预算factors和其他运行内存。
