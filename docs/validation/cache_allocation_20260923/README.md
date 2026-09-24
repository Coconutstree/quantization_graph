# 固定表示缓存对照：实现与验证

2026-09-23。新 native 指纹：`05c-ours-cache-allocation-v6`。

- 表示先按 full → PCA512 → PCA256 → PCA128 选择，并计入必要预留；缓存策略不影响维度。
- `--ours-cache-allocation graph_first|records_only`；默认保持 graph_first。
- 对照入口 `scripts/run_ours_cache_allocation.py` 使用同一表示计划、PCA 资产和 validation 逻辑记录访问排名。
- CLI 的 `--ours-frozen-route` 校验并复用冻结账本；`--ours-hot-profile` 验证来源与查询哈希后复用热点排名。
- 分类 I/O 按图、full4 验证阶段记录读取、rerank 阶段记录读取计数；后两者是同一 packed payload，不声称 compact/residual 是独立物理文件。
- `--ours-search-path-dir` 仅用于独立诊断；扩展序列包含无新邻居的节点，且详细日志不进入性能准入。
- 性能监督进程在离线准备后重新启动；原有 native 子进程 wait4/RSS/HWM 最大值计量不变。这样避免离线 NumPy/测试夹具的大内存污染 fork/exec 启动峰值。

## 验证记录

- Rust PCA 单元测试 3 项通过：full 优先、512/256/128 的精确边界和最低预算拒绝、查询状态隔离。
- `test_cache_allocation.py` 4 项通过：冻结账本、共享热点与策略锁、诊断拒绝、256 MiB 准备进程与小/超预算测量隔离。
- `test_ours_records.py` 6 项通过；`test_method_pipeline.py` 3 项通过。
- `test_process_separation.py` 2 项通过；资源协议 28 项（27 通过，1 项 cgroup 环境测试跳过）。
- 原有 native contract 11/11；官方参考 9 项通过。
- 使用独立新二进制的 `test_formal_admission.py` 7 项通过，覆盖 Ours full/PCA、两缓存策略、两宽度、精确路径比较，以及 DiskANN 准入回归。
- 原生合成 PCA 测试采用 32,768 节点、256 维，使 PCA128 加上投影开销后确实小于 full 表示。旧 8,192 节点夹具不满足离散 PCA128 的内存节省条件；没有因此放宽路由规则。
- 测试中曾发现大型离线准备影响 wait4 启动峰值。已用干净监督进程修复；保持真实超预算拒绝，不扣除峰值。

## 正式运行状态

已启动 `results/diagnostics/gist_cache_allocation_20260923/status.json` 中的队列：
GIST 0.5/4 GiB，每档两策略、两轮反序，32 workers、beam4、test800、九宽度。
首轮两组完成后，额外执行不计入性能的路径诊断，再运行第二轮。
失败保留原始证据并停止队列；只有经过原始 artifact 准入且路径对照通过后才输出对照 CSV。

独立二进制：`work/cache_allocation_20260923/qgraph05_shared_graph_port`，
SHA256：`d3a74f222360f4078dc256a71697e930f051dfcc86949f8845fea303b58ba4f3`。
编译环境、源码哈希与运行参数保存于新队列目录。已有 05 结果与旧二进制未覆盖。
本记录不代表完整 GIST 对照已完成；最终状态以新队列与原始 artifact 为准。06 尚未启动。

2026-09-23 用户调整：当前缓存对照只运行一轮，每档预算的 graph_first / records_only 各测一次，共四个性能配置。取消反序第二轮，仍复用已有图并保留独立参考和搜索路径校验；不据单轮结果宣称重复测量稳定性。
