# 当前运行入口与工具

正式实验入口：`experiments/01_disk_quantizer/run.py`、`02_disk_shared_graph/run.py`、`03_disk_system/run.py`。

| 用途 | 当前脚本 |
|---|---|
| 环境与依赖 | `setup_deps.sh`、`setup_cpp_deps_local.sh`、`setup_disk_baselines.py`、`setup_fio_local.sh` |
| 构建和端口登记 | `build_formal_local.sh`、`write_05_ports_local.py` |
| 数据下载/转换/检查 | `download_data.sh`、`data_tools/`、`check_datasets.py` |
| 图索引准备 | `local_runs/build_05_core_graphs.sh` |
| 三层统一运行 | `run_disk_experiments.py` |
| 正式结果绘图 | `plot_disk_experiments.py` |
| 记录运行环境 | `capture_environment.py` |
| 历史路径定位 | `resolve_result_path.py` |

`local_runs/` 除上面点名的建图工具外，主要是特定机器/数据集的诊断、恢复和维护脚本，不是论文复现入口。根目录其余分析、日志解析和作图工具也不会替代正式协议验收。旧实验的运行/绘图/导表脚本已归入 `legacy/scripts/`。

完整流程见 [REPRODUCING.md](../docs/REPRODUCING.md)，论文图表对应见 [FIGURE_PROVENANCE.md](../paper/FIGURE_PROVENANCE.md)。
