# GIST 整进程预算分档

独立入口连接最新 routing 分页实现和原 `hot_dynamic` 缓存，不改变原生入口默认策略。复用现有 BFS 索引和 validation 热点，不复制或重建索引。

```bash
python experiments/04_ours_memory_budget/gist_budget_sweep/prepare.py
python experiments/04_ours_memory_budget/gist_budget_sweep/run.py --calibrate
python experiments/04_ours_memory_budget/gist_budget_sweep/run.py --preflight-only
python experiments/04_ours_memory_budget/gist_budget_sweep/run.py --full
python experiments/04_ours_memory_budget/gist_budget_sweep/report.py
# 运行中只读查看当前组、L和最新内存阶段：
python experiments/04_ours_memory_budget/gist_budget_sweep/status.py
```

`--full` 先完成全部 pilot 验收再进入完整实验；`--pilot` 只跑小规模。成功目录按散列校验恢复，失败目录禁止自动覆盖或重试。重建二进制后须归档整套旧结果并重新校准，不能混用旧 lock。

- 源码快照：`work/ours_memory_budget/gist_budget_sweep/native/`；本地 C++/Rust 依赖快照及散列在同级 `source_dependencies/`、`source_dependencies.json`（标准 DiskANN 依赖仍由仓库及 Cargo.lock 提供）。
- 二进制：`work/ours_memory_budget/gist_budget_sweep/ours_gist_budget_sweep`
- 结果：`results/04_ours_memory_budget/gist_budget_sweep/`
- `calibration/`：四种显式模式的 validation 实测和锁定计划。
- `diagnostics/`：256/384/512 MiB 实际硬限制探测及异常归档。
- `preflight/<MiB>/`：模式、容量及拒绝缺口，不含性能点。
- `pilot/`、`full/`：每轮同期 resident reference 及分档运行。
- `validation/`：隔离进程单元测试日志；`performance_provenance.json`：性能执行版本。
- `*_summary.csv`：分轮 Recall/QPS、P95/P99、页数、cache、VmPeak/RSS；`figures/`：PNG/PDF/SVG；`report.md`：预算表和图。

## 自动策略与准入

快照入口提供 `--memory-policy auto|resident|hot_dynamic|factors|paged`、`--memory-cache-bytes auto|BYTES` 和 `--preflight-only 1`。新入口默认 auto；原入口不变。完整路由有余量时沿用静态热点记录优先、剩余给16分片FIFO的 `hot_dynamic`，不复用旧2GiB配额。不足一个静态记录及节点表时只常驻 routing。

auto 在完整 routing 不足时先试 factors 常驻+codes CLOCK；不够则全分页。显式 factors 不足时报错。分页无精细记录 cache，无固定热点 routing 页。全局在途 `min(64, capacity)`，最少1页。

固定成本由四种模式 validation100、L100/300/580、workers32 的 VmPeak 减去可归属的 routing/cache 空间取包络，再加8MiB完整输入输出增量和64MiB安全余量。该包络包含输入、库、映射、线程栈、每查询4MiB页缓存、visited/scratch、加载临时对象和分配器保留；不再叠加旧每线程12MiB、额外256MiB与映射预留。它是实测残差估算，不是精确分项测量，也不是最低可运行预算。

页缓存每槽按4096字节数据+256字节元数据收费；服务预留10MiB，分页评分 scratch 单列，factors按文件头20,000,000字节计费。完整 codes+factors 为148,000,000字节。centroid和映射已在基础包络中。节点排名初始化临时空间随 hot_dynamic 校准计入包络；不是只检查缓存最终驻留值。

256/384/512MiB 另做实际低容量分页探测，避免沿用旧预留直接拒绝：64页、L10、100条validation查询、无预热。其失败单独记录；即使成功，也不等于L580或完整40档已验证。统一准入门槛可能比实测可行点保守，报告必须区分。

## 实验协议

整进程 `RLIMIT_AS` soft=hard；`MALLOC_ARENA_MAX=2` 与原协议相同，记录 wait4 RSS及/proc VmPeak。固定beam1/workers32。每运行前复用O_DIRECT全文件预读协议；每个L先清空可变 routing/动态记录缓存，再执行100条预热并重置测量计数；静态热点记录保留。每个预算/轮次使用新进程，不宣称冷设备缓存。预热使用该组查询顺序的前100条：pilot中与测量100条相同，full中只覆盖测量800条中的前100条，因此pilot吞吐不能直接代表full。

主预算256/384/512/640/768/1024/1536/2048MiB；额外floor(T/MiB)-16和ceil(T/MiB)+16去重。 主档以256/512/1024/2048MiB的倍增骨架覆盖范围，384/640/768/1536MiB补充中间点，观察缓存收益变化；T两侧的追加点专门检查策略切换。主档属于预先设定的实验采样设计，T来自校准后的保守准入估算，不代表已实测的最低运行预算。pilot用原测试顺序前100条，L100/300/580，两轮反转预算顺序。完整实验保持原800条测试查询及40档L，每预算两轮；每轮有同期纯常驻参考。

逐查询校验结果ID、recall、visited、distance、db1 checks/survivors、full4和rerank计数。分页物理I/O按进程汇总与参考差额对账；共享读请求只记一次。常驻不得有routing reader统计。运行失败停止后续批次，不静默切模式、降线程或覆盖失败。峰值超过计划须停止、修正并重新验收。
