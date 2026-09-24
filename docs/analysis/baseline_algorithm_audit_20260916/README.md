# 磁盘 baseline 算法一致性核验（2026-09-16）

范围：05C 的 Glass-NSG、OG-LVQ、SymphonyQG、DiskANN-PQ。Ours 不在本次修改范围。
要求：图构建、量化、距离计算、候选队列、去重、终止、结果处理和参数语义沿用原方法；磁盘分页、读取和缓存属于存储适配。

## 结论与修正

| 方法 | 本次结论 | 已采取的操作 |
|---|---|---|
| Glass-NSG | 原磁盘端按 (距离, ID) 排序，违背官方并列距离规则 | 删除本地候选队列，直接使用 `glass::LinearPool<int32_t>`，按官方 Search 初始化、标记 visited、扩展与插入 |
| OG-LVQ | 官方建图与保存格式，官方 SearchBuffer，但距离仍为本地 scalar decoder；不能认定算法原样复现 | 注册表设 blocked，纠正 source_kernel/port_kind；生成脚本保留 blocked，禁止自动恢复 ready |
| SymphonyQG | 所检查循环与本地固定版本参考实现一致；新增官方真实建图编码测试通过 | 保留搜索算法，扩展独立官方入口对照测试 |
| DiskANN-PQ | 实际调用 `DiskIndexSearcher::search(..., SearchMode::graph(), ...)` | 保留官方搜索；已有 reader 完成队列/短读修复属于存储层，不回滚 |

**尚不能宣称四个 baseline 全部完成官方等价验证。** OG-LVQ 仍缺可用于磁盘行的官方 LVQ 距离实现；Glass/Symphony 测试也不等于全量真实数据集、距离与扩展序列的完整一致性证明。

## Glass 参考入口与测试

`experiments/03_system_fair/adapters/glass_nsg_adapter.py` 默认不传 `--batch`，worker 因而调用单查询 `search`，对应 `GraphSearcher::Search`。窗口与容量均为 `max(k, ef)`。可选 `SearchBatch` 的窗口为 ef，ef < k 时语义不同；最终磁盘实现与测试均采用单查询入口。

官方 LinearPool 在满容量时拒绝距离 >= 当前最差值的候选；未满时将新候选放在已有相同距离之后，保留官方 cursor reopening 与 visited bitset。

测试用同一图和官方 SQ4U 编码，经真实 O_DIRECT 读取磁盘页，比较官方 Search 与磁盘搜索的有序 top-10。64 节点、64 维、宽度 1/10/20/64/580、3 个查询，分别使用并列与固定种子随机数据：30/30 一致，见 glass-audit.log。此前并列数据的 15 组均不一致。仅编译 /tmp 审计二进制，历史性能二进制未替换。

## SymphonyQG 参考与测试

核对 `qg/qg.hpp` 的 search_thread_local、scan_neighbors、update_results，磁盘端复用官方 QGQuery、QGScanner、SearchBuffer、ResultBuffer 与 HashBasedBooleanSet。扩展节点时读取完整官方行；fallback 在复用读取缓冲区前复制邻居 ID，保持遍历顺序。CPU prefetch 不转换为额外强制候选行读取。

测试共 126 组有序 top-10 对照：原有并列/断连/fallback 78 组；新增官方 QGBuilder 构建的 128 节点、64 维、degree=32 图，直接复制序列化行和 rotator，6 个宽度 × 8 个查询共 48 组，覆盖实际生成的非零 codes/factors。全部通过，见 symphony-audit.log。这是合成数据上的算法对照，不是 AGNews/GIST/DBpedia 全量验证，也未新增独立扩展序列记录。

## OG-LVQ 的具体缺口

本地 `lvq_distance` 自行做 Turbo4 解码、浮点重构与累加；官方 `EuclideanBiased::fix_argument` 先做 query-centroid，其计算顺序与本地 query-(decoded+centroid) 不同，不能以数学表达式相同推断浮点结果和候选排序一致。现有 SearchBuffer 修正保留，但不构成距离内核等价证据。

已检查 packaged `svs/quantization/lvq/lvq.h`：关键模板仅声明；`libsvs_x86_objects.a`、`_svs...so`、`libsvs_mkl.so` 中未找到可直接链接的相关 EuclideanBiased/ScaledBiasedVectorLayout 导出符号。官方也说明公开源码不包含专有 LVQ/LeanVec：
https://github.com/intel/ScalableVectorSearch

完成该项需要与当前保存格式匹配、包含 LVQ 实现的官方 SDK/源码或可调用的官方距离接口。没有将本地 decoder 重新包装成“官方内核”，没有换成其他算法，也没有以整个内存索引搜索冒充磁盘移植。标量实现暂留作诊断，正式注册表拒绝运行。

## 来源与证据边界

算法逐段比较和编译参考为 baselines/DEPENDENCY_LOCK.json 指定的仓库内源码：Glass d2296ec4、Symphony 6124ddb3、SVS 078846e3、DiskANN 3218478b。源码哈希见 source_hashes.json。该源码树已有统计和线程接口等本地修改，本次没有将整个 vendor 目录认证为 pristine upstream。尝试重新获取固定 Glass 版本失败（网络限制/网页 cache miss），因此没有声称完成远端逐字节比对。

DiskANN 本次只完成搜索调用链及已有 Linux reader diff 的静态检查；没有重跑 DiskANN 集成测试。缓存适配的批内重复读和跨方法计时口径仍沿用 2026-09-15 审计中的未解决项，它们不自动等同于搜索算法改变。

## 验证与运行状态

- Glass C++ 参考对照：30/30。
- Symphony C++ 参考对照：126/126。
- `python experiments/05_disk_system_fair/tests/test_native_contract.py`：10/10。
- 当前注册表拒绝 blocked OG-LVQ 与旧 Glass 二进制：通过。
- `git diff --check`：通过。
- 两个 C++ 测试已加入 native/CMakeLists.txt 的 CTest 注册；本次实际采用日志对应的 g++-11 直接编译执行，未运行全工程 CMake 构建。

未启动新的性能跑批、未修改历史结果、未重打历史二进制 hash。Glass 当前配置阻止旧二进制继续运行；生成脚本中的新源码版本 fingerprint 为 official-linear-pool-search-v3，必须在实际重建与验证后生成配置。旧宽度检查点不得与修正后的结果混用。原有性能队列阻断文件未解除。
