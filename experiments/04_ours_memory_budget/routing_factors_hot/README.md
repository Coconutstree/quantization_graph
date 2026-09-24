# Factors 常驻与热点 codes 页试验

保持同一 sidecar 与索引布局。分页时可选 `--routing-factors resident`，将 factors 完整加载并从原预算扣除；codes 仍经共享 CLOCK 分页。默认仍为 `--routing-factors paged`，完整 routing 放得下时保留原常驻路径。

`--routing-hot-pages FILE` 接受升序、不重复的 little-endian u64 codes 页号，命中后不参与淘汰；热点页占用既有页槽，至少保留一个可替换槽。不预加载、不跨轮推测。文件与 native 页号数组的峰值空间计入预算。

`--routing-profile-output FILE` 是独立诊断功能，输出每个 codes 页成功获取次数（little-endian u64），计入命中及共享读取，重试失败不重复计数。计数数组与输出副本显式计账。性能试跑关闭 profile。

## 运行

```bash
python experiments/04_ours_memory_budget/routing_factors_hot/run.py --build --run
python experiments/04_ours_memory_budget/routing_factors_hot/run.py --regression
python experiments/04_ours_memory_budget/routing_factors_hot/report.py
```

GIST，beam1、workers32，同样的独立测试前 100 条。L100 两轮（第二轮反序），同期常驻与三种分页配置：CLOCK、factors 常驻、factors 常驻+热点。总预算分别沿用此前 25%/75% 档；新分支的 codes 缓存比例不再等于这些数字。

热点仅来自 validation split 前 100 条，与测试 query 向量逐项检查无重合；热点大小预先固定为 factors-only 缓存槽数的 10%，不根据测试结果调参。L300/580 各一次扩展正确性回归，不混入主性能结果。沿用 O_DIRECT 顺序预读与 100 查询预热，设备缓存未受控。

每个测量查询对照同期常驻结果、召回和访问/剪枝/精算/重排计数；物理 I/O 与 routing 统计对账，验收 RLIMIT_AS、VmPeak、RSS、64 页在途上限。预算是保守估算准入，不表示同预算常驻已实测 OOM。

源码及二进制冻结：`work/ours_memory_budget/routing_factors_hot/`。
原始测量：`results/04_ours_memory_budget/routing_factors_hot/`。
此前 optimized 与 v1 二进制、结果均保留。
