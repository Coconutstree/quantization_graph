# Hybrid固定缓存额度配比实验

状态：completed；当前：None；当前已完成L：0/40。

总新增缓存额度固定1,247,580,160字节（1189.78515625 MiB），整个进程RLIMIT_AS仍为2 GiB。原BFS布局、beam1、workers32、INT8/DB1、100预热、40档L保持不变。无NUMA绑定。

原200条验证查询按保存顺序前100条训练热点、后100条选择配额，二者与800条测试查询不重叠。每阶段沿用原流程：预热该阶段查询顺序前100条；调参集共100条，所以调参预热覆盖整批100条，属于重复查询预热口径，不宣称首次冷查询性能。

邻接表最多占总额度25%；静态精细记录上限依次为0%、12.5%、25%、37.5%、50%、75%；扣除静态实际占用后的全部剩余空间给16分片FIFO page。只选正分热点，配额超过热点覆盖后可能出现相同实际配置，这些点仍照实记录，不声称不同配置。

每个比例完整40档两次，第一轮固定种子打乱，第二轮反序；采用合并两次共8000条计时查询数/总查询秒数（40档每档100条每轮）排序，不挑单个L。以最高分的99%以内为近似并列，优先选精细记录配额更小者；这是预先固定的工程规则，不是统计显著性检验。

统一O_DIRECT顺序预读在每个搜索进程前执行一次，失败即停；预读、热点训练、初始化及预热不计查询QPS。缓存维护与查询I/O全部计时，缓存计入进程预算。

选定比例先写selection_lock.json，再使用已经验收的全部200条验证查询热点排名（与原v3相同）进行测试；测试结果不得反向更改比例。原25%与所选比例各两次交错测试，另在测试序列前后各跑一次无额外缓存基线。若选择25%，复用同一组作为default与selected，不重复伪造独立对照。

所有纯缓存运行须逐查询结果/召回/访问/候选计数一致，I/O统计与trace相符，内存预算通过。基线相对同查询集参考完整40档总吞吐允许0.75–1.35用于阻止数量级漂移；该门槛不证明统计等效。失败停止，记录原因，不自动放宽额度或改变协议。

## 完成阶段

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
- test_baseline_start
- test_p2500_r1
- test_p3750_r1
- test_p3750_r2
- test_p2500_r2
- test_baseline_end

## 验证集配额选择

锁定精细记录配额：37.50%。

| 精细记录上限 | 两轮合计QPS |
|---|---:|
| 0.00% | 139.09 |
| 12.50% | 176.59 |
| 25.00% | 203.63 |
| 37.50% | 241.08 |
| 50.00% | 207.84 |
| 75.00% | 225.50 |

## 测试集L100（单次行；基线分母为当前已完成基线的合并吞吐）

| 运行 | Recall | QPS | 相对基线 |
|---|---:|---:|---:|
| test_baseline_start | 0.951375 | 111.17 | — |
| test_p2500_r1 | 0.951375 | 260.02 | +133.4% |
| test_p3750_r1 | 0.951375 | 264.95 | +137.8% |
| test_p3750_r2 | 0.951375 | 354.96 | +218.6% |
| test_p2500_r2 | 0.951375 | 281.58 | +152.8% |
| test_baseline_end | 0.951375 | 111.62 | — |

## 最终两轮合并结果

按每档两轮1600条查询/合计查询秒数计算QPS；百分比分母为本轮两次基线合并QPS。

| 方案 | L | Recall | QPS | 相对基线 |
|---|---:|---:|---:|---:|
| 原25% | 100 | 0.951375 | 270.37 | +142.7% |
| 选中37.5% | 100 | 0.951375 | 303.42 | +172.4% |
| 原25% | 300 | 0.983500 | 162.30 | +223.1% |
| 选中37.5% | 300 | 0.983500 | 200.24 | +298.6% |
| 原25% | 580 | 0.986500 | 91.40 | +216.5% |
| 选中37.5% | 580 | 0.986500 | 98.19 | +239.9% |

## GIST六档配额曲线（验证集）

![GIST六档hybrid配额Recall–QPS](results/04_ours_memory_budget/hybrid_tuning/figures/gist_hybrid_final.png)

包含基线和0%、12.5%、25%、37.5%、50%、75%全部六档配额；单张Recall–QPS图，已删除右侧增长幅度图。数据来自100条调参验证查询，各配额两轮，主线为两轮合计查询数/合计查询时间，阴影为两轮最小–最大值，不是置信区间。基线也使用调参前后两轮。全部40档均保留，无平滑或插值。同L召回一致。

这里只能用验证集：测试集实际只测了25%和选中37.5%，其余配额没有测试集结果。该图不能当作六档配额的测试集验收图。

[PDF](results/04_ours_memory_budget/hybrid_tuning/figures/gist_hybrid_final.pdf) · [SVG](results/04_ours_memory_budget/hybrid_tuning/figures/gist_hybrid_final.svg) · [两轮合并源数据](results/04_ours_memory_budget/hybrid_tuning/figures/source_pooled.csv)

## GIST不同内存方案曲线（测试集）

![GIST八种内存方案Recall–QPS](results/04_ours_memory_budget/v3_direct/figures/gist_memory_strategies.png)

原v3_direct的基线＋7个方案，每组800条测试查询、40档L、一次完整扫描，共320个展示点，无误差带。图中Hybrid为原25%配额；没有混入后来单独调参实验的37.5%结果。导航三组会改变召回；已按要求从图中删除Full Records曲线及其40个点，原始实验记录保留。

[PDF](results/04_ours_memory_budget/v3_direct/figures/gist_memory_strategies.pdf) · [SVG](results/04_ours_memory_budget/v3_direct/figures/gist_memory_strategies.svg) · [绘图源数据](results/04_ours_memory_budget/v3_direct/figures/memory_strategies_source.csv)
