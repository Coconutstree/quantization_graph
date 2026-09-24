> 2026-09-21 入口更新：本文件原有结果描述属于历史64页版本。执行 `run.py` 现在默认转到 `gist_auto_inflight256`，无参数或 `--prepare-only` 只预检；显式 `--run` 才启动单轮40-width test100。新版输出到 `results/04_ours_memory_budget/gist_auto_inflight256/`，旧结果不覆盖。详见[当前入口说明](../gist_fixed_factors_budget/README.md)。

# GIST：40 widths × test100

入口：`python experiments/04_ours_memory_budget/gist_width40_test100/run.py`。
若其他任务占用机器，可加 `--wait-for-pid PID`，等待该进程退出后开始；本脚本不会终止或暂挂其他任务。

- Ours使用gist_memory_auto_policy冻结二进制与校准锁，factors常驻；5个代表预算档（538/616分页、768/1024/1536记录缓存）和无记录缓存resident参考。
- DiskANN使用当前已构建原生二进制，PQ常驻；仅保留已完成的c0 test100参考，不再新增DiskANN测试。历史40 widths × test800基线保留在results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32，单独标注查询规模。
- 每条曲线40个width（10–30步长1，40–100步长10，140–580步长40）；原test顺序前100条，每width预热100条；32 workers、beam1。
- 每个配置只运行一轮，Ours正确性参考先执行。共主配置7个run、280个width点、28000条测量查询（6条Ours＋已完成的1条DiskANN参考）。其余已完成和切换时在途结果作为补充数据保留，不再启动密集边界点。800 queries没有自动启动入口。
- 每组先顺序O_DIRECT预读搜索文件；设备缓存不可控，不宣称冷缓存。运行中请勿同时启动其他性能实验。
- wait4提供整进程Peak RSS；50 ms采样VmRSS并根据日志阶段匹配width。查询RSS列是采样中位数/最大值，非精确峰值；过短阶段无样本时保留空值。
- 汇总只接纳完整通过校验的run。Ours核对逐查询结果及I/O；DiskANN核对全部width、查询数、recall和物理读取汇总。中断/失败目录保留，禁止静默覆盖。
- 不为每个width复制进程Peak RSS并声称它是独立width峰值。CSV明确区分process_peak和query_rss。

结果：`results/04_ours_memory_budget/gist_width40_test100/`，每个run完成后更新report.md、CSV/JSON以及PNG/PDF/SVG。

启动后需检查查询阶段RSS、recall趋势与缓存读取变化（单轮不评估重复稳定性），再决定是否以相同配置执行800查询。测量脚本不自动将本轮结果标为正式结论。
