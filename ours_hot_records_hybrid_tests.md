# GIST / AG News：hot records、hybrid 与动态记录缓存对比

状态：两个数据集全部完成；静态hot records、hybrid及新方案均为两轮完整40档结果。

两个数据集依次运行，各使用完整800条测试查询、原40档L、beam1、workers32、100预热、原查询顺序和BFS布局。每个进程统一O_DIRECT完整顺序预读；预读失败即停。进程RLIMIT_AS 2 GiB，无NUMA绑定。

按用户要求不再复测基线和hybrid。复用各数据集已验收的基线两次及选定hybrid两轮，仅补跑hot records两轮、每轮完整40档。取消的GIST未完成基线保存在cancelled_retest，不参与结果。

GIST额外缓存上限1189.78515625 MiB；AG News上限1267.57421875 MiB，均沿用各自已校准额度，不因方法调整。hot records使用全部额度选择正分热点的compact＋residual，静态名单不替换，热点不足不填非热点。hybrid使用各数据集验证集已锁定的比例（当前均37.5%），邻接表上限25%，实际剩余额度给16分片FIFO页缓存。两者复用同一份200条独立验证查询热点排名；测试集不训练、不选比例。

相同的是可用额度，实际内存可能不同；记录缓存实际计账、节点数、页容量、RSS/VmPeak以及读页/查询。QPS排除准备、初始化和预热，包含查询期间缓存维护和I/O。

验收：40档全量逐查询ID、召回及访问/候选计数与基线一致；I/O统计与trace一致；进程内存及缓存额度通过；输入、二进制和profile哈希固定。复用对照原实验的基线漂移验收，不再增加同期基线。hot records与对照测量时间不同，当前对比不能排除时段/负载变化影响，不作为严格同期性能结论。失败保留日志并停止，不静默跳过或放宽门槛。

每档两轮QPS按合计查询数/合计秒数汇总；阴影表示两轮最小–最大范围，不是置信区间。不平滑、不丢弃L。每个数据集分别生成基线、hot records、hybrid、hot_dynamic四条Recall–QPS曲线。

## 新方案：hot_dynamic（静态热点＋动态记录缓存）

保留原hot records静态热点；剩余额度建立16分片FIFO动态节点记录缓存。compact和residual分别在实际读到后填入，分别标记有效，不额外读盘补齐；静态热点不替换。动态节点首次插入进入FIFO，命中不更新顺序，满时淘汰所在分片最早插入节点。每档L清空动态内容后执行原100条预热，正式计时前只清计数。查询中的查找、复制、填充和淘汰均计入时间。

缓存计账包含静态记录、动态预留缓冲、映射和元数据；动态预留空间不等于有效数据量。两个数据集的额度可覆盖全库精细记录，本轮未必频繁触发淘汰；淘汰行为另有小容量测试。这里只新增新方案测量，既有对照没有重跑。测量时段及新方案二进制不同，不能完全排除环境与执行路径差异；不据此宣称普遍最优。

[新方案方法与进度](ours_dynamic_records_tests.md)。动态命中、有效字节、驻留节点、插入和淘汰见 `results/04_ours_memory_budget/dynamic_records/{gist,agnews}/dynamic_stats.csv`。新增曲线为两轮合并QPS，阴影是两轮最小—最大范围，不是置信区间；全部40档均保留。

## 已完成

- agnews/hot_dynamic_r2
- agnews/hot_dynamic_r1
- gist/hot_dynamic_r2
- gist/hot_dynamic_r1
- gist/hot_records_r1
- gist/hot_records_r2
- agnews/hot_records_r1
- agnews/hot_records_r2

## gist 两轮结果

| 方案 | L | Recall@10 | QPS | 相对基线 | 缓存计账MiB | 读页/查询 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 100 | 0.951375 | 111.40 | — | 0.00 | 1100.38 |
| hot_records | 100 | 0.951375 | 271.18 | +143.4% | 402.69 | 350.61 |
| hybrid | 100 | 0.951375 | 303.42 | +172.4% | 1189.76 | 137.18 |
| hot_dynamic | 100 | 0.951375 | 412.92 | +270.7% | 1133.18 | 244.44 |
| baseline | 300 | 0.983500 | 50.23 | — | 0.00 | 2373.29 |
| hot_records | 300 | 0.983500 | 123.85 | +146.5% | 402.69 | 825.35 |
| hybrid | 300 | 0.983500 | 200.24 | +298.6% | 1189.76 | 182.37 |
| hot_dynamic | 300 | 0.983500 | 188.35 | +274.9% | 1133.18 | 508.46 |
| baseline | 580 | 0.986500 | 28.88 | — | 0.00 | 4000.81 |
| hot_records | 580 | 0.986500 | 69.98 | +142.3% | 402.69 | 1448.83 |
| hybrid | 580 | 0.986500 | 98.19 | +239.9% | 1189.76 | 246.94 |
| hot_dynamic | 580 | 0.986500 | 118.62 | +310.7% | 1133.18 | 810.03 |

![gist 四方案Recall–QPS](results/04_ours_memory_budget/hot_records_comparison/gist/figures/recall_qps.png)

[PDF](results/04_ours_memory_budget/hot_records_comparison/gist/figures/recall_qps.pdf) · [SVG](results/04_ours_memory_budget/hot_records_comparison/gist/figures/recall_qps.svg) · [完整40档结果](results/04_ours_memory_budget/dynamic_records/gist/comparison.csv)

## agnews 两轮结果

| 方案 | L | Recall@10 | QPS | 相对基线 | 缓存计账MiB | 读页/查询 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 100 | 0.992625 | 188.66 | — | 0.00 | 662.60 |
| hot_records | 100 | 0.992625 | 320.48 | +69.9% | 352.80 | 325.31 |
| hybrid | 100 | 0.992625 | 288.20 | +52.8% | 1267.56 | 143.13 |
| hot_dynamic | 100 | 0.992625 | 383.32 | +103.2% | 871.67 | 243.38 |
| baseline | 300 | 0.994375 | 90.89 | — | 0.00 | 1264.07 |
| hot_records | 300 | 0.994375 | 153.08 | +68.4% | 352.80 | 687.13 |
| hybrid | 300 | 0.994375 | 200.66 | +120.8% | 1267.56 | 169.81 |
| hot_dynamic | 300 | 0.994375 | 205.72 | +126.3% | 871.67 | 470.24 |
| baseline | 580 | 0.994375 | 51.87 | — | 0.00 | 2278.51 |
| hot_records | 580 | 0.994375 | 89.36 | +72.3% | 352.80 | 1183.49 |
| hybrid | 580 | 0.994375 | 135.76 | +161.7% | 1267.56 | 160.14 |
| hot_dynamic | 580 | 0.994375 | 135.40 | +161.0% | 871.67 | 731.02 |

![agnews 四方案Recall–QPS](results/04_ours_memory_budget/hot_records_comparison/agnews/figures/recall_qps.png)

[PDF](results/04_ours_memory_budget/hot_records_comparison/agnews/figures/recall_qps.pdf) · [SVG](results/04_ours_memory_budget/hot_records_comparison/agnews/figures/recall_qps.svg) · [完整40档结果](results/04_ours_memory_budget/dynamic_records/agnews/comparison.csv)

原始结果：`results/04_ours_memory_budget/hot_records_comparison/{gist,agnews}/runs/`。每组保留command.json、queries.jsonl、memory_stats/L*.json、result.json、memory_measurement.json、storage_precondition.json、acceptance.json；数据集结束输出comparison.json/csv和figures。

运行：`python experiments/04_ours_memory_budget/hot_records_comparison/run.py --run`；可恢复已验收组，未验收的残留组须人工核查后处理。
