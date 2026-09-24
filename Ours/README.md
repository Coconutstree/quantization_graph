# Ours 算法核心

这里保存跨实验复用的 ExRaBitQ 量化、距离计算和 Vamana 实现。

- `core/hnswlib/`：算法 C++ 头文件。
- `tests/`：查询 codec 的 C++ 正确性测试。
- `NOTICE.md`、`CORE_SOURCES.md`：许可与源码位置说明。
- `logs/`、`config.json`：历史 resident 入口遗留材料，不是新的磁盘实验结果或默认参数。

旧 `Ours/experiments/` 已整体迁入 `legacy/ours_experiments/`，旧 Python runner 测试迁入 `legacy/ours_tests/`。

当前磁盘实验从 `experiments/01_disk_quantizer/`、`02_disk_shared_graph/`、`03_disk_system/` 运行，完整命令见根 README。Rust 建图桥接在 `src/graph_core/`，Ours 磁盘检索实现在 `experiments/02_disk_shared_graph/native/src/ours_port.rs`，03 复用同一份代码。
