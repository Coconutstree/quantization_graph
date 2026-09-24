# 磁盘实验与内存预算实验

- `01_disk_quantizer/run.py`：量化精度、计算与 I/O。
- `02_disk_shared_graph/run.py`：共享基线图检索；Ours 自身图对照单独标注。
- `03_disk_system/run.py`：完整磁盘 ANN 系统比较。
- `04_ours_memory_budget/`：Ours 优化、缓存和 PCA 消融。
- `05_memory_budget/run.py`：独立的跨方法内存预算比较，0.5/2/4/8 GiB。

01/02/03/05 入口支持 `--phase doctor/export/validate/tune/run/plot`，并固定自己的实验。统一入口可使用 `--layers 01,02,03`；内存预算独立使用 `--layers 05`，不混入同一 run。04 使用自己的优化/消融脚本。05 复用 03 的 native 端口，但保存独立结果和参数锁；内部 `05c` 不等于公开实验编号。跨层共用实现位于 `src/`，旧实验位于 `legacy/`。

完整命令、依赖与准入条件见根 README，结果布局见 `docs/RESULTS_LAYOUT.md`。
