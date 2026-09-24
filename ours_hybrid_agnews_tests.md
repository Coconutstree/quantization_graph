# AG News：hybrid六档配额选择与测试验收

状态：completed；当前：None；当前完成0/40档。

只测hybrid与无额外缓存基线。AG News独立200条验证查询按原保存顺序分成前100条训练热点、后100条选择配额；扫描0%、12.5%、25%、37.5%、50%、75%各两轮。AG News单独选比例，不假设GIST的37.5%最优。800条测试查询不参与热点排名及配额选择。

AG News：769,382条、1024维；原R64/Lbuild400图、BFS合并页与独立residual页；原查询顺序、40档L、beam1、workers32、100预热、INT8/DB1、top10、原rerank规则不变。每阶段预热为该阶段查询顺序前100条。

沿用GIST已测试的配额可配置二进制，不修改搜索内核。每个进程前统一O_DIRECT顺序预读实际搜索页一次；耗时单列。RLIMIT_AS 2 GiB，无NUMA绑定；内核及控制器缓存不计入进程上限。

AG News新增缓存统一额度：1267.57 MiB。
邻接表上限保持新增额度25%；静态记录扫6档配额，其余实际剩余额度归16分片FIFO page。记录真实缓存节点数和page数量；热点覆盖不足时不同配额可能得到相同实际配置。100条调参查询全部出现在预热中，属于重复查询预热口径，与GIST配额扫描相同。

执行顺序：内存校准基线 → 100条训练profile → 调参基线 → 6配额两轮（乱序＋反序） → 调参基线复测 → 锁定比例 → 200条全验证集profile → 测试基线 → 原25%与选定比例各两轮（原/选/选/原） → 测试基线复测。开始的800条校准基线仅用于内存额度、正确性和性能合理性检查，不进入配额选择分数。

每组验收逐查询ID、召回、访问/候选计数、内存、I/O统计及输入哈希。基线前后完整吞吐比例要求0.75–1.35；校准基线对历史AG News同配置也使用此门槛，失败即停止解释和后续运行，门槛不是统计等效检验。

QPS排除热点训练、缓存初始化、预读及预热；正式查询的缓存查找/维护和I/O全部计时。只有两轮及一个新增数据集，不宣称普遍最优。

## 已完成阶段

- calibration_baseline
- train_profile
- validation_baseline
- validation_r1_p3750
- validation_r1_p1250
- validation_r1_p5000
- validation_r1_p2500
- validation_r1_p0
- validation_r1_p7500
- validation_r2_p7500
- validation_r2_p0
- validation_r2_p2500
- validation_r2_p5000
- validation_r2_p1250
- validation_r2_p3750
- validation_baseline_end
- profile
- baseline_start
- hybrid_p2500_r1
- hybrid_p3750_r1
- hybrid_p3750_r2
- hybrid_p2500_r2
- baseline_end

## 验证集配额选择

选中比例：37.5%。以两轮全部40档合计吞吐排序，不低于最高值99%的候选中优先较小配额；这是固定工程规则，不是统计等效检验。

| 精细记录上限 | 两轮合计QPS |
|---|---:|
| 0.00% | 193.90 |
| 12.50% | 239.97 |
| 25.00% | 237.26 |
| 37.50% | 250.52 |
| 50.00% | 241.15 |
| 75.00% | 249.39 |

## 两轮合并测试结果

| 方案 | L | Recall@10 | QPS | 相对基线 |
|---|---:|---:|---:|---:|
| 基线 | 100 | 0.992625 | 188.66 | — |
| hybrid 25% | 100 | 0.992625 | 266.01 | +41.0% |
| hybrid 37.5% | 100 | 0.992625 | 288.20 | +52.8% |
| 基线 | 300 | 0.994375 | 90.89 | — |
| hybrid 25% | 300 | 0.994375 | 186.24 | +104.9% |
| hybrid 37.5% | 300 | 0.994375 | 200.66 | +120.8% |
| 基线 | 580 | 0.994375 | 51.87 | — |
| hybrid 25% | 580 | 0.994375 | 137.04 | +164.2% |
| hybrid 37.5% | 580 | 0.994375 | 135.76 | +161.7% |

原始记录：results/04_ours_memory_budget/hybrid_agnews/runs/；每档保存queries.jsonl和memory_stats，整组结束保存result.json、memory_measurement.json和acceptance.json。
