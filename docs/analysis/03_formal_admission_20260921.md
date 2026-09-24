# 03/05 正式准入修复与剩余工作

2026-09-21。Ours 和官方 DiskANN 已接入基于实际证据的搜索结果准入。
这里只完成框架接入和合成数据回归，**没有重跑完整数据集，也没有将历史曲线改成正式结果**。
后续 [共同预算预检](03_common_budget_20260921/report.md) 已将 03 统一改为 4 GiB、32 workers；
05 内存预算扫描独立运行。下方小数据测试的 2 GiB 是其原始测试配置，不随默认值变化重标。

## 已解决的框架问题

此前 native 搜索始终写 `formal_ready=false`，且有三处流程冲突：
整进程 RSS 协议仍被旧分类记账条件拦截；存储顺序预读协议被要求宣称完全冷/热；
直接搜索与完整内存参考同进程，参考数据污染测量 RSS。

现在 Ours/DiskANN 的 validation/test 按以下顺序执行：

1. 完成原有路由资产和 validation 热点准备；test 仍只读取冻结的热点资产。
2. 单独运行原生 direct/memory parity。该准备进程不设搜索预算，不提供性能样本。
   使用同一二进制、索引、查询顺序、width/beam、路由、缓存策略与 CPU/NUMA 配置。
   保存输入文件哈希/状态、实际 parity、逐查询 direct 记录、资源证据及其哈希。
3. 准备进程结束后，重新执行约定的全搜索文件顺序 O_DIRECT 预读，再启动预算内搜索。
   搜索使用 `--parity-mode external`，不会再加载完整内存参考。
   整个搜索进程的加载、预热、查询及退出阶段仍纳入 RSS，不能只截取查询阶段。
4. 核对预算内搜索与准备进程 direct 记录的**有序返回 ID、Recall、访问计数和距离计算次数**。
   这些字段要求完全相同；不是对每个中间访问节点顺序的证明。
   准备进程自己的 direct/memory 比对继续使用原有阈值，并必须覆盖每个配置的全部查询。
5. 另外根据 result IDs 和原 groundtruth 重算 Recall@10，核对每个配置恰好覆盖全部查询一次，
   并核对总 I/O requests、4 KiB sectors、bytes 的逐查询均值与 summary 一致。
6. 外部 RSS、存储准备、独立 parity、原有来源/路由/缓存/trace contract 全部通过，才写 `formal_ready=true`。
   原始 native 结果留在 `*.native.json`，失败候选继续为 false；单改标志位不能通过复验。

证据位于 `*.reference/`、`*.resources.json`、`*.storage_precondition.json` 和逐查询 JSONL。
CSV 增加独立参考协议/路径/哈希及内存验收依据，方便追回原始记录。
保留的 native 搜索结果也绑定哈希；除外部校正的峰值 RSS 外，summary 的 Recall、QPS、I/O 等值必须与原始结果相同。

## 两项协议口径

- 内存通过条件是已采用的 `disk_ram_budget_rss_20260921`：整进程峰值不超过同一计划预算，
  零观测 swap，无额外 OS 地址空间限制。分类账不完整仍如实保留 `memory_accounting_complete=false`；
  `worker_scratch_reservation_bytes` 仍是预留量，不冒充实测分类值。
  旧协议或缺少有效 RSS 证据时，原分类记账门槛仍生效。
- 存储通过条件是已有的 `03_direct_sequential_search_files_v1` 操作证据。
  检查文件范围、完整读取字节数、O_DIRECT、文件状态/哈希与执行顺序。
  仍保留 `storage_cache_protocol=uncontrolled`、`device_cache_controlled=false`，
  不宣称控制器缓存全冷或全热。缓存完全命中时允许物理读为零，但必须和逐查询计数一致。

同时修复 `validate` 别名跳过部分 validation 检查的问题。
这次没有改变原生距离核、PCA 公式、M=32、搜索计时实现或二进制；也没有声称 PCA gate 是安全 hard prune。
独立参考证明存储路径一致性，不能替代官方算法来源审计或相同 Recall 下的性能实验。

## 验证范围

新增真实原生测试使用 8 条合成查询、width 20/40、16 workers：

| 路径 | 数据规模 | 预算 | 检查结果 |
| --- | ---: | ---: | --- |
| Ours 完整 1-bit + hot/dynamic | 512 × 256 | 2 GiB | 准入流程通过 |
| Ours 自动 PCA + hot/dynamic 策略 | 8192 × 256 | 236,504,831 bytes，约 225.55 MiB | 准入流程通过 |
| 官方 DiskANN | 512 × 256 | 2 GiB | 准入流程通过 |

PCA 预算刻意放在完整 1-bit 的规划边界下方，用来验证分支与准入，属于非正式网格诊断。
该分支记录缓存可能因余额很小而为零，不能据此声称 PCA+非零缓存的实际性能。
DiskANN 的 io_uring 在沙箱返回 EPERM，在宿主环境运行同一测试通过。
上述测试还覆盖缺失/篡改证据、修改 result IDs/summary Recall/QPS、参考输入变化时的拒绝。
它们不代表正式硬件 preflight 或完整 validation/test 数据集运行已经完成。

本轮相关检查合计 65 项通过，1 项条件跳过：native contract 11、资源协议 26（另 1 项跳过）、
统一预算 9、热点资产 5、03/05 目录隔离 10、存储预读 1、真实准入集成 3。
真实检查摘要和源码哈希见 [验证 JSON](03_formal_admission_20260921.json)；临时合成索引由测试清理，
该摘要不作为论文性能原始数据。

## 各方法下一步

| 方法 | 当前可做的动作 | 仍需的证据 |
| --- | --- | --- |
| Ours | 通过 03 入口使用新 run-id 重跑 validation → 冻结 tuning → test | 4 GiB、32 workers、完整查询、硬件/存储 preflight、同 Recall 曲线 |
| DiskANN | 和 Ours 在同预算/查询协议下重跑 | 同上；必须用允许 io_uring 的环境 |
| AiSAQ | 独立入口随共同预算改为同一 4 GiB RSS 验收，继续补查询顺序/预热/批次计时和逐查询输出 | result IDs、Recall/I/O、官方路径与参考的一致性；内存检查通过不等于正式准入 |
| Starling | 同样完成官方入口适配，再按数据维度核对页布局约束 | 除上述证据外，解决不适配当前节点页格式的数据集；4 GiB 诊断不能算 2 GiB 正式点 |
| OG-LVQ | 先获得可核验的官方 LVQ 距离实现并独立比较 | decoder/距离与搜索行为的一致性；本地实现内存/磁盘自一致不能代替官方对照 |
| SymphonyQG / Glass | 为现有补充移植入口补独立参考证据并按新协议重跑 | 当前 callable/ready 只说明能调用，不等于结果已正式准入 |

AiSAQ/Starling 的 `pending` 和 OG-LVQ 的 `blocked` 本次保持不变。
后续核查已将两个官方独立入口默认改为 RSS 预算验收，8 项 adapter 回归、9 项预算回归和
3 项 Starling 历史布局回归通过；未重新运行官方全量搜索。
现有 Glass/Symphony 官方对照测试另行重跑，分别 30/30 与 126/126 通过，仍属合成数据结果对照。
Starling 现有 GIST/32 workers 配置的已知分配下界为 2,148,741,824 bytes，已经超过 2 GiB，
且未包括所有运行开销；不能仅通过修改验收元数据放行这个配置。
如果 OG-LVQ 官方实现始终不可核验，应保留为带明确实现说明的补充诊断，不能以官方 baseline 名义进入主表。
可以先选择已接好的 Ours、DiskANN 完成成对正式比较，但不能把它称为所有方法已经完成。

实现入口：[admission.py](../../src/disk_bench/admission.py)、
[orchestrator.py](../../src/disk_bench/orchestrator.py)、
[native_contract.py](../../src/disk_bench/native_contract.py)。
真实原生检查：[test_formal_admission.py](../../src/disk_bench/tests/test_formal_admission.py)。
