# 03/05：量化与图联合设计的可信对照

## 2026-09-23 执行更新：重新取得官方源码

已重新下载 `https://github.com/gouyt13/SymphonyQG.git` 并 checkout 到
`6124ddb34ee4d176edea1bd7ad38d1672343df28`。新源码位于
`work/upstream/SymphonyQG-trusted-20260923`，未使用旧副本作为官方源。
下载、工作区状态、GNU C++ 9.5.0、CMake、Ninja、Python 和 CPU 信息记录在
`docs/validation/symphony_upstream_20260923/download_environment.json`。

实际差异审计发现旧副本不只有统计/线程接口修改：`qg.hpp` 改过构图扫描所查询的
visited 集合，`qg_scanner.hpp` 改过 FastScan 的 signed/unsigned 扩展与标量循环界限。
不能将其认定为纯统计补丁。完整 patch 和拒绝报告保留在
`build/disk/symphony_verified_03_20260923`，分类说明在
`docs/validation/symphony_upstream_20260923/local_difference_review.json`。
历史 GIST 1,800 组一致性仅代表当时本地实现的一致性，不是 pristine upstream 证明。

新的 `build/disk/symphony_pristine_03_20260923` 直接包含官方 checkout 的原始头文件。
参考进程串行调用官方 `search`，不再依赖本地 `search_thread_local` 扩展；构图计数
官方未导出，元数据中的旧零值 sentinel 只能解读为 unavailable，不能报告为零计算量。
新构建通过 126 组官方有序结果、192 组缓存遍历和并发淘汰测试；来源报告绑定实际
CMake source root、源码、构建配置及三个二进制的哈希。

新注册表为 `src/disk_bench/ports.trusted03.local.json`。SymphonyQG 通过源码准入，
Starling 保持 pending。该注册表不覆盖历史注册表或二进制。
GIST 新索引构建 run-id 为 `gist_03_pristine_symphony_20260923`，磁盘根为
`work/03_trusted_20260923/disk_root`。这是构建准备，不代表 validation/test 已通过。

`scripts/run_03_trusted_queue.py` 按 GIST、AGNews、DBpedia 及逐方法独立 run-id
执行 export/tune/run，不使用 fixed-beam 或跳过官方参考。默认仅生成计划，`--execute`
才执行；`--prepare-only --execute` 只执行 export，不启动性能调参或 test。
pending 方法记录缺席原因；不生成不完整的四方法汇总或图。
队列计划和逐阶段日志目录为 `results/diagnostics/03_trusted_queue_20260923`。
运行时使用相同 CPU/NUMA、4 GiB、32 workers、完整查询划分；每阶段仍由原始
orchestrator 完成 artifact 验证。恢复时不覆写失败的原始尝试。

io_uring 在沙箱外探测成功；磁盘仍识别为 HDD RAID。主机还存在其他高负载 ANN
任务，正式计时应在无竞争的实验时段执行，不能把当前构图或主机争用当作查询性能。
用户已明确会安排独占机器时段；此前不启动 tune 性能选参或 test 计时。
用户要求优先复用构图。三个数据集的 Ours 和 DiskANN 源图均已存在，继续通过原入口
哈希绑定并复用，不重新构图；GIST SymphonyQG 复用本次新建、来源已证实的索引。
旧 SymphonyQG 构图算法有差异，不能复用为 pristine 版本。Starling 优先复用已有官方
索引，准入状态独立处理。具体路径见
`results/diagnostics/03_trusted_queue_20260923/index_reuse_plan.json`。

当前 GIST 额外核验位于 `results/diagnostics/03_trusted_queue_20260923/gist_mmap_correctness`。
新核验已完成，200 条 validation 查询 × 9 个宽度，共 1,800 组有序 ID 全部一致。
该证据来自本次 pristine 上游构建与新索引，独立于历史本地参考记录；输入、资源侧录和
结果哈希见该目录的 `evidence_manifest.json`。
它只验证官方与适配内存路径的有序 ID，不属于正式 test，也不证明直接磁盘路径的计数。
此前因磁盘争用中断的全索引预读证据保留在 `gist_validation_correctness.reference`，
不可将该未完成步骤当作正式存储准入。

---

以下为首次执行前的协议与历史状态，源码下载阻塞现已解除。

2026-09-23。默认名单为 Ours-Disk、SymphonyQG-DiskPort、DiskANN-PQ-Disk、Starling-Disk。
SymphonyQG 是直接算法对照，图表标为 `SymphonyQG (our disk adaptation)`；仍是
`algorithm_preserving_disk_port`，不是官方原生磁盘实现。AiSAQ 显式可选，PipeANN 本轮不接入。
03 使用 4 GiB、32 workers；05 沿用 `BUDGET_GRID_GIB`（0.5/1/2/4/8 GiB）。

## 源码、构建与导出

固定 SymphonyQG 提交为 `6124ddb34ee4d176edea1bd7ad38d1672343df28`。
`scripts/audit_symphony_source.py` 接收已取得的官方 Git checkout，直接读取固定提交的 Git blobs，
比较全部 symqglib 文件。差异写入 patch，标记 `unreviewed_local_change` 并阻止认证；
不能凭文件名或“统计用途”声明自动放行。只有逐项审计后才能形成受控补丁方案。
工具支持 `--reviewed-patches`：审计记录必须固定上游提交，对每个差异文件记录 upstream_sha256、local_sha256、category（instrumentation 或 thread_api）、rationale 与 reviewer。所有差异必须逐项匹配；未审核修改或算法修改拒绝认证。本次未取得上游源码，因此未编造这些审计记录。

先在新目录配置 CMake，再运行审计工具；不得指向历史 `build/disk/native`：

```bash
cmake -S src/disk_bench/native -B build/disk/symphony_verified -G Ninja -DCMAKE_BUILD_TYPE=Release
python scripts/audit_symphony_source.py \
  --upstream-repo /path/to/pinned/SymphonyQG \
  --build-dir build/disk/symphony_verified
```

源码匹配后，工具重新构建三个 SymphonyQG 目标、运行合成测试，再记录源码、编译配置、构建日志和
二进制哈希到 `symphonyqg.source.json`。正式检查重新读取固定 Git objects，不能单改 verified 标记。
参考与测量二进制放在同一目录；新 registry 应指向该隔离构建。默认 registry 生成器也会验证源码证据，
缺少证据保留 pending，不能因二进制存在就恢复 ready。

导出时将分页后的全部行去除 padding，与官方序列化字节逐一比较；覆盖原向量、邻接、codes、factors、
entry point 和 rotator，并检查 padding、长度及文件尾。成功才写 `export.sha256`。
旧目录没有有效证明时拒绝覆盖，使用新磁盘索引目录重建。此证明不是上游源码证明，两者都必须存在。

## 搜索准入与图表

独立官方内存参考 → 适配内存参考 → 预算内 O_DIRECT 搜索。
官方与适配比较有序 top-10；适配内存／磁盘比较结果及访问、距离计数。
官方未导出遍历计数，不声称验证了官方完整扩展序列。参考进程的时间/RSS 不计入性能。
缺少或跳过官方参考不得进入正式表；`QG05_SKIP_EXTERNAL_PARITY` 不再豁免 formal_ready。

汇总逐项调用完整 artifact 验证。绘图再次验证原始证据，并逐字段比较 CSV 与原始汇总行，
重算 CSV 哈希不能掩盖性能数值变化。run 协议保存实际方法名单，新默认名单不得续写旧 run。
历史文件保持原样，若不满足新要求则仅作为历史证据保留。

## 本次验证与未完成项

隔离构建：`build/disk/symphony_trust_20260923`，未替换历史二进制或重标历史结果。
合成测试通过：126 组官方有序结果、192 组缓存遍历比较、并发淘汰及新增导出篡改拒绝检查。
既有 GIST validation 的 200 查询 × 9 宽度、共 1,800 组有序结果和文件哈希复验一致；
这属于旧契约的验证证据，不能代替新源码审计、导出证明或新 test 测量。

三个数据集的 doctor 均因 SymphonyQG 源码审计及 Starling pending 未通过。
当前设备被探测为 `hdd_raid`，不得标成 NVMe 性能。
固定源码下载此前被自动审批因账户额度限制拒绝；尚未获得可验证的上游 checkout。
DiskANN 原生测试的 io_uring 被沙箱返回 EPERM；Ours 三项实际原生准入回归通过。
因此本轮未启动完整正式 validation/test 或预算扫描，未生成新性能曲线。
每个数据集、方法、预算的状态见 `results/diagnostics/symphony_trust_20260923/status.json`。
