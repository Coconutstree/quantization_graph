# GIST 分档整进程内存实验

实验入口、自动策略与复现命令见 [实验 README](experiments/04_ours_memory_budget/gist_budget_sweep/README.md)。

- [预算、模式、容量与曲线报告](results/04_ours_memory_budget/gist_budget_sweep/report.md)
- [执行状态](results/04_ours_memory_budget/gist_budget_sweep/state.json)
- [校准及阈值](results/04_ours_memory_budget/gist_budget_sweep/calibration/lock.json)
- [独立测试记录](results/04_ours_memory_budget/gist_budget_sweep/validation/unit_tests.json)

当前准入采用校准后的统一峰值包络，加8MiB完整查询增量及64MiB安全余量。预估常驻阈值约827.14MiB；额外边界档为811和844MiB。这个阈值不能替代实测最低可运行内存。

256、384、512MiB已实际执行低容量分页探测：256MiB线程创建失败；384MiB worker初始化失败（底层原因未暴露）；512MiB查询页缓存分配报`std::bad_alloc`。详细证据在`diagnostics/low_budget_*`，均保留失败日志。这些是具体配置的诊断结果，不表示所有可能实现都无法在这些预算下运行。640MiB目前是保守预检拒绝，尚未实测OOM。

预检通过768、811、844、1024、1536、2048MiB。各档最终完成状态以报告及`state.json`为准，不将已启动视为已完成。失败档位不作为QPS=0画入曲线。

查询缓存生命周期检查发现启动时的双份缓存及查询间的新旧缓存重叠；详见[当前版本的低预算失败诊断](results/04_ours_memory_budget/gist_budget_sweep/diagnostics/query_cache_lifetime.md)。因此，失败点仅代表当前实现，不能作为分页算法的理论最低内存。

## 查询缓存复用版本

见 [独立验证报告](results/04_ours_memory_budget/gist_budget_cache_reuse/report.md)：384 MiB 显式全分页可运行；512/640 MiB 自动分支通过。原版本结果仍按原二进制解释。与新验证重叠的 full QPS 标记并排除，见 `validation/cache_reuse_overlap.json`。
