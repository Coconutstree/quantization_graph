# GIST Ours QPS 下降：证据和恢复方法

本说明针对此次 GIST、BFS graph+compact/residual 布局、32 workers、beam1、2 GiB RLIMIT_AS 的诊断。历史程序和当前 v2 baseline 均已完成完整复测及独立复核：当前 L100 **110.371 QPS**，历史109.301；当前40档总查询计时317.810秒，历史316.407秒（差约0.44%）。在显式记录的预处理条件下，当前版本的主要性能已恢复到历史水平。

## 已定位的主要因素

低 QPS 与可被直接顺序预读改变的下层存储读取状态有关。相同的旧布局文件、历史二进制、查询顺序和读取计数，在未进行该预读时约 9.5 QPS，直接预读后单档约 79.6 QPS；再恢复原40档顺序，L100 达108.816 QPS，历史为109.301 QPS。

这支持设备缓存/预取状态解释。没有读取控制器命中率或重放历史硬件状态，因此不能进一步断言是哪层缓存、何时失效或由哪次清理操作触发。没有证据支持删坏搜索代码或旧文件碎片更多导致此次数量级差距。不能把 O_DIRECT 理解为绕过所有设备缓存。

## 对照证据

| 实验 | L100 QPS | 说明 |
|---|---:|---|
| 旧文件交替复测两次 | 9.558 / 9.346 | 原程序、原文件 |
| 新导出文件交替复测两次 | 82.770 / 83.820 | 内容哈希相同，文件位置不同 |
| 旧文件普通 buffered 预读 | 9.629 | 普通哈希读取未恢复 |
| 旧文件 O_DIRECT 顺序预读 | 79.581 | 文件内容和位置不变 |
| 旧文件 O_DIRECT 预读 + 原40档前序 | 108.816 | 历史原程序；32000条查询13字段独立复核一致 |
| 同样条件，当前 v2 baseline | **110.371** | 32000条查询13字段一致，额外缓存为零 |

原40档总查询计时318.698秒，历史316.407秒，差约0.72%；各档并非完全相等。最低单档比例约0.69，不能写成所有档位逐点完全恢复。L100此前单档约80而完整扫描约109，说明只抽取一个L值不能替代原扫描协议；具体进程热身内部机制尚未单独隔离。

同一旧文件预读后也能恢复，所以重新导出、重新分配物理区间不是恢复的必要条件。实际设备请求等待也随干预降低，性能变化不只存在于程序内计时器。

## 重复当前 baseline 恢复实验

在仓库根目录执行。正式保存的诊断入口位于 `experiments/04_ours_memory_budget/run_storage_replay.py`；搜索核心没有改变。默认 `--pre-read none` 且只打印计划，必须加 `--execute` 才运行。

```bash
# 不做额外预读；不保证是冷缓存
python3 experiments/04_ours_memory_budget/run_storage_replay.py --pre-read none --execute

# 恢复实验验证过的预处理条件
python3 experiments/04_ours_memory_budget/run_storage_replay.py --pre-read direct --execute
```

省略 `--execute` 可先查看计划。`--output results/diagnostics/03_disk_system/layout_state_control/gist/<新的run-id>` 指定新目录；拒绝覆盖旧结果。`work/storage_recovery_20260919/replay.py` 是兼容入口，默认转发 `--pre-read direct`，也接受显式 `--pre-read none`。不要同时运行两种模式。

模式记录在 diagnostic_protocol.json；preparation.json 记录模式、文件身份、命令、返回码和独立耗时；report.md 标明模式。预读只在整个搜索进程之前执行一次，不是每个查询或每个L档前执行。输入身份检查仍可能普通读取文件，所以 none 仅表示没有额外直接预读，不能理解为清除了所有缓存。

该入口仍依赖当前已保存的 GIST 输入验证记录和指定 v2 baseline 二进制，是诊断复测入口，不是干净环境构建入口。此前 work 下的临时完整复测驱动仍保留供审计，但新入口不依赖它们。

执行顺序：核验输入和二进制 → 顺序直接读取两个原布局页文件 → 原40档搜索 → 32000条查询逐项对照 → 输出比较报告。读取命令等价于以下形式，实际绝对路径、时间和返回码保存在 preparation.json：

```bash
dd if=work/05_disk_system_fair/experimental_layouts/gist_bfs_split_20260912_115121/graph_compact.pages of=/dev/null bs=4M iflag=direct
dd if=work/05_disk_system_fair/experimental_layouts/gist_bfs_split_20260912_115121/residual.pages of=/dev/null bs=4M iflag=direct
```

必须确认预读成功，再启动搜索。脚本会检查失败并停止。无需 sudo、重建图或修改 RAID；不清缓存、不改文件内容。保持实验串行，并保留原40档顺序、100预热查询、800测试查询和32线程，才与本次恢复验证的条件一致。不能保证所有未来负载或设备状态下都获得完全相同数字。

## 论文结果口径

预读发生在查询计时之前，耗时单独记录。这表示显式预处理后的存储状态，不能称为冷设备缓存结果。后续若比较不同方法或缓存策略，应统一并记录存储预处理和查询前序，重新运行各组；不能用恢复后的 baseline 替换此前慢环境实验的分母。此处2 GiB约束为进程 RLIMIT_AS，不代表控制器缓存也计入同一预算。原正式结果和默认实验入口均未被替换。

## 原始记录

- [新旧文件交替](../../results/diagnostics/03_disk_system/layout_state_control/gist/20260919_alternating/report.md)
- [直接预读对照](../../results/diagnostics/03_disk_system/layout_state_control/gist/20260919_direct_preread/report.md)
- [历史程序完整恢复复测](../../results/diagnostics/03_disk_system/layout_state_control/gist/20260919_direct_full/report.md)
- [当前 baseline 完整恢复复测](../../results/diagnostics/03_disk_system/layout_state_control/gist/20260919_direct_current_full/report.md)
- [文件分配检查](../../work/layout_allocation_audit_20260919/report.md)

## 完成核验

两版各40档、各32000条查询均通过结果、召回、遍历计数、I/O计数、查询缓存等13字段核验；当前40档memory_stats与逐查询I/O汇总一致，cache_reserved_bytes=0；2 GiB RLIMIT_AS检查通过；原/新二进制哈希分别固定，当前构建记录中的源码哈希仍一致。独立核验输出见 [completion_audit.json](../../work/storage_recovery_20260919/completion_audit.json)，可通过 `python3 work/storage_recovery_20260919/verify.py` 重新检查。

恢复入口沿用已通过40档实测的搜索/校验流程，新增 none/direct 开关。已验证两种计划模式不启动实验、none 不执行预读、direct 实际读取且不改字节、预读失败时记录失败并抛错停止。未因新增开关重复运行第三次40档扫描。
