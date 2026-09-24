# Ours / DiskANN 代码与实验交接核验

核验日期：2026-09-22。范围为本地交接文档、03/05 原生搜索路径、构建入口、结果组装和现存产物。本次未修改实现、重建二进制或启动性能实验；没有重新执行完整真实数据准入。以下区分实现缺陷、证据缺口与性能设计限制。

## 1. P1：汇总结果可绕过准入验证

`scripts/assemble_reused_03_table.py:63-82` 直接读取 JSON、flatten，再调用 `validate_layer_completeness()`，没有调用 `validate_artifact()`。`:126` 无条件写 `formal_ready=True`。`src/disk_bench/native_contract.py:707` 的完整性检查只检查实验矩阵，不检查准入、资源/参考/trace 哈希或数值有效性。

实际复现：读取 DBpedia 36 行 CSV，仅在内存中把所有行的 formal_ready 改为 false、separate_reference_sha256 改为 64 个零、qps 改为 -1；同一完整性检查仍通过。没有改写任何实验产物。这证明检查不足，不证明历史测量数值被人为修改。

修复方向：组装前逐个验证源 artifact；复用方法应绑定原始不可变协议和 lock，并检查允许跨 run 复用的字段，而不是绕开完整准入。只有所有证据通过才标记汇总 ready。

## 2. P1：现存汇总表来源断链

检查 `results/03_disk_system/{gist,agnews,dbpedia}/{dataset}_aligned_20260921_03_ram4/tables/formal_test_rows.csv`：均有 36 行，但各表 Ours/DiskANN 的 artifact_path 均不存在。当前二进制与 registry 哈希一致，却与这些表记录的哈希不同：

| 来源 | Ours SHA-256 | DiskANN SHA-256 |
|---|---|---|
| 当前二进制 | d6180bca6bee23390a78fb45b99953c6c98f81e6e6e380c854bbad254c3208ea | 44726347bac54f49bb089a871891888c3c609280aaba649492c3d1bb08fd10f7 |
| GIST/AGNews 标准 run 表 | a4bf1a0a3b2a10c7b76675b69832ae67040f1e208fa13cc44037c3491ebaa1f9 | 5299340324c72359de1d8ac87d42a5f5b7db0288736209082f5f121755fd3fe3 |
| DBpedia 标准 run 表 | 4c1d1eabb8aad6abb9263a87e86e04884d73b8822028f5e2c61f6bb1dfd77991 | 682c11d613c2463831d7dcc3774cca53e1090c63f06067e59aaa7a2a96f755e4 |

旧版本结果本身可以保留，但不能称为当前版本结果，且归档后应更新可验证的来源定位。交接快照和 status 的 completed 不能替代对 artifact 的验收。核验时当前 GIST pair 的 Ours JSON 已 ready，DiskANN JSON 仍为 false；这里只报告文件快照，不由此推断宿主进程是否在运行。

## 3. P1：构建入口没有兑现 CPU 配置对齐声明

`scripts/build_formal_local.sh:44-47` 从调用者 cwd 用 `--manifest-path` 编译，没有切换到各 crate 目录或显式传入 target-cpu。两个 `.cargo/config.toml` 位于 crate 子目录，不能仅靠指定 manifest 保证被加载。

通过 inode 确认当前 release 可执行文件对应的 deps 文件：Ours 为 `1e52041a41ac9005`，DiskANN 为 `9dfac54a9aa82ec7`；两者对应 fingerprint 的 `rustflags` 都为 `[]`。因此本地证据不支持“当前 Rust 二进制按 x86-64-v3 编译”。旧 DiskANN `3b1c1ed613e78b28` fingerprint 才包含该参数。

两者当前 release profile 指纹相同，不能据此声称 Rust 两侧一定使用不同优化级别；问题是声明不成立、构建依赖 cwd。另 `src/graph_core/build.rs:32` 给 Ours C++ 距离内核硬编码 `-march=native`，也未与声明的 v3 目标统一。

修复方向：构建入口显式指定目标，记录实际 Rust/C++ 编译命令、工具链与二进制哈希。修复需产生新测量版本，不在正在使用的路径就地重建。

## 4. P2：Ours 固定重排 100 个候选，限制高召回实验的解释

`experiments/02_disk_shared_graph/native/src/main.rs:1183-1195` 写死 k=10、epsilon=1.9、rerank_candidates=100。`ours_port.rs:1239-1242` 仅取近似距离排序 pool 的前 100 个重排；width 增大到 580 不会增加重排数量。

这是已确认的设计限制，不是已证明的召回平台根因。图可达性、门控与量化排序同样影响召回。建议显式参数化并锁定 rerank 数量，做 100/200/width 独立消融，分别记录召回和 I/O。

Ours gate 的 survivor 在候选入池前就读取 packed compact+residual（`ours_port.rs:1176`、`:735-754`）；其 I/O 结构与 DiskANN 驻留 PQ 导航不同。frontier 邻接虽已批量预取，payload 仍按扩展节点处理。属于性能结构差异，不应仅凭读取次数推断两者延迟应相近，也不应直接合并 gate 批次而改变阈值语义。

## 5. P2：时间字段不能用于同口径分解

Ours `ours_port.rs:855` 的 io_wait_us 包含页去重、缓存查询、分配和记录拼装；`:1245-1260` 的 rerank_us 又包含读取，已计入 io_wait_us 的部分会重复。因此把 io_wait_us、distance_compute_us、rerank_us 简单相加会重复计算。

DiskANN `native_diskann/src/main.rs:851,854` 把官方 cpu_time_us 命名为 distance_compute_us，并将 rerank_us 写为 0。源码已声明 cpu_time_us 包含遍历和全精度重排 CPU 工作；0 不代表没有重排工作。这不会自动否定 wall-clock QPS，但不支持两侧“纯距离/纯 I/O”耗时对照。

修复方向：保留官方字段名称，并提供互斥阶段计时；包含关系明确标记，避免做堆叠求和。

## 6. P2：设备缓存因果结论超出证据

`DiskANN官方化交接.md:18,93,99` 将差异归因于设备缓存，并断言同数据集内比较有效。代码只证明测量前进行了顺序 O_DIRECT 读取；`storage_precondition.py:71-72` 明确 device_cache_controlled=false、cold_storage_claim=false。相同预处理操作不保证不同布局/大小索引获得相同缓存状态。

reference 在 measured 之前运行（`orchestrator.py:609-620`），DiskANN 内部 reference 每档还 `fs::read` 全部索引（`native_diskann/src/main.rs:1216`）。不能据此确定控制器容量或缓存命中因果；同数据集内也仍可能受缓存、次序和并发负载影响。

应把“唯一原因”改为待检验假设。做固定配置 A-B-A/B-A-B 多轮交错，记录设备负载与波动，再解释收益。不同数据集本身难度不同，也不适合直接比较绝对 QPS。

## 7. P2：官方源码核验脚本的信任边界不完整

本次 `python scripts/verify_diskann_baseline.py` 通过：1270 pinned 文件、12 patched 文件、3 个补丁。能证明本地文件符合本地清单和补丁后的哈希。

但 `--write-manifest`（`:65-87`）读取任意 source 工作树文件，并直接把 lock.commit 写进清单，没有验证 source HEAD 等于 pinned commit，也没有检查工作树干净。verify（`:97` 起）也不比较 manifest.source.commit 和 lock.commit。reverse apply --check 只证明补丁可逆应用，不验证逆应用后内容等于 pinned 哈希。

因此当前通过不等于独立证明上游来源；这不是发现源码造假。建议从固定 commit 的 Git blob 生成清单，验证 commit 一致，并在临时树逆应用补丁后比较完整原始清单。

## 已确认没有继续存在的旧问题

- DiskANN 两处搜索器的 traversal I/O cap 都已改为 usize::MAX（main.rs:1177、:1222 附近），旧 128 cap 不能继续作为当前召回平台原因。
- Ours 普通路径确实对选定 frontier 调用 graph.read_nodes(&frontier)，且逐节点处理 gate，符合交接描述。
- DiskANN 的完成队列补丁检查短读、排空完成请求后再返回错误；Ours libaio 错误分支销毁 context 后才释放批次缓冲。未在本轮静态检查中发现正常路径同类旧问题；未进行故障注入验证。

建议先修复结果证据链和可复现构建，再重跑最小准入；高召回与性能优化应另立实验，不混入既有表格。
