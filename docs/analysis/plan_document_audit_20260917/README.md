# 《32 线程磁盘实验方案》核验与 Starling/AiSAQ 补充

核验对象是用户指定的[飞书实验方案](https://my.feishu.cn/docx/BpvBdhAILonRVkxT0Jicnwf4nfH)，不是 AGNews 分析文档。本轮使用飞书 CLI 实际读取 revision 138，在该正文基础上完成 12 处局部修改，逐次回读至 **revision 150**。

结论：**Ours、DiskANN、Starling、AiSAQ 四方法主对比已明确写入方案，并补齐参数登记和支持状态；当前仍不能宣称四方法正式实验已可运行或已通过。** Ours 为 2 GiB，三个 baseline 各用自身配置，32 workers，每个正式配置一次，不要求相同内存。方案允许内存不同；内存差异本身不是配置错误。

## 核验发现与本轮处理

| 项目 | revision 138 的情况 | 本轮处理 |
|---|---|---|
| 主 baseline | 第 7 节已有两个方法名称，但缺逐方法执行配置与支持矩阵 | 在第 7.1—7.4 节补入原生流程、内存规则、配置参数、core 支持状态和接入缺口；开头明确四方法集合 |
| 内存扫描 | 第 7 节仍笼统写“固定 32 worker，1/2/4/8 GiB” | 明确仅为 Ours 补充扫描，baseline 不随之改变预算；线程 scaling 同样保留各自配置 |
| 历史状态 | 第 11 节标题仍称“核验结论与待修复项”，表内有“执行器待接入”与“4 GiB 是预检起点” | 在该节直接标为历史记录，并指向当前第 7/16 节；保留旧证据，不冒充当前要求 |
| 05B 图语义 | Pareto 输出表仍写全部是 Shared graph | 改为 PQ/SQ/SAQ 共用 baseline 图，Ours 固定自有图 |
| 执行说明 | 资源控制步骤仍容易被理解成所有方法强制 cgroup | 改为 Ours 2 GiB cgroup、所有方法实测 RSS，baseline 默认只观察 |
| 核验对象 | 第 15 节 AGNews 历史链接标为“本次读取核验对象” | 改为“历史补充参考”，避免混淆本轮对象 |
| 关联本地说明 | OFFICIAL_DISK_BASELINES.md 仍写 baseline 默认 4 GiB 且必须 cgroup | 同步修正该 CLI 说明和内存规则，与实际代码一致 |

## Starling 与 AiSAQ 的实际状态

本轮实际调用 `official_sources.verify_source` 核对锁定源码，两者均通过。AiSAQ 为 `f0a48e984c685bd498e3c4f88386b47e0e4ab1ac`，含已登记的 sysfs 设备元数据补丁；Starling 为 `17dc3e8a011533a62374445f53963e951b72883a`，没有登记补丁。检查结果在 [source_registry_check.json](source_registry_check.json)。

- **Starling**：完整导航图、分区、重布局、page search 纳入主实验。GIST R48 已有索引与完成的 32 worker / AS=4 GiB 诊断，但文件仍为 `formal_ready=false`、`throughput_comparable=false`。AGNews/DBpedia 当前 FP32/R64 路径分别因节点长 4356/6404 B 超过 4096 B 而 blocked。GIST R64 的 4100 B 也不适用，不能直接套用通用默认值。
- **AiSAQ**：建图与搜索均启用 `--use_aisaq`，登记 inline/rearrange、共享 PQ 缓存与每线程读页缓存。已有 1024×64、16 query 小样本功能检查；当前证据不足以认证三个 core 数据集全量通过。无补丁验证建索引成功、查询退出 255，已有环境记录指向设备访问；不能把兼容补丁版写成无修改原版。
- **正式接入**：`native_contract.py` 和 `ports.local.json` 已包含两个主 baseline，但两项仍为 `pending`。配置表标明脚本起点与已存 GIST 配置，不把起点声称为正式调优结果。未修改注册表状态、源码或二进制。

Ours 与 DiskANN 的两个实际二进制本轮重新计算 SHA-256，仍与注册表不一致；没有自动重登记。此前宿主 2 GiB cgroup 创建失败、缺少 numactl 的证据保存在[内存政策核验](../ours_2g_native_baselines_20260917/README.md)，本轮没有再次尝试配置系统。cgroup 要求只适用于 Ours 的限额运行；NUMA 绑定和各方法正式接口属于当前执行器的独立要求。

## 方案与现有实现的剩余差距

1. 两个 baseline 的原生 CLI 尚未完成正式 artifact、纯搜索计时、查询划分和 CPU/NUMA/预热协议的完整接入；不能仅因命令能执行就画正式曲线。
2. pilot 是方案中的小样本诊断阶段，当前 CLI 没有独立 pilot phase；`QG05_FAST` 不能输出正式聚合。这是执行步骤待衔接，不是恢复多次正式重复的理由。
3. 存储设备、输入全量哈希、目标维度支持和正式调参仍需逐方法验证。本轮核对既有小型证据与代码，没有重新计算大文件哈希、重算全量真值或运行性能实验。

论文依据复核：[DiskANN 原论文](https://harsha-simhadri.org/pubs/DiskANN19.pdf)、[Starling §6.1](https://arxiv.org/html/2401.02116v3)、[AiSAQ §4.2](https://arxiv.org/html/2404.06004v2)。原文对应的 64 GB 单机背景、Starling 默认 2 GB/10 GB segment 和 8 线程、AiSAQ 使用 10 条 query 且不加载 GT 的内存测量，均不能替代本项目 32 workers 的实际测量；2 GiB/Ours 与各 baseline 自身配置是用户选择的项目协议。

## 验证证据

- [原始飞书读取](fetch.json)、[最终飞书读取](plan_after.json)、[逐块写入记录](plan_progress.json)、[最终验证](verification.json)。
- 发布脚本每次写入都使用 revision 条件并回读全文，验证正文与预期一致以及原有资源属性不变；未覆盖整篇文档。
- [源码与既有结果哈希](evidence_sha256.json)冻结本轮检查的 10 个小型文件。该证据不代表重新运行原实验。
- 本轮只改文档，不修改算法或测量代码；没有重新运行上一轮 49 项测试，也没有启动全量实验。
