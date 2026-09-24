# 三层磁盘实验迁移说明

2026-09-21 后续更新：公开实验 05 已独立为 `experiments/05_memory_budget/`，用于跨方法内存预算比较；
04 为 Ours 优化与消融。05 复用内部 `05c` 端口和已导出的索引，结果独立位于 `results/05_memory_budget/`。
以下三层说明记录原迁移，不再限制新增的公开实验编号。

迁移日期：2026-09-18。

| 旧源码位置 | 新位置 |
|---|---|
| `experiments/01_quantizer_fair/` | 旧框架归档到 `legacy/01_quantizer_fair/`；磁盘共用候选生成器和量化实现提取到 `experiments/01_disk_quantizer/tools/` |
| `experiments/02_diskann_fair/` | `src/graph_core/`，保留 Cargo 构建、Ours bridge 和建图能力 |
| `experiments/03_system_fair/` | `legacy/03_system_fair/`；磁盘端口需要的 SVS worker 提取到 `experiments/03_disk_system/adapters/` |
| `experiments/04_query_codec_1bit_scan/` | `legacy/exploratory/04_query_codec_1bit_scan/`，保留原始文件 |
| `experiments/05_disk_system_fair/` | `src/disk_bench/`，对外分成 `experiments/01_disk_quantizer/`、`02_disk_shared_graph/`、`03_disk_system/` |
| `experiments/06_adaptive_disk_ann/` | `legacy/06_adaptive_disk_ann/`，探索代码归档，相关已集成核心实现不删除 |

现有脚本中的源码和 Rust 二进制路径已同步迁移。历史文档、结果和论文快照中的旧路径保留为当时的记录，阅读时按此表定位源码。C++ 使用新的 `build/disk/` 构建树，以免复用含旧源码路径的 CMake 缓存。

公开入口仅提供三层磁盘实验。底层 `05a/05b/05c`、`qgraph05_*` 名称和历史索引路径保持兼容，不修改算法或既有测量证据。`src/graph_core` 还保留被建图/一致性验证复用的驻留代码，它不构成独立的内存论文实验。

正式构建不依赖 `legacy/`。历史维护脚本仍可能使用归档代码；当前用户入口以仓库 README 为准。原 04 源目录属于其他用户，因此通过移动其父目录完整归档，未修改其文件内容；该归档中的旧相对构建路径不作为受维护接口。

## 实验实现按层归属（后续调整）

三层现在同时包含入口与本层实现。01 的量化端口/工具在其 `native/`、`tools/`；02 Rust 搜索和 I/O bridge 在其 `native/`；03 系统端口在其 `native/`、`native_diskann/`，官方 CLI 和 SVS worker 在其 `adapters/`。公共调度和 I/O 留在 `src/disk_bench/`，建图算法与共享 Cargo 工程留在 `src/graph_core/`，不复制算法。

旧 `Ours/experiments/` → `legacy/ours_experiments/`；旧 Python runner 测试 → `legacy/ours_tests/`。C++ 新输出目录为 `build/disk/native/`，候选工具为 `build/disk/01_disk_quantizer/`。结果随后按三层迁移，历史文件保持原始内容，详见 `docs/RESULTS_LAYOUT.md` 和 `docs/RESULTS_MIGRATION_20260918.md`。
