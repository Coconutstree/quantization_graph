# 三层磁盘代码重构验证

日期：2026-09-18。变更范围：主实验目录、源码依赖路径、构建入口、结果布局及必要的验证修复。未运行正式数据集性能测量。

## 验证结果

| 检查 | 结果 |
|---|---|
| `tests/` unittest | 8/8 通过，包括三入口、跨层拒绝、自定义输出根、来源追溯、native ID 保留 |
| native contract 独立测试 | 10/10 通过 |
| 磁盘格式/量化/缓存独立测试 | 20/20 通过 |
| `src/disk_bench/tests/` unittest（宿主） | 45 项：44 通过、1 跳过；跳过项要求可写的 cgroup 委派 |
| C++ CTest | 5/5 通过，包括 PQ/SQ/Ours 的真实 resident/O_DIRECT 对照、I/O、Glass、Symphony、LVQ 缓冲检查 |
| 仅建图入口 | PQ、Ours 均成功生成图，不执行查询 sweep、不生成性能 CSV |
| `cargo check --offline --locked ... --bins` | 通过 |
| `scripts/build_formal_local.sh` | 成功构建候选生成器、C++ 磁盘端口、Ours/共享图 Rust 程序及 DiskANN 原生端口 |
| 顶层 CMake 配置、Python 语法、shell 语法、`git diff --check` | 通过 |
| 三层 GIST doctor | 25 项通过、1 项失败：AiSAQ/Starling 正式端口仍 pending |

日志见 [validation/disk_layout_20260918/](validation/disk_layout_20260918/)。C++ 量化验证修复后重新构建并通过 CTest；端口登记已经按最终二进制重新生成哈希。

DiskANN io_uring 在沙箱内返回 `Operation not permitted`；在宿主执行相同的小样本测试后通过。未通过关闭 I/O 要求或改用 buffered I/O 绕过测试。

## 验证中修复的既有问题

1. 01 的原生量化端口缺少协议要求的 `query_comparisons`。使用重构前的二进制也复现同样的验收错误。
   - resident validation 现在在所报告 sweep 计时之外执行一次真正的 O_DIRECT 对照。
   - 比较实际距离数组，拒绝非有限距离，按查询×候选宽度配置计数，写入 artifact 和 parity 状态文件。
   - disk validation 使用 resident reference 实测比较；test 读取此前的计数并核对 reference 哈希。
   - 没有计数的旧 parity 文件必须重新 validation，不得直接用于新 test。
   - 正式计时的搜索/量化算法未修改；resident 验证进程会额外执行对照读取，其生命周期资源记录应按实际值解释。
2. doctor 原先将依赖锁的键集合限定为旧七个依赖，因已有 AiSAQ/Starling 条目而误判锁无效。现在要求必要的七个基础条目存在，同时继续核查锁内所有路径存在且位于仓库内，允许新增已登记依赖。

## 保留的边界

- AiSAQ/Starling 的正式每查询 artifact、资源/计时证据与准入仍为 pending；重构不会授予正式资格。
- Ours 2 GiB cgroup 限额需要真实可写委派，本次跳过了需要该条件的集成测试；未宣称预算验证完成。
- SAQ 端口已成功编译，共用 parity 逻辑已更新，但本次未运行需要真实 SAQ 预处理索引的全流程测试。
- 数据、历史结果与原论文图表快照未重写；本次测试输出不是论文性能结果。
- 历史 CMake 构建树保留，当前程序使用 `build/disk/`。由于源码路径改变，不应直接复用旧树增量构建。

## 各层实现归位后的验证

后续按用户要求，将专属实现放回各自实验目录：01 的 native/tools、02 的 native、03 的 native/native_diskann/adapters。旧 Ours 实验归档，共用实现仍保持单一来源，数据、日志和结果布局未改变。

- 完整 C++/Rust 构建通过，端口登记与最终二进制哈希一致。
- 目录/入口测试 9/9，native contract 10/10，磁盘格式/量化测试 20/20。
- 宿主 native/Python 回归 45 项：44 通过、1 项 cgroup 条件不足跳过。
- CTest 5/5；PQ/Ours 仅建图检查通过。
- GIST 01/02 doctor：26 项通过、0 失败。03 原生 baseline pending 状态保持不变。
- 日志：`validation/disk_layout_20260918/owned_sources_*.log`。
