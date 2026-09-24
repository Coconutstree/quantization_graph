> 数据清理说明：旧v2性能原始结果和图表已按用户要求删除；本文保留排查过程，指向旧v2性能文件的链接不再有效。历史03基线及独立恢复诊断证据未删除。新结果见 [当前测试文档](ours_memory_budget_tests.md)。

> 更新：已通过统一 O_DIRECT 顺序预读恢复基线性能，见 [恢复证据](docs/diagnostics/OURS_QPS_STORAGE_RECOVERY_20260919.md)。正在按同一预读协议重跑全部内存策略，进展见 [当前测试文档](ours_memory_budget_tests.md)。下文旧v2数值保留追溯，不作为新条件下的加速结论。

# GIST 基线QPS异常：历史原程序与新版交错复测

**当前已定位到存储I/O完成等待：历史原二进制在当前环境也只有约10 QPS，与新版接近。因此新增缓存/统计代码不是本轮约十倍下降的必要条件，也不是这次A/B所显示的主要原因。历史环境为何能达到109 QPS，尚缺当时的设备/控制器遥测，不能进一步断言硬盘故障、RAID降级或缓存策略变化。**

## 1. 直接对照：不是靠重新编译猜测历史程序

- 找回历史实际二进制，完整SHA-256为 `a64dfc64e5f351aa48e5d929d8413143935aa0b6079046402d3a04a2f826c92c`，与历史测量记录相同。
- 新版使用原v2二进制 `8e5526a71d9ffa960358d251635e07d7c2b0e727130c5ba05944789de0b58637`，未修改或重编译。
- 按旧→新→新→旧（ABBA）串行交错。只将L缩为100做隔离诊断，其余沿用原800条测试查询、查询顺序、100条预热、32线程、beam1、2 GiB RLIMIT_AS及测量器环境。正式40档结果未被覆盖。
- 四次均通过内存限制检查；800条查询的ID、召回、访问/距离/DB1/full4计数、I/O请求数和页数逐条与历史L100完全一致。每次均1100.385页/查询，Recall=0.951375。

| 执行 | 程序 | QPS | 读取路径等待 ms/查询 | sda读等待 ms/请求 | 主机CPU空闲 |
|---|---|---:|---:|---:|---:|
| 历史记录 | 原历史程序 | 109.30 | 256.80 | 未记录 | 未记录 |
| v2原记录 | 新版baseline | 9.73 | 3228.53 | 未记录 | 未记录 |
| 1_old | 历史原二进制 | 9.83 | 3207.07 | 9.85 | 59.7% |
| 2_new | 新版二进制 | 9.64 | 3255.28 | 9.84 | 59.4% |
| 3_new | 新版二进制 | 9.76 | 3216.48 | 9.78 | 59.5% |
| 4_old | 历史原二进制 | 9.80 | 3207.05 | 9.69 | 59.5% |

旧程序两次平均 **9.82 QPS**，新版两次平均 **9.70 QPS**，新版/旧版为 **0.988**。这个差异无法解释历史与本轮约11倍的差距；但每种仅两次，不用于证明零开销或统计显著差异。

### 配置相同的边界

搜索参数、输入内容、页布局、每查询4 MiB LRU、测量器环境及地址空间上限已逐项比对；但不能称所有条件完全一致。历史和v2正式运行均为40档完整扫描，本次ABBA仅L100。历史L100之前已跑过27档L=10–90，这可能预热控制器/设备缓存；每档100条查询预热不等价于此前完整扫描的存储状态。ABBA证明同一当前诊断条件下新旧程序接近，尚不能单独区分历史环境变化与扫描前序引起的缓存状态差异。历史原二进制完整40档对照现已获授权并启动，见[独立复现报告](results/04_ours_memory_budget/historical_full_replay_20260919/report.md)及[实时状态](results/04_ours_memory_budget/historical_full_replay_20260919/state.json)。

此外新版结果多出timing_semantics=search_wall_excludes_recall_evaluation，旧结果没有该字段；不能仅由字段缺失认定计时实现相同或不同。不过同一旧二进制的历史/当前差异不涉及新旧版本计时定义变化。CPU允许范围0–159和NUMA节点范围0–3一致，但不代表每个线程实际调度到相同核心或相同NUMA内存放置。

### 完整顺序复现的阶段性更新

完整扫描已通过L100及其后的L140/180/220。L100前27档均按原顺序执行，但平均查询延迟仍为3186.86 ms（历史289.49 ms），读取路径等待3172.80 ms（历史256.80 ms）。因此单测L100缺少前序扫描不足以解释差距；所有历史缓存状态因素尚未排除。任务尚未完成，准确QPS等待原生result.json，不用延迟倒数替代。详见[阶段性复盘](results/04_ours_memory_budget/historical_full_replay_20260919/interim_review.md)。

## 2. 已从“读取路径计时”进一步定位到实际块设备等待

- 旧程序运行中读取全部线程的等待位置：1个协调线程在 `futex_wait_queue_me`，32个搜索线程都在 `read_events` 等待AIO完成。这是一次快照，不能解释为每个线程全程都在那里。
- 诊断期间每秒记录 `/proc/diskstats`、`/proc/stat`、CPU/I/O pressure及程序状态。活跃读取窗口内sda约每秒1万次读请求，平均每次读等待约10 ms，设备忙碌时间约100%。这些是设备聚合数据，覆盖预热和搜索，不能与单查询延迟直接相加。
- RAID设备的100%忙碌本身不等于达到物理吞吐上限；结合线程等待和读请求延迟，才支持“当前搜索主要等待存储I/O完成”的判断。
- 当前路径落在XFS的 `ubuntu--vg-ubuntu--home`（dm-2），底层sda呈现为 **DELL PERC H740P Adp**。`ROTA=1`是控制器暴露的逻辑设备属性，不能单凭它确认后端所有盘的介质类型，更不能据此宣称SSD/HDD故障。

## 3. 对原先五条疑点的逐项修正

| 原疑点 | 新证据 | 当前判断 |
|---|---|---|
| 返回结果相同但性能不同 | 四次A/B逐查询结果、计数与历史完全一致；旧程序也慢 | 正确性无异常；环境相关性能差异可复现 |
| I/O等待统计含缓存、分配和复制 | 搜索线程实际阻塞在read_events；块设备read await约10 ms | 已有独立内核证据，不仅是程序内计时标签 |
| 新二进制或统计钩子导致十倍退化 | 旧原二进制也约10 QPS；新旧AIO关键函数规范化汇编一致 | 新增代码不是主要数量级差异；微小开销未精确隔离 |
| 高CPU任务或ROTA=1说明根因 | 主机约60% CPU空闲；高CPU任务两秒采样的read_bytes无增长；ROTA是RAID逻辑属性 | 不能把CPU负载或介质标记直接当原因；短采样不排除所有外部I/O |
| 缺少运行时遥测 | 本次四次诊断新增每秒主机/进程遥测 | 当前瓶颈已定位，但历史控制器/设备状态仍缺失 |

补充排除项：

- 查询诊断进程及上级cgroup，`cpu.max`为无限制，`io.max`未配置限额，CPU允许列表0–159；没有发现当前cgroup CPU/I/O硬限速。
- graph_compact、residual、ID映射等布局文件的inode与mtime均和历史storage_protocol一致，哈希此前已核验，未发现换文件、重排页或数据重写的证据。
- 原/新程序解析到相同的libaio、libc、libstdc++等动态库路径。AIO read_pages、构造函数及C桥接函数移除加载地址/RIP重定位后，分别633、203、162条汇编指令一致。这不是整个程序的等价证明，但没有发现底层AIO实现替换。
- 当前普通用户不可读系统内核journal，未取得RAID状态查询工具输出；因此没有控制器缓存命中率、物理盘健康、重建/巡检状态、固件事件等证据。不可将“无权限看到日志”写成“没有错误”。

## 3.1 专项核查：是否换了数据布局或存储位置

本轮新增只读核查已保存为[布局与位置证据](results/04_ours_memory_budget/baseline_diagnosis_20260919/layout_location_audit.json)。结论：**没有发现普通布局/BFS布局混用、页文件替换、路径重定向或由NVMe迁往HDD的证据。不能把十倍差距归因于已证实的布局改变。**

| 检查项 | 核查结果 | 证据边界 |
|---|---|---|
| 查询真正使用的布局 | 历史与v2命令均指向gist_bfs_split_20260912_115121，运行结果记录了相同layout路径和页哈希 | 两次均为graph+compact合并、residual分离 |
| 三个关键文件 | graph_compact.pages、residual.pages、id_to_slot.u32重新计算SHA-256，均匹配两次命令；inode和mtime均匹配历史快照 | 内容/ID映射相同；inode一致不能证明底层物理扇区永远不变 |
| 9月18日路径迁移 | 迁移记录整理results及artifacts；没有work/下布局目录迁移条目。关键页路径及祖先无symlink重定向 | 这是现有迁移清单和当前路径检查，不是所有历史管理操作日志 |
| 当前文件块分配 | 两份页文件分别只有1个extent，文件内逻辑范围在XFS块设备上连续 | 没有当前碎片化证据；无历史extent图可比；RAID仍会在物理盘间分条 |
| 历史存储类型 | 9月10日GIST建图preflight：disk_root同现有work目录，hdd_raid、rotational=true、O_DIRECT | 是建图时预检，非9月12日快基线运行当刻的设备拓扑快照 |
| 当前实际挂载 | 宿主机/home → /dev/mapper/ubuntu--vg-ubuntu--home，XFS；底层sda显示DELL PERC H740P Adp | 早期归档日志也记录同名逻辑卷；缺历史UUID及RAID后端映射，不能声称物理盘从未变化 |

**补充线索：历史存储预检本来就不快。** 9月10日的8 GiB、4 KiB、O_DIRECT/libaio随机读预检，QD32约5085 IOPS，QD128约9552 IOPS、平均延迟13.41 ms。当前诊断约1万IOPS和约10 ms等待，与这个量级接近。两种工作负载不同，不能直接比较或据此证明设备没有变化，但这削弱了“原来是高速NVMe、现在换成慢盘”的猜测，也不足以宣称设备后来退化了十倍。

历史109 QPS仍可能与设备/控制器缓存和扫描前序有关，当前尚无证据证明此假设。原历史二进制完整40档扫描已启动，结果单独记录；本次只读布局核查不能替代该控制实验。

来源：[历史GIST存储预检](results/archive/native_runs/runs/disk_03_graph_build_reuse_20260910_gist/manifests/preflight.json)、[历史fio预检](results/archive/native_runs/runs/disk_03_graph_build_reuse_20260910_gist/manifests/fio_preflight.json)、[迁移清单](results/manifests/path_migration_20260918.json)、[当前extent](results/04_ours_memory_budget/baseline_diagnosis_20260919/layout_current_extents.txt)。

## 4. 目前能下的结论与仍不能下的结论

**可以下结论：** 当前约10 QPS在历史原程序上也能复现；新增策略不是产生低基线的必要因素。当前搜索等待实际存储完成是主要瓶颈。单纯回滚新代码或删除统计钩子，没有证据能恢复历史109 QPS。

**仍不能下结论：** 9月12日到本次诊断间，究竟是控制器读缓存状态、设备配置、物理盘工作状态、其他I/O或其他存储环境变化造成了差距。本次没有历史对应遥测，也没有控制器级干预实验。这些只能列为待证实原因。

下一步定位设备层具体原因需要管理员提供只读RAID/控制器状态、读缓存策略和命中率、物理盘健康与后台重建/巡检任务、相关时段内核事件。若进行冷/热或缓存策略对照，应单独批准和记录；此次未清系统缓存、未更改RAID配置、未暂停其他任务。

### baseline封装与计时专项排查

QPS在原生程序内按完成查询数/搜索墙钟时间计算，不是Python测量器重算。历史40档计时合计316.41秒，外部进程376.39秒；v2分别3431.13秒和3897.33秒。L100的QPS×平均查询延迟分别约31.64与31.62，未发现线程数或微秒/秒换算导致十倍读数错误；这些只是计时一致性检查，不证明所有计时路径没有问题。

已追加[启动封装对照](results/04_ours_memory_budget/launch_path_diagnosis_20260919/report.md)：等待当前历史40档复现完成后，串行直接启动新版/旧版L100，保留2 GiB及原查询参数，去掉/proc周期轮询和主机遥测。该项执行前不能声称已排除测量器开销。

## 5. 对缓存实验结果的影响

- 原v2数据没有被删除或按比例修正，依然表示该次运行的观察。
- 现在不能再把“新程序可能十倍变慢”作为首要解释；同环境旧/新对照已不支持这个假设。
- 缓存方案的加速比仍仅适用于该次I/O条件，不能直接推广到历史109 QPS的环境；“全库记录或混合方案最好”仍需在目标部署存储条件下重测。不能用历史109直接替换本轮9.73作分母。
- 新增4次L100只用于诊断，不混入360行正式曲线，也不重算正式缓存额度。

## 6. 证据与复现

- [诊断协议、四次运行及遥测](results/04_ours_memory_budget/baseline_diagnosis_20260919/)
- [诊断指标汇总](results/04_ours_memory_budget/baseline_diagnosis_20260919/diagnosis_metrics.json)
- [线程等待快照](results/04_ours_memory_budget/baseline_diagnosis_20260919/old_thread_wait_snapshot.json)
- [cgroup限额](results/04_ours_memory_budget/baseline_diagnosis_20260919/cgroup_limits.json)
- [底层I/O汇编对照](results/04_ours_memory_budget/baseline_diagnosis_20260919/io_assembly_comparison.json)
- [历史文件身份对照](results/04_ours_memory_budget/baseline_diagnosis_20260919/layout_file_identity.json)
- [诊断执行脚本](experiments/04_ours_memory_budget/diagnose_baseline.py)、[指标汇总脚本](experiments/04_ours_memory_budget/summarize_baseline_diagnosis.py)、[本文生成脚本](experiments/04_ours_memory_budget/build_baseline_audit.py)。执行脚本拒绝覆盖已有目录；汇总和文档生成不启动性能测试。


## 2026-09-19 补充：直接预读恢复实验

本节更新前文“尚无预处理干预证据”的阶段性结论，保留前文作为排查记录。

- 新旧布局分别交替两次：旧 9.558 / 9.346 QPS，新 82.770 / 83.820 QPS。所有主文件均为单个连续区间，未发现旧文件碎片更多。
- 对同一旧布局两个页文件执行只读 `dd iflag=direct bs=4M of=/dev/null` 后，旧布局恢复到 **79.581 QPS**；紧接的新布局为 80.247 QPS。旧文件 inode、size、mtime 未变，800 条查询结果及 I/O/查询缓存等 13 字段全部一致。
- 因而重新导出或改变文件位置不是恢复的必要条件。主要差异与可被直接顺序预读改变的下层存储状态有关，强烈支持设备缓存/预取解释；没有控制器遥测，尚不能指认具体层次、配置或历史状态变化事件。
- 普通 buffered 预读未恢复（9.629 QPS），不能拿普通哈希校验代替本次直接 I/O 干预。
- 单档约 80 QPS 仍低于历史109.301。补齐原40档顺序后，历史原程序 L100 已恢复至 **108.816 QPS**（历史109.301，差0.44%）；40档总计时318.698秒（历史316.407秒），32,000条查询全部通过9字段一致性检查及2 GiB限制核验。说明直接预读加原始完整扫描协议可以恢复历史程序的主要性能。当前 v2 baseline 同样40档验证亦已完成：**L100 110.371 QPS**（历史109.301），40档总查询计时317.810秒（历史316.407秒，差0.44%）。独立复核两版合计64,000条查询13字段一致，当前40档额外缓存均为零，源码/二进制构建身份匹配。恢复步骤见[恢复说明](docs/diagnostics/OURS_QPS_STORAGE_RECOVERY_20260919.md)。

证据：[交替复测](results/diagnostics/03_disk_system/layout_state_control/gist/20260919_alternating/report.md)、[直接预读](results/diagnostics/03_disk_system/layout_state_control/gist/20260919_direct_preread/report.md)、[原程序40档恢复复测](results/diagnostics/03_disk_system/layout_state_control/gist/20260919_direct_full/report.md)。

这里的预读是显式诊断条件，执行在查询计时之前，耗时另记；不能称为冷设备缓存结果，也不能把恢复后的 baseline 与未按相同存储协议测量的策略混算加速比。历史和已有论文/实验结果未被替换。
