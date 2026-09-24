# AGNews 磁盘环境 32 线程实验配置核验

> 后续更新（2026-09-17）：本文保留初次核验时点的记录。用户已授权修复代码与两份飞书，并明确每个配置只跑一次；原先五重复要求不再适用，单次运行本身不是失效理由。当前实施状态和验证见 [修复记录](../disk_protocol_fix_20260917/README.md)，当前实验条件以 [新版协议](../../plans/DISK_EXPERIMENT_PROTOCOL_20260917.md)为准。

日期：2026-09-17。通过 `lark-cli docs +fetch --as user --detail full` 读取[AGNews 实验分析](https://my.feishu.cn/docx/AhoKdQxEBogmWoxfk6xcxHKjnsg)，revision 316，并交叉核对报告所指 CSV、原始 artifact、运行命令和 preflight。此次不修改该分析文档、不重跑实验。

## 结论

该组结果可以保留为历史端口与 I/O 诊断，不能作为当前四个原生磁盘系统的正式优劣证据。问题不限于 2 GiB，涉及内存记账、缓存配置、算法忠实性、不同 run 的测量条件及统计归因。文档已经有“重要更正”和撤回说明，但后面的“查询差距与不同”“Ours 进一步优化”仍沿用已失效排名和未证实假设，前后矛盾。

## 直接核对出的配置

| 项目 | Ours | OG-LVQ | Glass-NSG | SymphonyQG |
|---|---:|---:|---:|---:|
| CSV w32 行数 | 40 | 40 | 40 | 40 |
| repeat_id | 0 | 0 | 0 | 0 |
| 声明预算 GiB | 2 | 2 | 2 | 2 |
| artifact cache_bytes | 26,259,456 | 0 | 0 | 0 |
| artifact worker_scratch_bytes | 268,435,456 | 4,096 | 3,276,800 | 4,096 |
| artifact peak_rss_bytes | 818,089,984 | 12,304 | 3,280,996 | 12,288 |
| CPU affinity 字段 | 0–159 | uncontrolled | uncontrolled | uncontrolled |

上表仅转录历史字段，不承认这些内存字段均为真实峰值。OG-LVQ 的 12,304 正好等于 8,208 resident + 4,096 scratch；Symphony 的 12,288 正好等于 8,192 + 4,096；Glass 的 3,280,996 正好等于 4,196 + 3,276,800。这些值呈现分类求和，不是可靠的 32 线程进程峰值 RSS。Ours 的 CSV 各点峰值和 artifact 全批次峰值也要区分计量区间。

## 问题及影响

### 1. 无法从“预算=2”证明硬限制或可用余量

历史 artifact 与调用参数写有 `search_dram_budget_gib=2.0`，但此次读取的这些文件不足以证明操作系统强制限额及其范围。不能将后来的 RLIMIT_AS 执行证据追溯到 8 月/9 月初的旧运行。新方案必须单独保存限额机制、实际约束、峰值与事件。

原文已说明 Ours `resident_bytes=1019435246` 混入 full4-resident 消融的最大常驻预算，而当前曲线是 DB1+SSD 配置。由此计算“2 GiB 还空着约 1 GiB”没有依据；必须重测当前配置的全进程/资源组用量，包含所有线程及加载峰值。磁盘目录大小也不能代替进程内存用量。

### 2. 缓存配置差异需要按研究问题解释

Ours 有约 25.04 MiB 跨查询图缓存，另外三种旧端口为 0。该差异使“性能差全部来自 DB1”的解释不成立。共同总预算下允许各方法原生缓存不同；正式系统实验应开放官方参数并计入同一总预算，而不是强制每个方法多分同样的缓存。

原文提出 C0/B2 可以作为经明确定义的受控诊断；不能把它自动变成对 AiSAQ/Starling 等原生系统的统一改造要求。预算上限相同不要求实际内存相等，缓存相等也不证明总资源公平。

### 3. SymphonyQG 历史端口有额外候选整行读取

本次读到的原始 fingerprint 是 `05c-symphonyqg-fastscan-native-odirect-neighborfetch-v2`。文档 2026-09-08 更正明确撤回这条旧曲线；后续本地审计也指出应移除候选准入阶段无计算用途的强制整行读取。不能将旧 SymphonyQG 的 5.18 QPS 当作原论文方法固有性能。

`parity=passed` 不足以证明官方等价。历史 Ours artifact 甚至同时写 `implementation_parity=passed` 和 `mean_top10_overlap=0.0`，需要溯源该字段的生成含义，不能把标签视为通过独立正确性测试。已有 [2026-09-15 审计](../disk_fairness_audit_20260915/README.md) 区分了同一端口 AIO/mmap 对照与原生算法等价；Glass 已在有效量化并列距离测试上发现成员集合不一致，不能据此推断本次 AGNews 的具体影响程度。[OG-LVQ 审计](../og_lvq_official_parity_audit_20260915.md) 仍要求官方端到端对照。

### 4. 混合运行与一次重复不足以支持稳定排名

Ours/OG-LVQ/Glass 来自 `agnews_05c_rerun_20260901_152210`，Symphony 来自 `fix_w32_diskpayload_symphony_20260831_140957`。不同 run ID 本身不是错误；问题是没有证明设备状态、二进制语义、缓存与统计范围可比，而且每点只有一次重复。

两个 preflight 都标记 `disk_profile=hdd_raid`、`rotational=true`；它们不是已验证的 NVMe SSD 条件。4 KiB fio 预检在 QD32 时分别为 6504.99 与 5252.49 IOPS，QD128 时为 11538.29 与 10127.26 IOPS，说明至少两次预检数值存在差异；这不足以单独确定变化原因或查询性能影响。不能只凭接口 O_DIRECT/native AIO 把该结果当作 NVMe 性能。

Symphony 顶层 invocation_run 后来记录 workers=16，但其 w32 artifact 及对应 terminal.log 命令确实写 workers=32。这说明 run 级清单存在覆盖/混用风险，不证明该 w32 点实际只用了 16 线程。后续使用不可变运行记录，并核查实际线程和 CPU/NUMA 绑定。

### 5. 最高 QPS 与跨宽度平均数不是同 recall 对比

| 方法 | 报告的最高 QPS | 对应实际 Recall@10 |
|---|---:|---:|
| Ours | 202.1339 | 0.946625 |
| OG-LVQ | 56.7189 | 0.924875 |
| Glass-NSG | 135.5470 | 0.908750 |
| SymphonyQG | 5.18181 | 0.876375 |

以上仅用于揭示比较口径，不计算方法加速比。报告中的 586.72、2609.75、686.04、29576.80 ms 平均 latency，可由各方法 40 个宽度点的 mean latency 再取算术平均得到。它们不是统一 operating point 的指标。原文“同 recall 下 I/O 更低”也不能由跨宽度平均请求数证明。

“Recall 上限：Ours 最高 0.9944；Symphony 0.9992”在文内即自相矛盾；两者都只能描述此次有限参数扫描所观察到的最大 recall，不能称算法的理论上限。

### 6. 计时字段和读取模式不能直接作瓶颈归因

报告已承认部分 baseline 的 distance/queue 字段包含整个遍历，不能视为互斥阶段。后续源码审计还指出部分端口的每查询时延包括 GT recall 评估、另一些不包括，因此旧阶段占比和横向延迟需要重测。

不能从 io_wait 比例或带宽估算直接断言“页面已顺序化”“32 线程已打满盘”。同批页排序不保证连续 LBA，更不能把独立 HDD preflight、不同宽度均值和峰值 QPS 相乘来证明存储带宽。必须使用同运行、同点的实际 I/O 统计与设备证据。

### 7. 构建成本和存储算例仍有证据边界

原文区分 reused graph 与 from-scratch 是正确的。但 Ours 从零构建记录没有 Lbuild/alpha，而查询图参数来自另一份 manifest；总成本比较前应证明该构建记录确实产出被测图，且明确量化、训练、编码是否在 build 与 export 中重复计时。不能仅按名称拼接构建成本。

逻辑 compact、4+residual、页填充、图、sidecar 和整个目录应分栏；完整系统允许码率不同，但 nominal 4-bit 的量化器对比必须另计 residual 的额外位数与尺度。旧普通布局主码与残差已同读，文中再次建议“让两者同页”不能直接视为新增优化；当前 locality 将残差分开属于另一个有取舍的布局实验。

## 处理建议

1. 旧分析保留为历史问题记录；撤回说明应覆盖后续排名、瓶颈解释和优化收益排序。此次读取任务不直接修改该分析文档。
2. 保留经过核验的输入与索引；新 05C 主实验采用 Ours、DiskANN、AiSAQ、Starling 原生系统协议，旧移植版单列。
3. 按修订的“32 线程磁盘实验方案”先接入真实资源约束和计时核验，再执行 4 GiB 预检、正式 5 次重复与固定 32 线程预算扫描。
4. GIST/AGNews/DBpedia 按方法记录原生支持范围。AGNews 当前 Starling FP32 路径的页容量问题与内存问题独立，不强行补齐四条曲线。

本次原始读取、数据提取和证据保存在本目录 `agnews_before.json`、`agnews_csv_check.json`、`agnews_evidence.json`。未改动旧结果和实验代码。
