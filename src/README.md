# 跨实验共用实现

- `disk_bench/`：调度、输入/结果协议、内存测量、绘图、公共 direct I/O 与跨层测试。
- `graph_core/`：02/03 复用的建图、Ours 算法桥接及 Rust crate。Cargo 目标引用 `experiments/02_disk_shared_graph/native/` 的实际源文件。

各层专属实现直接位于 `experiments/01_disk_quantizer/`、`02_disk_shared_graph/`、`03_disk_system/`。共用代码只保留一份。
