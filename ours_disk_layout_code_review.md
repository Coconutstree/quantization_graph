# Ours 当前磁盘布局与读取路径：代码审查

本次只审查代码、读取现有索引并做容量统计；没有修改查询程序、重写索引或启动性能测试。范围是当前GIST的BFS locality路径，以及内存实验v2的缓存钩子。普通graph/payload分离路径只用于对照说明。

**最值得先做的三件事：按实际最大度数收紧邻接表槽位；修复共享page缓存命中后不回填单查询缓存的问题；减少读取路径的重复分配和整页复制。** 前两项有直接代码或全量数据证据。收益大小仍需受控复测，不用于解释尚未解决的历史十倍QPS差异。

## 1. 当前实际存储方式

逻辑节点ID不变，`id_to_slot.u32`将ID映射到BFS物理槽位；邻接表内仍存原逻辑ID、保留原边顺序。导出从逻辑节点0开始BFS，未访问的连通部分按ID继续遍历；这与当前原搜索从0开始相符，不应擅自改成index.meta的start_point。

| 数据 | 存储位置 | 每条格式/大小 | GIST情况 |
|---|---|---|---|
| DB1及相关参数 | 内存 | 原1-bit门控编码及factors等 | 原策略不变 |
| ID→slot | 内存 | u32/节点 | 4,000,000 B |
| graph＋compact | graph_compact.pages | 336 B邻接槽＋529 B compact=865 B | 每4 KiB页4节点，250,000页 |
| residual | residual.pages | 648 B/节点 | 每4 KiB页6节点，166,667页 |
| 单查询页缓存 | 每个worker的查询工作区 | 约4 MiB LRU，含管理结构 | 各查询重新初始化，两个文件共享容量 |
| v2共享缓存 | 可选进程内缓存 | 16分片FIFO | 跨查询，按L清空并重新预热 |
| v2热点记录 | 可选进程内缓存 | compact＋residual | 两者一起入选、静态驻留 |

页地址：`page = slot / records_per_page`，`offset = slot % records_per_page × record_bytes`。combined与residual的每页记录数不同，不能共用page编号；代码用file=0和file=2隔离缓存命名空间。

```mermaid
flowchart LR
    A[展开节点u] --> B[读取u的邻接表]
    B --> C[对新邻居做内存DB1筛选]
    C --> D[读取幸存邻居的compact并评分]
    D --> A
    D --> E[搜索结束取最多100个候选]
    E --> F[读取候选compact及residual重排]
```

B与D都从combined页取数据，因此给邻居评分时，邻接表也可能顺带进入查询页缓存。这是当前共置设计的好处，不应把邻接字节全部算作无用读取。residual只在最后重排读取，也有明确的分离依据。

代码：[导出与BFS顺序](scripts/local_runs/export_ours_locality_layout.py:49)、[页定位与读取](experiments/02_disk_shared_graph/native/src/locality.rs:54)、[搜索入口与遍历](experiments/02_disk_shared_graph/native/src/ours_port.rs:886)、[重排](experiments/02_disk_shared_graph/native/src/ours_port.rs:1105)。

## 2. 确定的布局浪费：邻接表按83个邻居预留，实际最多64个

对现有graph页中**全部1,000,000个底库节点**读取degree字段，统计得到：最小度1、最大度64、平均度48.389647，中位数59；90/95/99分位数均为64。头部声明max_degree=83，原导出器直接采用头部上限，所以固定邻接槽位为`4 + 83×4 = 336 B`。没有证据说明83为何形成，不将它归因于辅助节点，也不把头部保守上限当作图损坏。

### 2.1 优先候选：不删边，只把每槽收紧到实际最大度64

| 项目 | 当前 | 按实测最大度重新编码 |
|---|---:|---:|
| 邻接表槽位 | 336 B | 260 B |
| compact | 529 B | 529 B |
| combined记录 | 865 B | 789 B |
| 每页节点数 | 4 | 5 |
| 满页有效记录占比（仍含槽内padding） | 84.47% | 96.31% |
| combined页数 | 250,000 | 200,000 |
| combined文件 | 976.56 MiB | 781.25 MiB |
| combined页尾padding总量 | 159,000,000 B | 30,200,000 B |

**combined文件可少204,800,000 B，即195.31 MiB、20%。** residual不变，两份数据页合计约1627.61 MiB降至1432.29 MiB，约减少12.0%。这是确定的格式容量计算，尚未导出或测量新布局；不等于查询读页或QPS也改善20%。

该方案保留所有边、边序、逻辑ID、BFS槽顺序和向量编码。它优于先上变长图记录：仍可用整除和取余定位，不增加每节点offset表。

**实施时不能只把现有meta中的83改成64。** 原始graph页仍按336 B排列；必须读取旧格式后另建260 B邻接槽的combined文件。当前加载器从源`index.meta`取得graph_bytes，新格式需要独立且有版本的locality描述，并同步reader、热点图缓存及预算计账。源索引和旧二进制需要继续可读。最大度必须每个数据集重新扫描，不能把64写成通用常量。

证据：[原图页导出](experiments/02_disk_shared_graph/native/src/lib.rs:240)、[locality沿用源graph_size](scripts/local_runs/export_ours_locality_layout.py:49)、[当前加载入口](experiments/02_disk_shared_graph/native/src/main.rs:2422)、[全量统计JSON](results/04_ours_memory_budget/v2/layout_degree_audit.json)。复算：`python experiments/04_ours_memory_budget/inspect_layout.py`。

### 2.2 后置候选：变长邻接表或窄整数ID

当前固定邻接槽总量336,000,000 B，实际degree＋有效ID只需197,558,588 B，槽内空白138,441,412 B。收紧到260 B后仍有变长编码空间，但需要offset、分桶或页内目录，不能将这部分直接视为净收益。

本GIST规模可容纳24-bit ID，原边序不变也能压缩ID；但需处理哨兵/辅助ID、SIMD解码、越界检查和更大数据集的回退。先验证简单定长260 B版本，再考虑这些复杂方案。

## 3. 实验版缓存层次存在可修正的缺口

### 3.1 共享page命中没有回填单查询缓存

v2生成的`QueryPageCache::get()`先查C++单查询LRU，未命中则直接返回`memory_experiment::page_get()`。后者从共享分片复制出4 KiB。这个成功返回没有调用本地LRU的put。

因此同一查询下一次访问该页时，仍会本地未命中、获取共享分片锁并复制整页。单查询已经读过它，也不能保证随后不被共享FIFO淘汰后再次产生磁盘读取。

**建议：共享缓存命中后只回填本地缓存，再返回。** 不要误走同时写共享层的通用put而产生重复管理开销。需要验证本地hit、共享hit、真实I/O分开计数；当前上层`query_cache_hits`会把get返回成功的共享命中也包括进去，不能按字段名直接解释为L1命中。

注意，这会改变缓存替换轨迹和读页数，这是预期效果；应保持查询结果和候选数一致，而非要求I/O计数不变。也不能据此断言所有负载都更快，仍需比较复制和LRU维护成本。

证据：[生成缓存钩子](experiments/04_ours_memory_budget/prepare.py:19)、[实际v2 get](work/ours_memory_budget/v2/native/lib.rs:60)、[共享页缓存](experiments/04_ours_memory_budget/memory_experiment.rs:260)。修改应落在prepare.py/实验源文件，不能仅手改生成快照。

### 3.2 同一个冷页没有跨worker的在途读取合并

共享缓存只有已完成页面的get/put，未记录“某worker正在读这个页”。两个worker同时未命中时，都可能提交读取，完成后第二次put才发现页面已经存在。

可尝试为同页增加in-flight状态，后续worker等待并共享结果；必须计入等待、错误传播和管理内存，避免慢请求阻塞所有访问。先记录重叠重复读比例，再决定是否值得做。不能把现有page合并当成已解决此问题：当前合并仅在一次调用内部处理重复/相邻页。

### 3.3 FIFO与16分片是待测取值，不是已证明最优

命中不更新FIFO顺序，热点可能被淘汰；分片各自固定容量，可能冷热不均。先补每分片占用、淘汰、命中及在途重复读指标。只有出现实际问题再试CLOCK/LRU或其他准入策略，避免用更重的锁操作抵消收益。

## 4. 读取与工作区：当前有多层分配和复制

一次未命中大致经过：

1. C++分配对齐缓冲区并读页。
2. C桥把页面复制到Rust预分配的输出Vec。
3. locality reader为每页分配Box，再复制进4 MiB查询缓存。
4. 页内容按节点切成`Vec<Vec<u8>>`，再拼成最终compact/residual批次。

命中查询LRU也不是零拷贝：C++get整页复制到Rust输出，之后还要提取记录。v2静态记录命中仍会拼装结果Vec，只是避免读盘。

**先做低风险优化：** worker复用批次输出、page-ID数组和临时容器；直接把需要的compact/邻接字段写进最终输出，避免复制整条865 B记录后再丢弃另一部分。后续才考虑带生命周期约束的页引用、固定页池或批量gather，防止缓存淘汰造成悬垂引用。

另一个确定的分配点是`clear_query_cache()`每次新建整个4 MiB缓存对象。可以研究保留页槽内存、仅重置索引/LRU状态，在不跨查询保留有效缓存内容的前提下减少分配/初始化。按32个worker算工作区约128 MiB，这不是额外静态缓存；不能用“复用内存”偷偷改变原查询缓存生命周期。

**不要误判锁：** locality读取期间持有cache锁，但该查询缓存通常由worker独占使用，不是32线程共用一把全局锁。不能凭看到Mutex就认定它串行化了整个搜索。全局共享分片锁是另一层，需要单独计时。

也核对了reader生命周期：当前`BackendFactory::create()`复用已有DirectGraphReader并重置缓存，不是每条查询重建AIO上下文；这条不应列为已存在的每查询io_setup/io_destroy问题。

证据：[Rust读取输出](experiments/02_disk_shared_graph/native/src/lib.rs:151)、[C桥复制](experiments/02_disk_shared_graph/native/direct_io_bridge.cpp:99)、[记录拼装](experiments/02_disk_shared_graph/native/src/locality.rs:54)、[LRU整页复制](src/disk_bench/native/query_page_cache.hpp:96)、[缓存重建](experiments/02_disk_shared_graph/native/src/lib.rs:442)、[reader复用](experiments/02_disk_shared_graph/native/src/lib.rs:1227)。

## 5. AIO“用了异步接口”不等于搜索已形成流水线

`DirectAioReader::read_pages()`提交一批请求后，在io_getevents循环等待该批全部完成，再返回给搜索。查询侧beam=1，按“邻接表→DB1筛选→compact→更新候选池”逐轮推进；max_inflight=128只是每批上限，不代表始终有128个有效读取在途。

可优先研究不改变搜索顺序的机会：

- 重排ID已经确定后，将compact与residual缺页一起提交，而不是先等compact完整返回，再开始residual读取。两个文件需要共享提交/完成管理或并行future；不能仅将现有同步封装多调用一次就声称并发。
- 提交缺页后先处理已有的纯内存工作，再等待必需结果。
- 对确定将使用的页做受预算约束的预取，记录无用预取页数。

跨frontier批量展开、更改beam、提前改变候选池更新顺序可能改变搜索行为，应作为另一个算法/并发消融，不能混入“其余参数不变”的布局优化。预取也可能浪费I/O，需保留对照。

证据：[提交及等待](src/disk_bench/native/direct_io.cpp:210)、[逐节点展开](experiments/02_disk_shared_graph/native/src/ours_port.rs:934)、[顺序读取重排数据](experiments/02_disk_shared_graph/native/src/ours_port.rs:1112)。

## 6. 根据驻留策略选择布局，避免用一个格式覆盖全部场景

### 6.1 当前BFS排序有合理性，但不等于页内访问最优

BFS只按图连接关系排列，没有利用真实查询的共同访问关系。一个节点平均约48个邻居，而一页仅装4个combined记录；不能假定一次扩展的邻居大多已经同页。需要统计DB1幸存批次的唯一页数、每读页实际使用的记录数、同页邻接表后续复用率。

下一步可以用验证查询统计共同访问关系进行页分组，但保持逻辑图、边序和距离计算不变；测试查询不得用于学习布局。同样的BFS slot顺序同时用于residual，而重排候选之间的共现未必与图遍历一致，独立residual映射也值得后置研究，代价是另一份映射和格式复杂度。

### 6.2 精细记录全驻留后，combined页带来无用compact读取

full_payload让compact和residual全在内存，但邻接表仍从combined页读取，因此此时磁盘读取携带的compact没有新增用途。

更匹配该驻留策略的候选是：内存精细记录＋BFS顺序的独立graph页。以260 B定长邻接表计算，每页可放15节点，而combined只有4/5节点。**这只是容量对比，不代表实际graph读页会减少3倍以上**，因为同页利用率取决于访问序列。它改变磁盘布局，应与现有full_payload单独对照。

也可先不改布局，只把full_payload未用的约148 MiB附加额度给page缓存；这是更小的实验变量。缓存额度必须按新基线与整个进程峰值重新验收，不能沿用旧RSS差值估算。

### 6.3 热点策略要看省下的I/O而不仅是请求频次

当前热点compact/residual各占50%排名权重，是明确的工程取值；两个类别的请求量、记录大小和实际缺页概率不同。静态合并记录本身合理，但未证明每字节收益最优。建议先在验证集分别统计请求频率、真实缺页和同页复用，再决定是否分别缓存compact/residual，以及如何分配容量。

单独缓存热点邻接表时，同页compact仍可能需要读盘；混合方案还应对照“热点精细记录＋page、不额外缓存邻接表”。现有单轮QPS受存储状态影响，不用它单独证明哪种设计必然更好。

## 7. 建议实施顺序与验收

| 优先级 | 改动 | 改磁盘格式 | 改逻辑搜索 | 主要验收 |
|---|---|---|---|---|
| P0 | 新格式元数据独立、计时拆分、本地/共享命中分开统计 | 仅为新格式作准备 | 否 | 来源与计账明确，避免错误归因 |
| P1 | 共享page命中回填单查询LRU | 否 | 否 | 命中序列测试、结果/候选一致、共享锁和读页变化 |
| P1 | 邻接槽按实测最大度64重编码 | 是 | 否 | 全库边及边序/码字逐条一致，新旧独立加载，页数/文件大小核对 |
| P2 | 复用工作区与LRU存储，减少记录复制 | 否 | 否 | 单查询缓存隔离、超容量批次、峰值内存、分配/复制耗时 |
| P2 | 全库精细记录＋余量page | 否 | 否 | 同预算下的读页、QPS、P99、VmPeak |
| P3 | 同页在途读取合并、重排双文件并发 | 否 | 否（设计目标） | 并发/失败恢复、无重复提交、结果顺序一致 |
| P3 | 独立graph页、变长/窄ID、查询共现页分组 | 是 | 否（设计目标） | 格式校验、训练测试隔离、I/O与CPU权衡 |

每次只引入一个变量；不能同时改L、beam、查询顺序和布局。先做正确性与格式验收，再在同一受监控的存储条件下交错对照。除Recall/QPS/P99外，应分别记录设备等待、缓存查找/锁等待、分配复制、每文件读页、页内有效字节利用率及整个进程VmPeak。

本次发现的是可解释、可测量的优化机会，**不是历史十倍QPS差异已被布局问题解释**。两次原实验仍使用同一旧布局；相关异常继续参考[基线核查文档](ours_memory_budget_baseline_audit.md)。
