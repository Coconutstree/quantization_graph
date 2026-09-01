# AGNews 磁盘环境 32 线程实验分析



数据来源：AGNews 05C 四个方法。Ours-Disk、OG-LVQ、Glass-NSG 使用本机新 run `agnews_05c_rerun_20260901_152210` 的 w32 sweep；SymphonyQG 使用 `fix_w32_diskpayload_symphony_20260831_140957` 修正后的完整 sweep。

## 构建

02 graph build 是共享的：DiskANN 家族（PQ-DiskANN / SQ-DiskANN / SAQ-DiskANN）共用同一张 fp32 Vamana shared graph，本机重跑一次，graph meta 记录总构建时间约 40.32 min、distance evaluations 约 40094397151、graph bytes 约 213.90 MiB。Ours 使用独立的 ours_native 图。没有保留 progress-stage 分段日志（GIST 的 02build 日志同样只有最终汇总格式），所以不能拆 train/encode/graph build 三段。

### 构图时间

| 方法 | 构图/构建时间 | 峰值内存 | 来源 |
|---|---:|---:|---|
| Shared fp32 Vamana（PQ/SQ/SAQ 共用） | 40.32 min | — | shared_graph meta |
| Ours（native） | graph 2.34 / total 2.46 min | 6.80 GiB | Ours_OursDiskANN_M64_build.json |
| OG-LVQ | 3.59 min | 3.34 GiB | OG-LVQ_LVQ4_R64_W400_build.json |
| Glass-NSG | 1.62 min | 8.38 GiB | Glass-NSG_R64_L100_build.json |
| SymphonyQG | 3.07 min | 14.77 GiB | SymphonyQG_R64_EF400_t3_build.json |

统一口径：本表“构图/构建时间”一律取 **from-scratch 官方 build record**（`*_build.json` 的 `build_time_ms`；Ours 额外给出 `graph_build_time_ms` 拆分），`graph_build_mode=reused_graph` 的记录不计入构图耗时。

Ours 磁盘查询使用的 native 图在 02 raw 中的拆分为 graph build 6.77 s / encode 17.47 s / total 24.25 s，其 `graph_build_mode=reused_graph`，属于复用图加载 + payload 编码，不是 from-scratch 构建；官方从零构建记录为 `Ours_OursDiskANN_M64_build.json`（graph 2.34 min / total 2.46 min / peak 6.80 GiB）。两者是两套口径（reused 加载 vs from-scratch 构建），不直接比较。M64 官方记录未记录 Lbuild/alpha；磁盘查询图参数（R64/Lbuild400/alpha1.2、ExRaBitQ4-symmetric）来自 02 manifest。如需把构建耗时严格绑定到磁盘查询图本身，可在 Ours 图上补一次 from-scratch 插桩构建。

下面的“磁盘索引导出”是 02 构图完成后，把 payload 编码并写盘的时间。PQ / SQ / SAQ 虽然共用同一张图，但 payload 量化方式不同，所以导出耗时不同（PQ 训 codebook、SAQ 球面变换、SQ 纯标量量化）。

05 层导出阶段的 `build_stats`：Ours-Disk、OG-LVQ、Glass-NSG 均来自本机新 run。

| 方法 | 磁盘索引导出（payload 编码 + 写盘）(min) | 导出 peak RSS (GiB) |
|---|---:|---:|
| PQ-DiskANN | 14.87 | 8.15 |
| SQ-DiskANN | 0.36 | 3.75 |
| SAQ-DiskANN | 5.44 | 3.75 |
| SymphonyQG | 27.88 | 26.63 |
| Ours-Disk | 6.67 | 3.89 |
| OG-LVQ | 0.07 | 3.36 |
| Glass-NSG | 0.62 | 5.71 |

说明：PQ/SQ/SAQ 的导出时间差异来自 payload 编码，不是构图（三者共用同一张 shared graph）。SymphonyQG 是第三方系统，其导出最重；Ours-Disk 已由本机新 run 补齐。

### Ours 索引大小（4bit / 8bit）

| 组成 | 大小 |
|---|---:|
| 4-bit payload | 407 MB |
| 8-bit payload（4-bit + residual） | 906 MB |
| adjacency | 263 MB |
| fp32 base（单独存放） | 0 |
| 4-bit 索引（4-bit payload + adjacency） | 638.6 MiB |
| 8-bit 索引（8-bit payload + adjacency） | 1114.1 MiB |

## 查询

### Ours 查询参数与口径

| 项 | 值 |
|---|---|
| 数据集 | agnews（769k × 1024） |
| 存储 | 4-bit payload 407 MB + 8-bit payload（4bit+residual）906 MB + adjacency 263 MB；fp32 base 单独存放 |
| 查询 | 32 workers、2 GiB budget、hybrid_disk、direct I/O + native AIO、page 4096 |
| sweep | width 10–580（40 点）、beam=1、ablation=db1+coalescing+reuse |
| parity | passed |

### 05C 方法指标对比

| 指标 | Ours-Disk | OG-LVQ | Glass-NSG | SymphonyQG |
|---|---:|---:|---:|---:|
| Recall@10 范围 | 0.9317 - 0.9944 | 0.9189 - 0.9530 | 0.9038 - 0.9449 | 0.8213 - 0.9992 |
| 最高 QPS | 202.13 | 56.72 | 135.55 | 5.18 |
| 平均 latency | 586.72 ms/query | 2609.75 ms/query | 686.04 ms/query | 29576.80 ms/query |
| I/O wait 占比 | 99.43% | 97.68% | 94.69% | 99.75% |
| distance compute 占比 | 0.08% | — | — | — |
| I/O requests/query | 931.4 | 3578.8 | 2846.8 | 3708.8 |
| bytes read/query | 3.64 MiB | 13.98 MiB | 11.12 MiB | 57.95 MiB |
| DB1 checks/query | 3362.3 | — | — | — |
| full4 page reads/query | 809.7 | — | — | — |

注意：OG-LVQ、Glass-NSG、SymphonyQG 没有暴露 DB1 / full4 内部计数，其 distance/queue 字段基本等于总耗时，无法与 Ours 的 distance compute 单独拆分口径对比，所以这些格标为“—”。

### Figure 2：05B shared graph Recall-QPS（02 层）

![Figure 2. AGNews 05B Recall-QPS](@./results/disk_environment/02_diskann_fair/agnews/figures_w32/disk05b_shared_graph_recall_qps.png)

图读法：02 层共享图方法（PQ-DiskANN / SQ-DiskANN / SAQ-DiskANN / Ours-Disk）的 Recall@10–QPS。

### Figure 3：05C system Recall-QPS（03 层）

![Figure 3. AGNews 05C Recall-QPS](@./docs/analysis/fig05c_recall_qps_agnews_paper.png)

图读法：03 层完整磁盘系统（Ours / SymphonyQG / OG-LVQ / Glass-NSG）的 Recall@10–QPS，使用本机最新数据（log-y）。

### Figure 4：Ours-Disk 查询时间拆分

![Figure 4. AGNews Ours-Disk 查询时间拆分](@./docs/analysis/fig03_ours_query_time_decomposition_w32_agnews_paper.png)

图读法：只看 Ours-Disk。左图是 total latency 与 I/O wait；中图是非 I/O 的 prep/queue/distance/rerank；右图是每 query 的 DB1/full4/rerank 平均计数。

### 查询差距与不同（重点）

- **Recall 上限**：Ours-Disk 最高，约 0.9944；SymphonyQG 约 0.9992；OG-LVQ 约 0.9530；Glass-NSG 约 0.9449。
- **Latency / QPS**：Ours-Disk 平均 latency 约 586.72 ms/query、最高 QPS 约 202.13，在四个方法里吞吐最高且延迟最低；SymphonyQG 平均 latency 约 29576.80 ms/query。
- **I/O 成本**：同 recall 下 Ours-Disk 的 I/O requests/query 和 bytes read/query 明显低于其他三个方法。平均来看 Ours-Disk 约 931.4 次/query、3.64 MiB；Glass-NSG 约 2846.8 次/query、11.12 MiB；OG-LVQ 约 3578.8 次/query、13.98 MiB；SymphonyQG 约 3708.8 次/query、57.95 MiB。
- **瓶颈**：四个方法都以 I/O wait 为主（Ours-Disk 99.43%、OG-LVQ 97.68%、Glass-NSG 94.69%、SymphonyQG 99.75%）。Ours-Disk 的 distance compute 只有 0.08%，说明 Ours 不是距离核慢，而是读盘等待慢。
- **Ours 为什么快**：Ours-Disk 的 DB1 1bit 初筛把 full4 候选压到约 811.1 个/query，只读 4bit 页面（3.64 MiB/query），且页面访问已顺序化（约 200–350 MB/s），所以吞吐远高于以随机读为主的 OG-LVQ / Glass-NSG / SymphonyQG。同时 Ours 索引只有 1.36 GB（SymphonyQG 约 12.6 GB），磁盘 footprint 也更小。

### Ours 进一步优化

按收益排序：

1. 把 2 GiB cache 真正用起来（当前 `cache_bytes` 仅约 25 MiB）。
2. 压低 full4 读：收紧 DB1 门控；同页候选合并读；4bit 与 residual 尽量同页。
3. 继续提升页面顺序性、prefetch 和 AIO depth。
4. 按目标 recall 选最小 width，避免无谓读盘。
5. 尝试热页面 / 分层缓存，把高频 payload 页留在 DRAM。

## 数据与口径

- 查询内存限制：`search_dram_budget_gib=2.0`；05C 均为 workers=32、repeat=0。
- Ours-Disk 为磁盘模式：`direct_io=True`、`native_aio=True`、`page_size=4096`。

## Source Data

- `results/disk_environment/03_system_fair/agnews/csv/formal_test_rows_formal_diskenv_20260826_114755_bc_agnews.csv`
- `results/disk_environment/03_system_fair/agnews/csv/formal_test_rows.csv`
- `results/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/`
