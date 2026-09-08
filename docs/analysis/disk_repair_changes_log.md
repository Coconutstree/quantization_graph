# Disk 实验修复与改动记录（2026-08-31 ~ 09-01）

记录本轮 disk_environment 修复/重跑过程中所有代码、脚本、数据改动，供审计与复现。

## 1. 根因与图修复

- **根因**：05B/05C 原生端口契约要求 baseline shared graph 至少 `N + additional_points(1)` 个节点
  （`node_count >= base + additional`）。DiskANN 共享图规范形态为 **N+2**（base 节点 + 虚拟 start 节点 +
  空 sink 节点），`start_point = N`。gist（1000002/1000000）、dbpedia（990002/990000）、历史 agnews meta
  （N=769384, entry_point=769382）均为此形态。
- **错误修复过程**：早期脚本把 agnews 图裁到 `base_count=769382`、`start_point=769381`，导致
  `baseline shared graph must contain at least N+additional nodes` 失败。
- **正确修复**：重建 agnews shared graph（8/31 14:09 常规版、22:53 带距离计数版），恢复 N+2=769384、
  `start_point=769382`；图 sha 与历史一致 `8a047a92b0df737e6758ffbff0cc3ba10ad92fea600b0e2487e53d727dcaf207`。

## 2. 构建期距离计数埋点（代码改动）

| 文件 | 改动 | 输出 |
|---|---|---|
| `baselines/diskann/diskann-quantization/src/algorithms/kmeans/mod.rs` | 新增进程级原子计数 `KMEANS_DISTANCE_COUNT` + `add/take_kmeans_distance_count` | — |
| `.../kmeans/lloyds.rs` | `distances_in_place` 每次调用计 `nrows × centers` | PQ k-means Lloyd 距离 |
| `.../kmeans/plusplus.rs` | kmeans++ 每选一个中心计 `nrows` | PQ k-means++ 初始化距离 |
| `experiments/05_disk_system_fair/native_rust/src/main.rs` | PQ export 读取 kmeans 计数写入 export json；SQ/SAQ 记 0（训练无成对距离，属实） | export json `build_distance_computations` |
| `experiments/02_diskann_fair/src/diskann_runner.rs` | shared graph meta 增加 `graph_build_distance_evaluations=` | `.graph.json` |
| `baselines/symphonyqg/symqglib/qg/qg_builder.hpp` | `QGBuilder` 增加 `distance_count_`（exact_nn 计 N 次 + 建图循环 4 处 `dist_func_` 调用） | `build_distance_count()` |
| `experiments/05_disk_system_fair/native/symphonyqg_disk_port.cpp` | `Meta` 增加 `build_distance_computations`（写/读 meta + export json 字段） | export json `build_distance_computations` |
| `experiments/05_disk_system_fair/ports.local.json` | 更新两个端口的 `binary_sha256`（shared_graph_port=`f5ebd2d2…`，symphonyqg=`a9d54c00…`） | — |

实测计数：
- agnews PQ k-means 训练：`604,672,701,440` = 769382 × 256 × 512 × 6 ✓
- agnews SymphonyQG 建图：`14,048,126,319`
- agnews 02 shared graph 建图：`40,094,397,151`（meta `graph_build_distance_evaluations`）
- SQ / SAQ 训练：0（标量量化只有 min/max；SAQ 为均值/范数闭式解，无成对距离）

## 3. 脚本改动

- `scripts/local_runs/resume_w32_diskpayload_symphony.sh`：
  - 图校验逻辑改为接受 `base+1..base+2` 节点（不再裁到 N）；start_point 越界修正为 N
  - 新增 `SKIP_05B_EXPORT` / `SKIP_05B_VALIDATE` / `SKIP_05C_VALIDATE`（复用 export、跳过单线程 validate）
- `scripts/local_runs/agnews_worker_sweep_then_gist_dbpedia.sh`：总控（等 w32 → 补跑 export 开构建统计 →
  agnews 16/8/4/2/1 sweep 每轮存 `figures_w{W}` → gist/dbpedia ABC）
- `scripts/local_runs/resume_agnews_sweep_w16.sh`：w16 接续版（跳过已完成 05B、05C 重导出守卫）；
  gist/dbpedia ABC 含 Ours-Disk
- `scripts/local_runs/run_gist_dbpedia_abc_ours.sh`：gist/dbpedia ABC（05B 加 `Ours-Disk` hybrid_disk +
  `full4-resident/no-gate`；05C 加 `Ours-Disk`；05A 含 `Ours_RaBitQ_K1`）
- `scripts/local_runs/verify_run_rows.sh`：完整性门（方法集/行数/宽度覆盖/workers/预算 2.0/缓存模式/phase=test/
  无混数据集/05B shared graph sha 唯一/parity 文件/统计列非空非 NaN）；05B/05C export json 必须带
  `build_distance_computations`（排除 `.build_stats.json`）

## 4. 数据修复

- w32 05B 发布行曾被 w16 run 的 republish 覆盖 → 从 `formal_test_median.csv` 恢复并合并（02/agnews 现 400 行）
- workers 字段规范化：`'32.0'` → `'32'`（旧行来自 8/26 运行，浮点格式）
- 02 figures 从 `figures_w32` 复制回标准目录
- 目录整理后 `results/{agnews,gist,dbpedia,02_diskann_fair,03_system_fair}` 被移入
  `results/disk_environment/` → 创建 5 个 symlink 保持旧路径可解析（orchestrator 依赖旧路径）

## 5. 运行状态

- w32 agnews：**完整完成**（03:54 merge + 画图 + `DONE`），发布 CSV/图在 `results/disk_environment/`
- 02 图重建（带计数）：完成 23:39
- 恢复运行（09-01 17:04 启动，PID 2598244）：w16 05C → w8/4/2/1 → gist/dbpedia ABC（含 Ours-Disk）

## 6. 已知数据说明

- Ours-Disk / OG-LVQ / Glass-NSG 的 AGNews 行来自 `formal_diskenv_20260826_114755_bc_agnews`
  （另一台机器 `/home/msy2025/...`），本机只有汇总 CSV 快照
  （`results/disk_environment/03_system_fair/agnews/csv/formal_test_rows_formal_diskenv_20260826_114755_bc_agnews.csv`），
  原始 test JSON / export build_stats 未同步到本机
- gist/dbpedia 的 Ours-Disk 由本次 ABC（w32）重跑并覆盖旧行
- 构建期 `read_bytes=0` 是热缓存所致（机器 1.4TB 页缓存），非统计 bug

## 7. 构建耗时统一口径（2026-09-01）

- **规则**：“构图/构建时间”一律取 **from-scratch 官方 build record**（`*_build.json` 的 `build_time_ms`；
  Ours 额外给出 `graph_build_time_ms` 拆分）。`graph_build_mode=reused_graph` 的记录不计入构图耗时。
- **Ours**：官方从零构建记录 `Ours_OursDiskANN_M64_build.json` = graph 2.34 min / total 2.46 min /
  peak 6.80 GiB。02 raw 中 R64 图的 graph build 6.77 s / encode 17.47 s / total 24.25 s 是
  `reused_graph` 加载+编码耗时，不是构建，不参与对比。
- 其他方法同口径：OG-LVQ 3.59 min / 3.34 GiB、Glass-NSG 1.62 min / 8.38 GiB、
  SymphonyQG 3.07 min / 14.77 GiB（均来自官方 `*_build.json`）；Shared fp32 Vamana 40.32 min
  （graph meta，from-scratch 插桩构建）。
- 已知缺口：M64 官方记录未记录 Lbuild/alpha；磁盘查询图参数（R64/Lbuild400/alpha1.2、
  ExRaBitQ4-symmetric）来自 02 manifest。如需严格绑定，可在 Ours 图上补一次 from-scratch 插桩构建。
