# 02 共享图磁盘检索

- `run.py`：本层运行入口。
- `native/src/main.rs`：原生磁盘端口入口与结果导出。
- `native/src/lib.rs`：磁盘 provider、索引导出和读取实现。
- `native/src/ours_port.rs`：Ours 磁盘检索。
- `native/src/baseline_disk.rs`：基线磁盘布局/检索实现。
- `native/src/locality.rs`：局部性布局支持。
- `native/direct_io_bridge.cpp`：Rust/C++ I/O 桥接。

本层 binary 由 `src/graph_core/Cargo.toml` 注册，复用其中的建图与 Ours 算法桥接，03 的 Ours 对比也使用这份实现。第三方算法在 `baselines/diskann/`，公共 I/O 在 `src/disk_bench/native/`。

```bash
cargo build --release --locked --manifest-path src/graph_core/Cargo.toml --bins
python experiments/02_disk_shared_graph/run.py --phase doctor --datasets gist
```

基线共享图与 Ours 自身图的区别必须保留在论文说明中。结果规范见根 README。
