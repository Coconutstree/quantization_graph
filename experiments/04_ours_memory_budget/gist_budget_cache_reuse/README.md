# GIST 查询缓存共享与原位重置

独立版本；原 `gist_budget_sweep` 的源码、二进制、校准及结果不变。

- 每个 worker 的 DirectGraphReader 与 LocalityReader 从初始化时共享同一个 4 MiB QueryPageCache；worker 之间不共享。
- 查询间原位重置哈希索引、链表连接和命中/淘汰计数，保留页数组及容量，不分配新缓存。仅在持有缓存 mutex、上一个同步查询结束后调用。
- 保留原候选顺序、评分内核、文件命名空间、routing 策略及 32 workers。
- `prepare.py` 从旧冻结快照复制 Rust/C++ 依赖，在独立依赖树内修改；不会修改公共 C++ 头或正在运行的旧版本。

执行：

```bash
python experiments/04_ours_memory_budget/gist_budget_cache_reuse/prepare.py
python experiments/04_ours_memory_budget/gist_budget_cache_reuse/run.py
python experiments/04_ours_memory_budget/gist_budget_cache_reuse/report.py
```

结果：`results/04_ours_memory_budget/gist_budget_cache_reuse/`。

校准沿用四分支、validation 集前 100 条、L100/300/580、每档 100 条预热，整进程 RLIMIT_AS soft=hard。仍保留 8 MiB 完整查询集余量及 64 MiB 安全余量，不手动降低准入预留。

256/384/512/640 MiB 均进行硬限制实测。通过新预检的预算验证 auto 分支；未通过预检的预算进行绕过保守准入估计的显式全分页诊断：64 routing pages、32 workers、同样三档 L 和预热。诊断成功仅表示该固定小缓存配置下通过；不自动推翻保守准入规则。已通过 auto 的预算不再启动新的小缓存诊断，probe.json 明确标记 not_run 并引用实际验收；调整执行顺序前已启动的 512 MiB 补充诊断仍保留。失败不重试、不改线程、不填零吞吐。

本轮用于内存和逐查询正确性验证，可能与已启动的旧实验重叠；不将测得耗时作为独占设备的 QPS 对比，也不覆盖旧性能点。若需绘制新版本性能图，须另行独占运行两轮测试集实验。

验证包括 C++ 100 次填满/淘汰/清空/重填（clear 无分配、无陈旧命中）、原有 Rust 回归、逐查询结果与计数、硬限制、routing 在途/容量和物理 I/O 计账。

报告正文包含全部 24 行 run×L 实测数据（Recall、QPS、P95/P99、总读页及 routing 读页）。完整列见结果目录的 `measured_results.csv` / `measured_results.json`，原始结果与逐查询数据链接随行保留。计时是并发工作下的观测，不作为独占设备性能对比。

`report.py` 会自动调用 `plot.py`，生成并嵌入 `figures/budget_curves` 与 `figures/calibration_curves`（PNG/PDF/SVG）。主图纵轴采用对数刻度，失败标记独立于吞吐轴。

阈值的逐项计算、跨数据集公式及当前 GIST 专用常量限制，见报告“562.14 MiB 是什么”一节；562.14 MiB 是本配置的整进程 routing 常驻准入，不是 codes 自身大小。文档由 `threshold_explanation.py` 按校准输出生成。
