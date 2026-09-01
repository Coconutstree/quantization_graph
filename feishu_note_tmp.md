统一口径：本表“构图/构建时间”一律取 from-scratch 官方 build record（*_build.json 的 build_time_ms；Ours 额外给出 graph_build_time_ms 拆分），graph_build_mode=reused_graph 的记录不计入构图耗时。

Ours 磁盘查询使用的 native 图在 02 raw 中的拆分为 graph build 6.77 s / encode 17.47 s / total 24.25 s，其 graph_build_mode=reused_graph，属于复用图加载 + payload 编码，不是 from-scratch 构建；官方从零构建记录为 Ours_OursDiskANN_M64_build.json（graph 2.34 min / total 2.46 min / peak 6.80 GiB）。两者是两套口径（reused 加载 vs from-scratch 构建），不直接比较。M64 官方记录未记录 Lbuild/alpha；磁盘查询图参数（R64/Lbuild400/alpha1.2、ExRaBitQ4-symmetric）来自 02 manifest。如需把构建耗时严格绑定到磁盘查询图本身，可在 Ours 图上补一次 from-scratch 插桩构建。
