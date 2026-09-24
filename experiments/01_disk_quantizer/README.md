# 01 磁盘量化与 I/O

- `run.py`：本层运行入口。
- `native/quantizer_port.cpp`：PQ、SQ、Ours 的驻留/磁盘 payload 对照、距离扫描和结果导出。
- `native/saq_quantizer_port.cpp`：SAQ 磁盘量化端口。
- `native/CMakeLists.txt`：本层端口构建目标。
- `tools/quantizer_kernels.cpp`：量化实现与评估辅助代码。
- `tools/faiss_hard_negative_candidates.cpp`：固定候选生成工具。
- `tools/CMakeLists.txt`：候选工具构建目标。

Ours 算法在 `Ours/core/`，公共 direct I/O 在 `src/disk_bench/native/`。由 `scripts/build_formal_local.sh` 统一构建，不复制算法实现。

```bash
python experiments/01_disk_quantizer/run.py --phase doctor --datasets gist
```

完整阶段和结果规范见根 README。
