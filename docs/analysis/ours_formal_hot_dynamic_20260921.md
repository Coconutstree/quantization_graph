# Ours 正式入口接入 hot+dynamic

2026-09-21。03 和独立 05 共用实现，版本为 `05c-ours-pca1bit-hot-dynamic-v4`。
已修改入口、原生搜索、validation/test 资产冻结、资源计账和 CSV 导出，并重新构建/登记二进制。
没有重跑全量 GIST 或其他数据集的性能曲线；下面的合成数据结果仅用于正确性检查。

## 已接入的步骤

1. 先按完整运行预算选择路由：完整 1-bit 能放下就保留；否则取最高可常驻 PCA 前缀，M=32，无 residual norm。
2. 保留已有 BFS 图页缓存，记录缓存使用 `budget − route/工作区/查询/进程预留 − BFS cache`。
   不把 04 的固定 2 GiB 实验配额复制到新预算中，不为缓存降低 PCA 维度。
3. validation 测量前单独运行一次热点准备。每个 width/beam 对记录访问数归一化后累加；
   只选择正分节点，按分数降序、相同分数按 ID 升序排列。预热访问不参与打分。
4. 静态热点记录优先使用剩余额度。余量交给从 04 迁入的 16 分片 FIFO：命中不刷新队列，
   新节点插入、满分片淘汰；compact/residual 的有效位分别维护，数据在锁内复制后返回。
5. 每个 width/beam 清空动态内容，再执行原预热；计时前只清统计，不清预热后的缓存。
   静态热点在整个运行中保持不变；查询期间缓存查找、复制、插入、淘汰都计时。
6. validation 冻结热点 manifest/排名哈希，tuning lock 保存引用；test 校验预算、路由、图、二进制和资产，
   直接复用热点。test 期间动态缓存正常按访问填充，不训练静态热点。

正式存储是原有 packed payload，compact 和 residual 在同一记录内；读盘已得到两部分时一起缓存。
04 的分离页布局没有复制到正式索引，因此没有为了缓存 residual 新增读取，也没有重建图或重排 ID。
缓存中的 residual 是原始精细重排数据，不是 PCA residual norm。

`standard` 默认启用上述策略；`c0` 和 02 固定消融保留原行为。
余量不足时记录容量为 0，并绕过缓存读写分支；静态热点不足时动态缓存也只分配实际可缓存节点数。

## 内存与证据

静态记录、动态预留缓冲、全库 ID 映射、槽位、有效位、容器/锁及分配余量都在记录缓存额度内。
加载静态排名使用流式读取；用于打分的计数/排序数组只存在于独立准备进程，准备不计入搜索 QPS。
准备记录保存在 `*.hot_profile/`，明确 `preparation_only=true`；不能作为正式吞吐数据。

每档 JSON 记录 `ours_record_cache_stats`；CSV 包含：

- BFS 字节、记录缓存预算与实际预留字节；
- 静态热点节点数、字节和 part 命中次数；
- 动态容量、驻留节点、有效 payload 字节、命中/未命中、插入和淘汰。

part 命中按 compact/residual 两部分分别累计，不应把两者相加误称为“命中节点数”。
`cache_bytes` 现在包含 BFS 和记录缓存合计。缓存账本通过仍不代表 RSS 通过；
外部测量器继续按整个进程峰值 RSS 与零 swap 验收。

## 验证结果

54 项回归检查通过：原生存储/缓存/PCA 20 项、内存协议 9 项、native contract 10 项、
03/05 目录隔离 10 项、热点冻结/预算/CSV 5 项。包括 FIFO 淘汰、部分记录有效位、并发复制、
超配拒绝、缺失统计拒绝、修改热点资产拒绝，以及 test 不启动热点训练。

真实 O_DIRECT 小数据测试使用 8,192 个 256 维向量、4 workers、width 40/80，
validation 与独立 held-out test 各 8 条查询；分别验证原始 1-bit 和强制触发的 PCA 前缀。
启用缓存与对照的返回 ID、Recall、visited、距离计算次数一致，并与内存参考一致。
test 复用 validation manifest；篡改排名文件同时被 Python 准备器与原生加载器拒绝。

完整 1-bit、2 GiB 组的 validation/test 均通过 RSS 准入，测得整进程峰值约 127.63 MiB，零 swap。
静态缓存 1,572 个热点；动态容量 6,620 个节点。独立 test 的两档分别记录 4/37 次动态插入，
compact 与 residual 分别有 39/171 次动态命中。这些计数只描述本次合成测试，不能外推 QPS 收益。

为了在小数据上触发 PCA，另设非正式网格中的约 81.46 MiB 临界预算，选择 64 维。
该组剩余记录额度只有 3,071 bytes，因此记录缓存容量为 0；搜索结果一致性通过，
但整进程 RSS 超预算，外部测量器返回 `budget_exceeded`、`budget_admitted=false`。
这是一条**正确性/拒绝边界诊断**，不是通过预算验收的 PCA 性能点，也不是正式 512 MiB 实验。
PCA 分支有非零记录缓存时的全量性能仍需在 05 中测量。

原始检查摘要及源码/二进制哈希：[验证 JSON](ours_formal_hot_dynamic_20260921.json)。

## 正式验收与 baseline 后续

后续更新：Ours/DiskANN 的独立参考进程和证据验收已接入，并通过真实小数据测试；
见 [正式准入修复](03_formal_admission_20260921.md)。下面描述的是本文件接入 hot+dynamic 时的阻塞状态。

接入入口没有改变已有 `formal_ready` 判定。原有 `memory_accounting_complete=false`、
存储控制状态以及内存参考 parity 同进程等验收问题仍然存在；尤其 parity 会把完整参考 payload
与缓存同时计入峰值。完整正式低预算比较需要独立验收 parity 并完成原准入条件，不能只修改标志位放行。

AiSAQ/Starling 的问题属于适配未完成：应在官方搜索入口补齐相同 RSS 预算、查询顺序/预热与批次计时、
逐查询结果 ID/I/O 证据，再由共享 contract 验收；现有独立 CLI 观测不能直接升级成正式结果。
OG-LVQ 的问题还包含算法来源：当前保留本地标量 decoder，现有内存/磁盘自一致性不能替代官方 SVS
LVQ 距离及搜索轨迹对照。应先完成官方距离核接入/独立比较，再恢复正式性能运行。
这三者的 pending/blocked 状态本次没有被改为 ready；详见
[官方 LVQ 审计](og_lvq_official_parity_audit_20260915.md) 和 [2 GiB 核查](03_memory_budget_audit_20260921.md)。
