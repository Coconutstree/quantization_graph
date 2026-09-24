# AGNews 磁盘实验实际核验（2026-09-17）

> 后续用户决定已覆盖下文的统一预算方案：**Ours 为 2 GiB，baseline 各自配置**。最新代码与验证见 [实施记录](../ours_2g_native_baselines_20260917/README.md)。下文环境、二进制和计时核验保留为当时记录，cgroup 缺失不再阻塞无需外层限额的 baseline 观测。

对象：[AGNews 磁盘环境 32 线程实验分析](https://my.feishu.cn/docx/AhoKdQxEBogmWoxfk6xcxHKjnsg)。用飞书 CLI 从 revision 350 读取，再核对旧 CSV、原始 artifact、当前源代码、实际二进制和宿主环境。

**结论：当前不能启动符合新版协议的正式实验。** 缺少可写的 cgroup 委派和 `numactl`，原生端口与固定二进制版本也未全部验收。本轮补修了遗漏的 Rust 计时问题，并完成有限的实际查询检查；这些不等于 AGNews 的 4 GiB 合规或性能验收。

用户要求保持不变：每个正式配置只运行一次；不做五次取中位数。本轮没有运行全量实验，没有产生新排名。测试中的失败重试用于定位环境/测试输入问题，不是筛选正式性能结果。

## 1. 实际检查结果

| 检查 | 结果 | 证据与边界 |
|---|---|---|
| 沙箱 cgroup 预检 | 阻塞：挂载只读，返回 EROFS | [sandbox_evidence.json](sandbox_evidence.json) |
| 宿主 cgroup 预检 | 阻塞：挂载为 rw，但当前 UID 8017 创建子组返回 EACCES | [host_evidence.json](host_evidence.json)；70 个可见目录中没有当前账号可写的目录 |
| 用户 systemd 委派入口 | 当前会话不可用 | 用户总线环境不可用；未修改系统服务、挂载或其他实验的 cgroup |
| NUMA 工具 | 未发现 `numactl` | 沙箱与宿主均检查；不能宣称 NUMA 内存绑定已通过 |
| 外层执行器、资源证据与配置矩阵 | 14 项通过、1 项跳过 | [resource_matrix_tests.log](resource_matrix_tests.log)；跳过的是需要真实 cgroup 的 OOM 检查 |
| Glass / Symphony 极小原生查询 | 2 项通过 | [native_timing_tests.log](native_timing_tests.log)；256 向量、4 查询，核对字段与 Recall 汇总，不证明原生算法等价 |
| Ours / 共享图 PQ Rust 查询 | 2 项通过 | [rust_native_timing_tests.log](rust_native_timing_tests.log)；512 向量、64 查询、32 workers、打乱顺序、固定 CPU 集合 |
| DiskANN Rust 查询 | 宿主环境通过 | [rust_diskann_host.log](rust_diskann_host.log)；同样 512 向量、64 查询、32 workers；沙箱拒绝 io_uring，不能误归因为算法失败 |
| 两个 Rust release 二进制 | 编译通过 | [Ours/共享图编译](build_ours_rust.log)、[DiskANN 编译](build_diskann_rust.log) |
| 正式内存与性能验收 | 未通过 | 未执行真实 4 GiB 限额、swap=0 和 OOM 集成检查；未运行 AGNews 全量曲线 |

本轮对应检查最终为 **19 项通过、1 项环境跳过**，不包含重复执行的失败尝试。资源测试中既有真实子进程检查，也有合成 sidecar 验证；后者不算实际 cgroup 证据。原生查询使用未限额诊断模式，明确保留 `budget_verified=false`、`formal_ready=false`。

Rust 测试逐条按 `query_id` 从返回的 `result_ids` 重算 Recall，并核对汇总值，避免把查询调度顺序误当真值行号；同时检查真实 RSS、CPU affinity 读回以及实际 I/O。测试不要求某种 QPS 数值，也不将耗时波动作为通过条件。

DiskANN 首次检查暴露旧测试命令未传共享图路径，已修正新的诊断测试；随后沙箱内 io_uring 返回 EPERM，转到宿主通过。对应失败记录保留于 [输入诊断](rust_diskann_retry.log)、[沙箱诊断](rust_diskann_final.log)，没有把失败记为通过。

## 2. 本轮发现并补修的代码遗漏

上一轮修复了 Glass、Symphony、OG-LVQ 的 C++ 计时；Ours/共享图和 DiskANN 的 Rust 入口仍在计时的 worker 循环中计算 Recall。

- `native_rust/src/main.rs`：`search_one` / `search_one_ours` 不再计算 Recall。保存所有搜索结果、确定批次结束时间后，再按 `query_id` 评估 Recall。
- `native_diskann/src/main.rs`：在并行搜索结束后立即保存 `wall_seconds`，之后再计算 Recall。
- 两条入口增加 `timing_semantics=search_wall_excludes_recall_evaluation`。搜索算法、返回结果排序与 QPS 的查询计数没有改变。

本轮精确差异见 [共享图/Ours](native_rust_timing.diff) 与 [DiskANN](native_diskann_timing.diff)。`code_before/` 是修改前的工作区快照，不是干净 Git HEAD。工作区已有修改未回滚。

这次修复只解决批次吞吐包含 Recall 评估的问题。阶段计时能否互斥相加、原生缓存策略是否受控、完整内存归因以及原生等价性，仍须分别核验。`formal_ready` 没有被改为 true。

## 3. 二进制与登记版本不一致

[existing_evidence.json](existing_evidence.json) 保留核验开始时的完整哈希及路径：

| 方法 | ports.local.json 状态 | 当前文件与登记哈希 | 对正式运行的影响 |
|---|---|---|---|
| Ours | ready | 不匹配 | `_execute_port` 会拒绝运行；本轮补修后还需重新验收新文件 |
| DiskANN | ready | 不匹配 | 同上 |
| Symphony | ready | 不匹配 | 不能只凭 ready 标签开跑 |
| Glass | blocked | 不匹配 | 原有准入限制继续有效 |
| OG-LVQ | blocked | 不匹配 | 官方 LVQ 实现/等价性问题继续有效 |
| AiSAQ | pending | 尚未固定正式适配二进制 | 完整正式 artifact、资源与计时接入待完成 |
| Starling | pending | 尚未固定正式适配二进制 | 同上；AGNews 当前固定 FP32 实现的跨页支持问题独立处理 |

没有运行批量重写 registry 的脚本。应在验收通过后固定哈希；直接把当前哈希写进去只会改变版本门槛，不会解决准入问题。

Ours/原生 DiskANN 目前还明确输出 `storage_cache_protocol=uncontrolled` 和 `memory_accounting_complete=false`；新资源 wrapper 不会自动把这些字段变成已验证。单次运行不是阻塞原因。

## 4. 旧实验结论复核

直接来源是两组旧 run：`agnews_05c_rerun_20260901_152210`（Ours/OG/Glass）和 `fix_w32_diskpayload_symphony_20260831_140957`（Symphony）。每方法 40 行，workers=32、repeat_id=0。源路径与 SHA-256 保存在 [existing_evidence.json](existing_evidence.json)。

| 方法 | 最高 QPS | 该点 Recall@10 | 旧 peak_rss_bytes | 旧 cache_bytes |
|---|---:|---:|---:|---:|
| Ours | 202.133917611 | 0.946625 | 818,089,984 | 26,259,456 |
| Glass | 135.547 | 0.908750 | 3,280,996 | 0 |
| OG-LVQ | 56.7189 | 0.924875 | 12,304 | 0 |
| Symphony | 5.18181 | 0.876375 | 12,288 | 0 |

这些数字只用于识别问题，不计算加速比。OG、Glass、Symphony 的旧 peak_rss_bytes 恰为 resident+scratch；它们不能代替真实峰值。旧 artifact 缺少限额执行、完整内存与存储缓存协议证据；预算参数 2.0 不证明实际限制到 2 GiB。Ours 的 resident 字段与全批次/消融范围混用，不能用它从预算中相减得到缓存余量。

两批预检都记录 `hdd_raid`、`rotational=true`。当前目录位于 XFS/LVM 上，这个挂载事实本身也不能证明介质是 NVMe。旧 CPU/NUMA 字段不一致，历史最高 QPS 对应不同 Recall，跨宽度平均数不是匹配 Recall 的比较。

缓存容量不同本身不构成完整系统比较失效：在相同实测总预算内可以使用不同原生缓存。旧实验的问题是没有证明总预算及可比条件，也不能把图、缓存、布局共同带来的差异全归因于 DB1。

4 GiB 是预检起点，不能仅凭这个数字证明负载受磁盘限制。原始 FP32 数据量、压缩磁盘目录大小、真正驻留的工作集是不同量；后续使用实际内存与 I/O 证据解释固定 32 workers 的预算扫描。

## 5. 飞书与后续执行

按 lark-doc 工作流完成 4 处局部修改，飞书 **revision 350 → 354**：删除两处把重复次数当失效原因的旧表述，补写计时修复、实际检查结果与剩余阻塞。没有替换历史图片或旧表数值。逐次回读通过，三张图片的资源属性保持一致，详见 [verification.json](verification.json)。

后续顺序：

1. 在具备可写委派 cgroup、memory controller、`memory.peak` 和 `numactl` 的执行环境验证小进程成功限额、OOM、CPU/NUMA 绑定。当前没有执行提权安装或修改系统服务。
2. 对拟使用的端口完成原生一致性、缓存/计时/内存范围验收，再固定源码、二进制与索引哈希；支持矩阵按数据集分别记录。
3. 用新 run-id 执行用户确定的单次测量。同 Recall 比较，旧记录保持历史诊断用途。

复核命令（真实 cgroup OOM 需要明确的委派路径；Rust DiskANN 检查需允许 io_uring 的宿主环境）：

```bash
python3 experiments/05_disk_system_fair/tests/test_measured_protocol.py ResourceTests MatrixTests
python3 experiments/05_disk_system_fair/tests/test_native_timing.py
python3 experiments/05_disk_system_fair/tests/test_rust_native_timing.py
```
