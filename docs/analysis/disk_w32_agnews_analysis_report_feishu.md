# AGNews 磁盘环境 32 线程实验分析



数据来源：`results/disk_environment/03_system_fair/agnews/csv/` 的 05C formal 数据。查询对比使用 05C system 层的四个方法：Ours-Disk、OG-LVQ、Glass-NSG、SymphonyQG。其中 SymphonyQG 使用 `fix_w32_diskpayload_symphony_20260831_140957` 修正后的完整 sweep，其余三个方法使用 `formal_diskenv_20260826_114755_bc_agnews` 的 32 线程 sweep。

## 构建

02 shared graph（DiskANN fp32 Vamana，本机重跑）的构建信息记录在 graph meta：总构建时间约 40.32 min，distance evaluations 约 40094397151，graph bytes 约 213.90 MiB。没有保留 progress-stage 分段日志（GIST 的 02build 日志同样是最终汇总格式，不含分段），所以不能拆 train/encode/graph build 三段。

05 层导出阶段的 `build_stats` 只同步到本机一部分；Ours-Disk / OG-LVQ / Glass-NSG 的 export build_stats 仍在另一台机器的 run 里，本机只有汇总 CSV。

| 方法 | 磁盘索引构建/导出 (min) | 构建 peak RSS (GiB) |
|---|---:|---:|
| PQ-DiskANN | 14.87 | 8.15 |
| SQ-DiskANN | 0.36 | 3.75 |
| SAQ-DiskANN | 5.44 | 3.75 |
| SymphonyQG | 27.88 | 26.63 |

说明：SymphonyQG 导出耗时约 27.88 min、peak RSS 约 26.63 GiB，是已有数据里最重的；Ours-Disk 的 AGNews 导出耗时本次缺失，无法与它直接比较。

## 查询

### Ours 查询参数与口径

| 参数 | 值 |
|---|---|
| dataset / base_count / dimension | agnews / 769,382 / 1024 |
| workers | 32 |
| search_dram_budget_gib | 2.0 |
| storage_mode / cache_mode | hybrid_disk / standard |
| kernel | ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search |
| direct_io / native_aio | True / True |
| page_size | 4096 |
| search_width sweep | 10 到 580（40 个点），beam_width=1 |
| ablation | db1+coalescing+reuse |
| query_count | 800 |
| index_size_mb | 1222.66 |
| resident / peak RSS | 0.95 GiB / 0.86 GiB |

### 05C 方法指标对比

| 指标 | Ours-Disk | OG-LVQ | Glass-NSG | SymphonyQG |
|---|---:|---:|---:|---:|
| Recall@10 范围 | 0.9317 - 0.9944 | 0.9190 - 0.9530 | 0.9046 - 0.9450 | 0.8213 - 0.9992 |
| 最高 QPS | 22.13 | 10.92 | 12.78 | 5.18 |
| 平均 latency | 3776.95 ms/query | 14526.01 ms/query | 11530.80 ms/query | 29576.80 ms/query |
| I/O wait 占比 | 99.97% | 99.89% | 99.93% | 99.75% |
| distance compute 占比 | 0.00% | — | — | — |
| I/O requests/query | 930.9 | 3578.7 | 2843.2 | 3708.8 |
| bytes read/query | 4.54 MiB | 13.98 MiB | 11.11 MiB | 57.95 MiB |
| DB1 checks/query | 3362.3 | — | — | — |
| full4 page reads/query | 1039.3 | — | — | — |

注意：OG-LVQ、Glass-NSG、SymphonyQG 没有暴露 DB1 / full4 内部计数，其 distance/queue 字段基本等于总耗时，无法与 Ours 的 distance compute 单独拆分口径对比，所以这些格标为“—”。

### Figure 2：Recall/QPS/I-O 曲线

![Figure 2. AGNews 05C Recall/QPS/I-O 曲线](@./docs/analysis/fig02_w32_recall_qps_io_curves_agnews.png)

图读法：只看 05C system 层。左图是 Recall@10 与 QPS；右图是达到相同 recall 时的 I/O requests/query。

### Figure 3：Ours-Disk 查询时间拆分

![Figure 3. AGNews Ours-Disk 查询时间拆分](@./docs/analysis/fig03_ours_query_time_decomposition_w32_agnews.png)

图读法：只看 Ours-Disk。左图是 total latency 与 I/O wait；中图是非 I/O 的 prep/queue/distance/rerank；右图是每 query 的 DB1/full4/rerank 平均计数。

### 查询差距与不同（重点）

- **Recall 上限**：Ours-Disk 最高，约 0.9944；SymphonyQG 约 0.9992；OG-LVQ 约 0.9530；Glass-NSG 约 0.9450。
- **Latency / QPS**：Ours-Disk 平均 latency 约 3776.95 ms/query、最高 QPS 约 22.13，在四个方法里吞吐最高且延迟最低；SymphonyQG 平均 latency 约 29576.80 ms/query。
- **I/O 成本**：同 recall 下 Ours-Disk 的 I/O requests/query 和 bytes read/query 明显低于其他三个方法。平均来看 Ours-Disk 约 930.9 次/query、4.54 MiB；Glass-NSG 约 2843.2 次/query、11.11 MiB；OG-LVQ 约 3578.7 次/query、13.98 MiB；SymphonyQG 约 3708.8 次/query、57.95 MiB。
- **瓶颈**：四个方法都以 I/O wait 为主（Ours-Disk 99.97%、OG-LVQ 99.89%、Glass-NSG 99.93%、SymphonyQG 99.75%）。Ours-Disk 的 distance compute 只有 0.00%，说明 Ours 不是距离核慢，而是随机读盘等待慢。

## 数据与口径

- 查询内存限制：`search_dram_budget_gib=2.0`；05C 均为 workers=32、repeat=0。
- Ours-Disk 为磁盘模式：`direct_io=True`、`native_aio=True`、`page_size=4096`。

## Source Data

- `results/disk_environment/03_system_fair/agnews/csv/formal_test_rows_formal_diskenv_20260826_114755_bc_agnews.csv`
- `results/disk_environment/03_system_fair/agnews/csv/formal_test_rows.csv`
- `results/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/`
