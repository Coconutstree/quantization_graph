# Starling 搜索崩溃定位与恢复（2026-09-17）

## 结论

原生 Starling 的 GIST R48 索引已成功生成。此前搜索崩溃是 32 线程工作区无法装入 2 GiB 进程地址空间预算导致；不是需要重新建索引的问题。未修改 Starling 源码、算法、工作区大小或二进制。

修复范围是外层实验驱动：搜索前检查已知内存分配下界，预算不足时明确输出 `blocked_memory`，避免再次触发官方程序的空指针访问；允许复用索引，以显式指定且独立保存的资源配置继续诊断。

## 根因证据

固定官方版本的 `src/pq_flash_index.cpp::setup_thread_data` 为每个线程分配 `4 × 16384 × aligned_dim` 字节坐标工作区。GIST 960 维时每线程 60 MiB，32 线程仅这一项就是 1920 MiB。

官方 `include/utils.h::alloc_aligned` 使用 `aligned_alloc`，失败检查仅通过 assert；当前 Release 二进制未在失败时安全返回，之后执行 `memset(scratch.coord_scratch, 0, coord_alloc_size)`。

[主机 GDB 日志](gdb_as2g_host.log) 在 2 GiB 限制下复现 SIGSEGV，调用栈为 `memset → PQFlashIndex<float>::setup_thread_data`，目标地址寄存器 `rdi=0`。这与分配失败后的空指针清零一致。调试未修改源码和二进制。

[全量查询内存下界](memory_preflight.json) 为 **2,148,741,824 B，约 2.00117 GiB**，超过 2 GiB 的 2,147,483,648 B。计算包含固定工作区、PQ 短码、分区向量和映射、导航向量、800 条查询及一份 PQ 码本，尚未计入库、线程栈、visited、导航边与分配器开销；这是拒绝明显不适配配置的下界，不是完整内存估计。低于预算不保证一定能运行。

## 使用同一索引和二进制的对照验证

查询取实际原测试顺序的前 16 条，L=40、beam=1。以下是功能与内存诊断，不是公平性能结果。

| 配置 | 结果 | 采样峰值 RSS | 采样峰值地址空间 |
|---|---|---:|---:|
| 32 线程／2 GiB | 初始化阶段 SIGSEGV（原运行及 GDB 复现） | 见原测量 | 见原测量 |
| 32 线程／4 GiB | 正常完成 | 2,160,070,656 B | 2,590,728,192 B |
| 16 线程／2 GiB | 正常完成 | 1,149,001,728 B | 1,430,777,856 B |

两组成功运行的有序 top-10 ID 完全一致，ID 合法、无重复；距离与原始向量 float64 重算一致，最大绝对误差约 3.69e-7。见 [结果校验](validation.json)、[32 线程测量](w32_as4g_measurement.json)、[16 线程测量](w16_as2g_measurement.json)。

2 GiB／32 线程的新增运行前检查也已实际执行，返回 `blocked_memory`，没有启动搜索进程。

## 恢复执行与输出

恢复脚本：[recover_starling_search.py](../../../experiments/05_disk_system_fair/recover_starling_search.py)。复用原图、导航图、分区和查询顺序，采用 **32 线程／4 GiB RLIMIT_AS**，执行原来的全部搜索宽度与 800 条测试查询。保持旧原始失败记录不变，仅补充原因分类。

后台结果位置：

`results/disk_environment/05_disk_system_fair/test_L_400_w_32/gist/Starling-Disk/diagnostics/w32_as4g_20260917/`

仍生成 result.json、queries.jsonl、command.json、terminal.log、memory_measurement.json、storage_protocol.json 和核验状态文件。查询记录明确写入真实的 4 GiB 预算；官方没有导出的指标继续为 null。源码、二进制哈希与原运行一致；恢复脚本不重新建图。

完整搜索的实时状态由该目录 result.json 给出。**4 GiB 结果不得作为原 2 GiB 主对比曲线。** 若正式实验坚持 32 线程／2 GiB，应如实记录该配置不可运行；若采用新预算或线程数，其他方法也需在同一资源条件下重新比较。

验证：导出与内存下界单元测试 3 项通过；实际 2 GiB 运行前拒绝测试通过；两组原生成功诊断与 GDB 失败复现完成。
