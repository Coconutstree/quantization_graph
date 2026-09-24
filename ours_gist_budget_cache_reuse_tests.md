# GIST 查询缓存复用验证

实现与复测已完成，报告见 [验证报告](results/04_ours_memory_budget/gist_budget_cache_reuse/report.md)。

- 256 MiB：实际线程创建/内存分配失败，保留日志。
- 384 MiB：64 页全分页、32 workers、L100/300/580 通过；保守 auto 预检仍拒绝。
- 512 MiB：factors 常驻＋codes CLOCK 分页自动模式通过，VmPeak 443.64 MiB。
- 640 MiB：routing 常驻＋hot_dynamic 通过，VmPeak 585.31 MiB；静态记录 65,962 条，动态容量为 0。
- 估算固定开销 686 → 421 MiB；完整 routing 准入阈值 827.14 → 562.14 MiB，安全余量未删减。
- 8 组共 2400 条测量查询逐项与旧校准参考一致，物理 I/O 计账通过。

本轮为 validation 集的内存与正确性验证，不是新版本的独立 test 集完整性能实验。与旧实验重叠的 QPS 不用于独占设备对比。

实验入口：[README](experiments/04_ours_memory_budget/gist_budget_cache_reuse/README.md)。旧二进制、索引和原始结果未改；新源码及二进制位于 `work/ours_memory_budget/gist_budget_cache_reuse/`。
