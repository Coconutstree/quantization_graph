# 磁盘实验公共运行框架

本目录只承载共用调度、协议、工具及跨层验证。实验专属 native 程序已经分配到 `experiments/01_disk_quantizer/native/`、`02_disk_shared_graph/native/`、`03_disk_system/native/` 和 `03_disk_system/native_diskann/`。

- `orchestrator.py`：阶段调度、配置冻结与结果发布。
- `layout.py`：三层公开名称及输出布局。
- `native_contract.py`、`protocol.py`：原生结果与正式准入检查。
- `memory_runner.py`：统一RAM预算与实测RSS验收（默认无需cgroup）；另保留显式cgroup测量选项。
- `native/`：公共 direct I/O、query cache 与 C++ 统一构建工程。
- `tests/`：跨层契约、I/O 和集成测试。
- `ports.*.json`：各层原生端口登记。

Python 格式/量化参考代码及兼容入口用于验证与历史维护；论文运行入口以 `experiments/` 和根 README 为准。
