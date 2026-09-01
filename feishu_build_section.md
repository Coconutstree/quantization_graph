### 构建成本（总 = 构图 + 磁盘索引导出）

| 方法 | 构图耗时 (min) | 磁盘索引导出 (min) | 总构建成本 (min) | 构图 peak RSS (GiB) | 导出 peak RSS (GiB) | 来源 |
|---|---:|---:|---:|---:|---:|---|
| Shared fp32 Vamana（PQ/SQ/SAQ 共用） | 40.32 | — | 40.32（共享一次） | — | — | shared_graph meta |
| PQ-DiskANN | 40.32（共享） | 14.87 | 55.19 | — | 8.15 | shared meta + export build_stats |
| SQ-DiskANN | 40.32（共享） | 0.36 | 40.68 | — | 3.75 | shared meta + export build_stats |
| SAQ-DiskANN | 40.32（共享） | 5.44 | 45.77 | — | 3.75 | shared meta + export build_stats |
| Ours（native） | 2.46 | 6.67 | 9.13 | 6.80 | 3.89 | Ours_OursDiskANN_M64_build.json + export build_stats |
| OG-LVQ | 3.59 | 0.07 | 3.66 | 3.34 | 3.36 | OG-LVQ_LVQ4_R64_W400_build.json + export build_stats |
| Glass-NSG | 1.62 | 0.62 | 2.24 | 8.38 | 5.71 | Glass-NSG_R64_L100_build.json + export build_stats |
| SymphonyQG | 3.07 | 27.88 | 30.95 | 14.77 | 26.63 | SymphonyQG_R64_EF400_t3_build.json + export build_stats |

统一口径：本表“构图耗时”一律取 from-scratch 官方 build record（*_build.json 的 build_time_ms；Ours 取 total 2.46 min），graph_build_mode=reused_graph 的记录不计入构图耗时；“磁盘索引导出”为 05 层 export 的 .build_stats.json（payload 编码 + 写盘，wall 耗时与 peak RSS）。

Ours 磁盘查询使用的 native 图在 02 raw 中的拆分为 graph build 6.77 s / encode 17.47 s / total 24.25 s，其 graph_build_mode=reused_graph，属于复用图加载 + payload 编码，不是 from-scratch 构建；官方从零构建记录为 Ours_OursDiskANN_M64_build.json（graph 2.34 min / total 2.46 min / peak 6.80 GiB）。两者是两套口径（reused 加载 vs from-scratch 构建），不直接比较。M64 官方记录未记录 Lbuild/alpha；磁盘查询图参数（R64/Lbuild400/alpha1.2、ExRaBitQ4-symmetric）来自 02 manifest。

构建成本分析：总成本排序为 PQ（55.19）> SAQ（45.77）> SQ（40.68）> SymphonyQG（30.95）> Ours（9.13）> OG-LVQ（3.66）> Glass-NSG（2.24）min。PQ/SQ/SAQ 的总成本被共享图构建（40.32 min，仅发生一次）主导，其边际导出成本只有 14.87 / 0.36 / 5.44 min（差异来自 payload 编码方式：PQ 训 codebook、SAQ 球面变换、SQ 纯标量）。SymphonyQG 总成本几乎全在磁盘索引导出（约 90%），是第三方系统里导出最重的；Ours 构图轻（2.46 min）且导出 6.67 min，总 9.13 min，显著低于 SymphonyQG 与“按全额共享图口径”的 DiskANN 家族；OG-LVQ / Glass-NSG 最轻（<4 min）。
