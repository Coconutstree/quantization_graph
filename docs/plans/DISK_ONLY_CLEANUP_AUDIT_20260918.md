# 磁盘论文代码清理清单

审计日期：2026-09-18。范围：当前仓库的目录、构建入口、源码引用、脚本和结果布局。未遍历或修改根目录 `data/`，未删除文件，未修改现有代码。结论来自静态依赖检查，未执行重编译或实验。

目标：交付仅围绕磁盘方法的可复现代码。区分“内存检索实验”与“磁盘方法所需的内存组件、建图、内存测量和一致性对照”；后者不能按名称删除。工作区已有大量已修改和未跟踪文件，清理时不能用 git reset/clean 代替逐项整理。

## 1. 可先从提交包移除的旧内存结果

| 路径 | 本次磁盘占用（du -sh） | 判断 |
|---|---:|---|
| `results/memory_environment/` | 468 KiB | 旧 query codec 诊断 notebook、报告和数据，非当前磁盘正式结果 |
| `logs/memory_environment/` | 168 KiB | 旧 01/02/03 内存实验及 query codec 日志、状态文件 |

这两项可以作为第一批删除对象。所检查的一方源码和运行脚本中未发现对这两个目录的字面路径依赖；历史材料如需追溯，应在仓库外归档。这里的“可删”针对复现磁盘方法，不意味着历史记录可重新生成。

## 2. 可移出提交包，但不应当作纯 memory 文件直接清空

| 路径 | 建议与条件 |
|---|---|
| `experiments/04_query_codec_1bit_scan/`、`results/04_query_codec_1bit_scan/` | 独立内存距离扫描微基准。若最终论文不保留 query codec 内核消融，可连同相关说明移除；它仍可能解释磁盘方法的计算内核 |
| `results/query_coarse_bench_gist_1bit_search_analysis/`、`results/query_coarse_bench_gist_1bit_search_v1/`、`v2/`、`v3_pinned/`、`v4_numa/`、`v5_numa_reverse/`（后五项沿用相同前缀） | 历史 query codec 对比结果；不再用于论文后可以归档删除 |
| `Ours/experiments/run_query_coarse_codec_bench.sh`、`run_gist_query_codec_search_sweep.sh`、`analyze_gist_query_codec_search_sweep.py`、`plot_gist_query_codec_search_sweep.py`、`build_gist_query_codec_search_report.py`、`build_gist_query_codec_search_markdown.py` | 与旧 codec 扫描/搜索消融配套，取消该实验时一起清理，并更新调用和文档 |
| `scripts/plot_query_coarse_codec.py` | 随对应旧实验移除 |
| `ablation/` | 约 140 MiB，包含 kmeans 消融结果和图表。量化器训练消融不自动等于内存系统实验；确认论文不引用后归档 |
| `Ours/logs/` | 约 96 KiB，旧统一 Ours 入口的日志；归档后可不交付 |
| `LVQ.pdf`、`OQG.pdf`、`SymphonyQG.pdf` | 参考论文，不属于运行代码，可移到个人文献目录 |
| `feishu_05c_analysis.md`、`feishu_build_section.md`、`feishu_note_tmp.md`、`feishu_opt_new.md` | 飞书编辑中间材料，可移出代码提交包；其中有磁盘分析，不归类为 memory 实验 |
| `test.md`、`docs/notes/test.md` | 研发笔记；将仍有效的要求合并到正式文档后移除 |
| `run_glass_l400.sh` | 带绝对路径和固定 run-id 的一次性磁盘运行脚本，可在通用入口覆盖其配置后移除 |
| `.vscode/` | 个人编辑器配置，通常不用交付 |
| `paper/figures/`、`paper/system_fair_table_all.tex` | 旧图表/表格候选；先核对论文引用，再决定是否随旧实验移走 |
| `docs/analysis/plan_document_audit_20260917/` | 文档同步与审计中间记录，可外部归档；不是 memory 源码 |

论文仅研究磁盘系统，并不必然要求删除所有内存中的内核消融。最终是否保留该类实验应与正文/补充材料一致。

## 3. 必须先拆依赖，不能整目录删

### experiments/01_quantizer_fair/

既包含旧量化对比，也提供固定候选生成工具。`scripts/build_formal_local.sh:38` 仍构建该工程；`scripts/local_runs/build_agnews_dbpedia_prereqs.sh:12` 使用其 `faiss_hard_negative_candidates`，05A 原生测试和数据准备使用 `work/01_quantizer_fair/`。

建议将磁盘 05A 需要的候选生成器及必要工具独立出来，再移除纯内存评估框架、旧绘图/批量入口。现状不能整删。

### experiments/02_diskann_fair/

**当前磁盘实现的实际构建和算法依赖，必须保留。**

- `Cargo.toml` 定义 `qgraph05_shared_graph_port`，入口指向 `../05_disk_system_fair/native_rust/src/main.rs`。
- `src/lib.rs` 引入 05 的 Rust 模块，并导出 `ours_diskann`。
- 05 的 `native_rust/src/ours_port.rs` 使用 `crate::ours_diskann`。
- `build.rs` 编译自身的 `native/rabitq_bridge.cpp`，以及 05 的 direct I/O 源码。
- `scripts/local_runs/build_05_core_graphs.sh` 还使用 `run_diskann_fair` 建图。

后续可以迁移/改名，但不能因为存在旧内存搜索入口，就直接删掉整个 02 或 `run_diskann_fair`。

### experiments/03_system_fair/

主体是旧完整系统比较框架，但仍有直接磁盘依赖：

- `experiments/05_disk_system_fair/native/og_lvq_disk_port.cpp:354` 调用 `experiments/03_system_fair/adapters/_scripts/svs_run.py`。
- `scripts/local_runs/build_agnews_dbpedia_prereqs.sh:111` 调用旧 03 runner 建索引。
- `scripts/build_formal_local.sh:39` 构建 03 CMake 工程。
- 若干合并、统计、审计脚本导入 03 包。

建议先迁移仍需的 SVS 建图与公共数据工具，再删除旧调参、纯内存搜索及专属报表入口。若同时取消 OG-LVQ 补充磁盘实验，还需修改 05 CMake、端口登记、测试和依赖安装流程。

### Ours/experiments/ 与 scripts/

`Ours/experiments/run_ours.py` 仍被 03 Ours adapter 调用。`run_master_round2.sh`、`run_formal_int8_three_datasets.sh`、旧 01/02/03 绘图与报表脚本可随旧实验退休，但需要先检查相互调用和共用函数。`scripts/local_runs/` 中大量脚本名包含 03，实际服务于磁盘建图、恢复、缓存审计和结果发布，不能整删。

## 4. 明确保留

| 路径 | 原因 |
|---|---|
| `data/` | 按用户要求保持不动 |
| `Ours/core/` | 量化、距离核和建图实现，02 native bridge 直接引用 |
| `experiments/05_disk_system_fair/` | 当前磁盘运行、I/O、端口、协议与验证主体 |
| `experiments/06_adaptive_disk_ann/` | 磁盘方法的自适应投影/路由实验；是否进入最终论文另定，不能当作旧 memory 实验删 |
| `tests/`、`Ours/tests/`、05 内测试 | 当前磁盘入口、codec 与一致性验证；按最终保留的功能收敛，不整体删除 |
| `results/disk_environment/`、`logs/disk_environment/` | 磁盘实验结果和日志；内部 01/02/03 分别对应 05A/05B/05C |
| `results/graph/`、`results/<dataset>/indexes/` | 磁盘导出或建图脚本仍寻找/复用的图和索引，不能当成旧结果清空 |
| `work/05_disk_system_fair/disk_root/` | 实际磁盘布局/索引，非普通临时缓存 |
| `work/01_quantizer_fair/` | 固定候选缓存，05A 的输入之一 |
| `baselines/diskann/`、`baselines/aisaq/`、`baselines/starling/` | 磁盘框架和原生 baseline |
| `baselines/DEPENDENCY_LOCK.json`、`baselines/patches/`、许可证与 NOTICE | 版本锁、源码差异和第三方许可材料 |
| `CMakeLists.txt`、`environment.yml`、`requirements.txt`、安装/构建脚本 | 复现入口，需在精简源码时同步调整 |
| `paper/sigmod_draft/` 和对应源数据 | 当前论文与磁盘图表材料，保留可追溯输入 |

下列名称容易误删，实际需要保留：

- `experiments/05_disk_system_fair/memory_runner.py`：磁盘实验的 cgroup 限额和 RSS 测量，被 orchestrator 和官方 baseline 入口导入。
- `experiments/05_disk_system_fair/tests/test_method_memory_policy.py`、`test_measured_protocol.py`：磁盘资源政策测试。
- `experiments/05_disk_system_fair/plot_05_gist_test_memory_style.py`：绘制磁盘数据，只是沿用旧图形样式。
- `blocked_05c_memory_system_port.py` 及其 integration test：防止误把纯内存 baseline 当作磁盘系统的保护性检查，可以保留。
- `docs/analysis/starling_memory_recovery_20260917/`、`disk_memory_protocol_review_20260917/`：磁盘实验内存故障/协议记录。
- DiskANN 内 `inmem` provider 和 Starling 内存导航代码：磁盘方法可以包含内存组件，不能按关键词批量删源码。

`baselines/pyglass/`、`symphonyqg/`、`svs/`、`faiss/`、`saq/` 当前均被 05 C++ 端口的 include/link 使用。即使主实验最终只保留 Ours/DiskANN/AiSAQ/Starling，也必须先移除相应补充端口、05A 功能和构建依赖，再裁剪这些第三方库。

## 5. 生成产物：可不交付，但本地删除会失去现成运行环境

- `build/`、各 Rust `target/`、`baselines/builds/`：构建产物。交付源码时通常排除，但本地删除后要重编译；现有端口登记指向这些二进制。
- `__pycache__/`、`.pyc`：可清理，严格排除 `data/` 及指向它的链接。
- `baselines/deps/`、`baselines/tools/`：依赖/工具安装目录。先确认安装脚本可重建，不能作为无用文件随意清空。
- `downloads/`：下载原始包。确认 `data/` 无软链接依赖、转换/重建不再需要这些源文件后，才能考虑移到仓库外。
- `work/`：混合存有实际索引、工作数据、烟雾测试和备份。需要逐个识别，不能整体删。
- `.git/`：仅在制作独立提交压缩包时排除，不能清理当前工作仓库的版本历史。

本次没有核验所有软链接目标、运行中进程或第三方库内部的逐文件可达性，因此这些生成/安装/工作目录不属于已确认可以立即本地删除的清单。

## 6. 推荐顺序

1. 先归档/移除两处明确的 `memory_environment` 历史结果与日志。
2. 将参考 PDF、飞书草稿、个人配置和无关历史材料移出代码提交包。
3. 从 01/03 提取磁盘需要的候选生成器、SVS 建图和公共工具；保留 02 构建工程及 Ours 核心。
4. 同步收敛 CMake、Cargo、安装脚本、端口登记、README 和 source map，再移除旧内存运行入口及报表脚本。
5. 在独立输出目录验证干净构建、磁盘端口测试和小规模 build/export/search 流程，再制作不带大型构建/索引产物的提交包。

不建议直接执行 `rm -rf experiments/0[123]* baselines/* work/*` 或按 `memory` 关键字批量删除。
