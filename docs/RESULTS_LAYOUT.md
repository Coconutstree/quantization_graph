# 磁盘论文结果组织

结果与源码使用同一组实验名：`01_disk_quantizer`（量化与 I/O）、`02_disk_shared_graph`（共享图检索）、`03_disk_system`（默认预算完整磁盘系统）、`05_memory_budget`（独立内存预算比较）。`04_ours_memory_budget` 保留优化与消融的专用结果。内部 native ID `05a/05b/05c` 保留兼容，03 和 05 复用 `05c`；JSON/CSV 的 `experiment` 字段区分公开实验。

## 新运行

每组结果位于 `results/<experiment>/<dataset>/<run-id>/`：

- `manifest.json`：来源、验收状态和公共运行记录位置。
- `raw/<method>/<phase>/`：原生 JSON、逐查询记录、资源 sidecar 和运行日志。日志属于可核查证据，不与根目录的临时日志混用。
- `manifests/`：验证集调参和参数锁定记录。
- `tables/`：通过既有协议验收的汇总 CSV 及证据清单。
- `figures/`：由同次运行的有效数据生成的论文图表。

`results/manifests/<run-id>/` 保存跨层公共协议、输入哈希、调用及资源环境记录。`--out-root <ROOT>` 将上述结果及公共记录写到自定义根，不再另建 `runs/` 或 `published/` 副本。绘图使用 `python scripts/plot_disk_experiments.py --results-root <ROOT> --run-id <ID>`；也兼容 `--run-root <ROOT>/manifests/<ID>`。

05 每个预算使用独立 run-id，协议保存 `experiment=05_memory_budget`，输出到 `results/05_memory_budget/<dataset>/<run-id>/`。
入口与绘图均使用 `--layers 05`，或通过 `experiments/05_memory_budget/run.py`；不能与 01/02/03 混入同一 run。
05 复用 native 端口和索引，但不复用 03 的测量结果或参数锁。旧协议中仅有 `experiment_group=budget_scan` 的 03 运行仍按原路径读取，不自动改归 05。

图索引在 `artifacts/graphs/`；历史索引在 `artifacts/indexes/`。已有查询划分位于 `artifacts/query_splits/<dataset>/shared/`，新生成划分位于 `artifacts/query_splits/runs/<run-id>/<dataset>/`。这些可复用输入始终属于仓库 artifacts 根，不受结果 `--out-root` 影响。`data/` 未修改。独立建图程序默认临时输出在 `work/graph_build/`。

更改参数、数据、方法、层选择、二进制登记或查询配置时必须更换 run-id。正式结果仍需通过协议、哈希、资源和端口准入校验；目录迁移不改变计量口径、测量次数或通过标准。

## 历史结果与诊断

- 旧三层磁盘结果位于相应层的 `<dataset>/legacy_snapshot_20260918/raw/legacy_snapshot/`。旧输出没有一致的 run 划分，故保留原始快照；标记 `not_revalidated`、`formal_ready=false`。`tables/legacy_snapshot` 和可用的 `figures/legacy_snapshot` 是相对链接，避免复制。
- 旧完整系统 `test_L_400_w_32` 按数据集归入 `03_disk_system/<dataset>/test_L_400_w_32/raw/`，保留原有 pending 状态。公共原始记录在 `manifests/legacy_test_L_400_w_32/source_snapshot/`。
- 诊断、失败、修复历史和临时试验在 `diagnostics/`；旧内存结果及更早的磁盘结果在 `archive/`。这些不会自动进入正式论文图表。
- `archive/legacy_layout_20260918/` 保留旧目录骨架及相对兼容链接，帮助追溯历史材料。历史 JSON/CSV 中的路径字符串没有重写，避免破坏原始哈希。

旧路径映射见 `results/manifests/path_migration_20260918.json`。共享证据读取器支持映射；手工定位可用 `python scripts/resolve_result_path.py <旧路径>`。该兼容层不保证所有旧独立脚本可直接运行，正式复现实验请使用当前三层入口。

迁移检查见 `docs/RESULTS_MIGRATION_20260918.md`。投稿时按需要提供轻量表格、图和清单，大型生成文件默认不进入 Git；归档材料也不等于正式论文结果。
