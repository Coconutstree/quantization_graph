# 论文图表与实验证据对照

本表以当前 `paper/sigmod_draft` 为准。图号来自其图表说明与正文插入顺序，不把文件名中的 fig01 直接当成正文图 1。机器可读版本见 `figure_provenance.csv`。

| 正文/补充编号 | 内容 | 实验 | run-id | 源数据 | 命令 |
|---|---|---|---|---|---|
| 图 1 | 方法示意图 | 无测量 | 不适用 | `sigmod_draft/overview.tex` | C0 |
| 2 | fig01_throughput.pdf | 03 | `test_L_400_w_32` | `source_data/target_recall.csv`，完整路径见 CSV | C1 |
| 3 | fig02_read_volume.pdf | 03 | `test_L_400_w_32` | `source_data/target_recall.csv`，完整路径见 CSV | C1 |
| 4 | fig03_tail_latency.pdf | 03 | `test_L_400_w_32` | `source_data/target_recall.csv`，完整路径见 CSV | C1 |
| 5 | fig04_screening_io.pdf | 03 | `test_L_400_w_32` | `source_data/screening_io.csv`，完整路径见 CSV | C1 |
| 补充（未编号） | fig05_full_sweep.pdf | 03 | `test_L_400_w_32` | `source_data/all_measurements.csv`，完整路径见 CSV | C1 |
| 补充（未编号） | fig06_requests.pdf | 03 | `test_L_400_w_32` | `source_data/all_measurements.csv`，完整路径见 CSV | C1 |

C0：`python paper/sigmod_draft/build.py`。C1：`python paper/sigmod_draft/figures/disk_w32/plot_figures.py`，随后可用 C0 编译论文。论文编译还需要其 build.py 所用的 LaTeX 环境，见草稿 README。

上述 source_data 相对于 `paper/sigmod_draft/figures/disk_w32/`。**权威输入是 `source_data/snapshot/<dataset>/<method>/result.json`，CSV 是绘图程序生成的派生表**，并非从当前可变的 results 目录直接读取。`source_data/snapshot_manifest.json` 保存来源和 SHA-256。本次仅核验快照，没有重新生成或修改图、CSV 和快照。

冻结批次来自 `test_L_400_w_32`，是包含修复历史的批次名称，不能理解为所有点来自完全相同的原生调用。逐方法的调用和参数以快照为准。现有证据覆盖 AGNews/GIST 各五系统、DBpedia 两系统；仍待正式验收和完整内存账本。图 5 是筛选计数展示，不等于独立的因果消融。

## 下一批正式图

| 论文位置 | 实验入口 | 绘图输出主文件 | run-id / 数据 |
|---|---|---|---|
| E3：量化精度、存储与 I/O，图号待定 | `experiments/01_disk_quantizer/run.py` | `disk05a_quantizer_fair_summary.pdf` | 尚未运行；01 的 `<dataset>/<run-id>/tables/formal_test_rows.csv` |
| E4：共享图比较，图号待定 | `experiments/02_disk_shared_graph/run.py` | `disk05b_shared_graph_recall_qps.pdf` | 尚未运行；02 的同结构 CSV；额外消融仍需对应配置 |
| E1：完整系统，图号待定 | `experiments/03_disk_system/run.py` | `disk05c_system_recall_qps.pdf` | 尚未运行；03 的同结构 CSV；AiSAQ/Starling pending |
| E2/E5：构建与内存敏感性 | 尚待确定具体配置 | 尚无已验收论文图 | 不预填性能值、run-id 或图号 |

测量阶段命令见 `docs/REPRODUCING.md`，逐层绘图命令见 CSV。以上新图的实际路径均从 `results/<experiment>/` 开始。完成运行后应将 `<dataset>/<run-id>` 替换为真实值，并保留协议、源证据哈希和环境记录。文件名里的 05a/05b/05c 是兼容标识，公开论文层号为 01/02/03。

## 旧 paper/figures

`paper/figures/` 与当前草稿的 `sigmod_draft/figures/disk_w32/` 不是同一套图。旧图保留原文件，标为历史图，不自动沿用为当前正文结果。

- `fig01`–`fig08`、`fig10`：旧绘图程序 `legacy/scripts/plot_vldb2027_figures.py` 有相应输出定义；使用旧 01/02/03 路径。这只能证明程序与文件名关联，不能证明现有图片的确切 run-id。
- 旧查询编码图的生成程序（当前 `paper/figures/` 未见 fig09 文件）：参见 `legacy/scripts/plot_query_coarse_codec.py`；属于旧查询编码实验。
- 其他文件和上述旧图的精确运行来源：未核定；不猜测为当前磁盘结果。

旧图片完整清单在 `legacy_figure_inventory.csv`，run-id 统一标记 `unknown`。其原始图像不因登记而变为已验证证据。
