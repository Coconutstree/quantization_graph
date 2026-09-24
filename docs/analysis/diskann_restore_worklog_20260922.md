# DiskANN 官方化交接（2026-09-22）

> 新对话先读本文件，再读 `实验交接文档.md`、`results/diagnostics/aligned_20260921/status.json`
> 与 `results/diagnostics/aligned_20260921/status.paused_20260922.json`。本文件是 2026-09-22
> 下午这次对话的快照：目标是把 DiskANN baseline 恢复成官方代码，并用新 TAG 全量重跑 03/05。

## 0. 一句话现状

官方化改造**已完成并通过验收**：`baselines/diskann` 现在是 pinned commit 官方内容 + **3 个声明式补丁**，
03 端口已迁到树外独立 crate，两侧编译选项已对齐，行为等价闸门与准入测试全部通过；旧队列已于
**2026-09-22 15:31:35 +08:00 暂停**并存证，msmarco 构图**未暂停**（只解冻了它的 driver）。
当前状态：**在同一个 TAG `aligned_20260921` 内就地覆盖重跑**，只重测二进制变了的 Ours-Disk（03+05）
与 DiskANN-PQ-Disk（03）；Glass-NSG-DiskPort / SymphonyQG-DiskPort 二进制未变，由编排器自动复用既有 artifact。
（用户 2026-09-22 16:0x 明确要求"没必要重跑 Glass/Symphony，覆盖上一次结果即可"；详见第 5.3 节。）

## 1. 队列暂停记录（已完成）

- 暂停时间：2026-09-22 15:31:35 +08:00。
- 方式：`SIGTERM` 到队列进程组 155143（`scripts/run_aligned_03_05.py` pid 155144 +
  `experiments/05_memory_budget/run.py` pid 237448）以及独立会话的原生子进程
  `src/graph_core/target/release/qgraph05_shared_graph_port` pid 239318。
  注意：原生端口自己 `setsid`，PGID/SID 均为 239318，**只杀父进程组杀不掉它**。
- 验收：`pgrep -af "qgraph05|run_aligned_03_05|disk_system/run.py|memory_budget/run.py"` 为空；
  无残留 `T`（冻结）进程；无第二个队列实例。
- 存证：`results/diagnostics/aligned_20260921/status.paused_20260922.json` 是暂停前原样快照；
  `status.json` 已改为 `paused_for_official_restore_20260922`，并把
  `dbpedia_aligned_20260921_05_ram1` 标为 `abandoned`（当时只到 hot-profile 准备阶段，无有效测量）。
- 队列自带的 `finally: SIGCONT` 清理**不会**因 SIGTERM 执行：已手动核对
  `results/diagnostics/03_other_methods_admission_20260921/aisaq_suspended_for_performance.json`，
  其中唯一记录 pid 4126608（AiSAQ 建索引）早已退出，无需处理。

### 1.1 msmarco 构图：半冻结 + 已解冻（重要）

- 发现：`logs/msmarco_graph_build_20260915/` 的 driver pid 2388321 与 build bash pid 2388323
  当时是 `T`（被人 SIGSTOP 过，不在队列的 pause 记录里），但 builder 二进制 pid 2388331
  （`run_diskann_fair`）一直是 `R`，仍在推进。
- 风险：shared 阶段结束后，被冻结的 driver 无法安装图、也无法启动 `ours` 角色，整个作业会假死。
- 处置：已对 2388321/2388323 发 `SIGCONT`；未 SIGSTOP 任何构图进程；tmux 会话
  `msmarco_graphs_20260915` 与 `work/05_disk_system_fair/msmarco_graph_build_20260915/` 未动。
- 当前进度（15:29）：`points_processed=110,460,928 / 113,520,750`，速率约 300 点/秒，
  shared 阶段预计 18:00–18:15 前后结束；之后 driver 会安装 shared 图并启动 `ours` 角色
  （ours 从零建图，按 AGNews 每点成本外推约 8–10 天，**估计值**）。
- 活日志：`work/05_disk_system_fair/msmarco_graph_build_20260915/staging/msmarco/shared/02_diskann_fair/msmarco/logs/PQ/msmarco_PQ_R64_Lbuild400.log`
  （`logs/msmarco_graph_build_20260915/shared_progress.log` 是指向它的软链接）。

## 2. 核查结论：`baselines/diskann` 相对官方差了哪些

**真源**：本地有一份干净的官方 clone `/home/kai3/coco/quantized_hnsw/baselines/diskann`，
`origin=https://github.com/microsoft/DiskANN`，`HEAD=3218478b5f7d1721840c29309e22d9fd74b1b4bc`
（与 `baselines/DEPENDENCY_LOCK.json` 的 pin 一致，workspace `version = 0.55.0`，shallow clone）。
用 `diff -rq --exclude=target --exclude=.git` 对拍得 **23 条差异**（去掉 `Cargo.lock` 与 LFS
`test_data` 后约 20 个源码文件），分六类：

| # | 类别 | 具体文件/内容 | 性质 |
| --- | --- | --- | --- |
| 1 | 结构重构 + 版权头 | `diskann-disk/src/build/mod.rs` 把上游 `build/configuration/*` 拍平到 `build/*`；`build/builder/{build,mod}.rs` 由上游 397/15 行替换成 ~30 行 stub；多出 `build/{disk_index_build_parameter.rs,filter_parameter.rs}` 且**无 Microsoft 版权头** | 本地重构，最严重（含许可/归属问题） |
| 2 | 构建注入 | `diskann-disk/Cargo.toml` 加 `serde_json.workspace = true` + `[[bin]] qgraph05_diskann_port`（path 指向 `experiments/03_disk_system/native_diskann/src/main.rs`，HEAD 里原指向 05 目录） | 本地注入，由提交 `2f1c872` 引入 |
| 3 | 内部 API 放宽 | `diskann-providers/.../inmem/mod.rs` 导出 `SQQueryComputer`；`inmem/scalar.rs`、`inmem/spherical.rs`、`fast_memory_quant_vector_provider.rs` 放宽 `dim/get_vector/query_computer/get_vector_sync` 可见性并新增 `quantizer()/compressed_vector()/set_compressed_vector()`；`diskann-quantization/src/spherical/quantizer.rs` 新增 `restore_from_scaled_shift()` | 02/05B（SQ/SAQ/球形）端口依赖，非行为改动 |
| 4 | 仪表计数 | `diskann/src/graph/{mod,index}.rs` 全局 `GRAPH_BUILD_DISTANCE_EVALUATIONS`；`diskann-quantization/.../kmeans/{mod,lloyds,plusplus}.rs` PQ k-means 计数 | 只影响上报指标 |
| 5 | 行为改动 | `diskann-providers/src/model/pq/fixed_chunk_pq_table.rs` 越界时 `chunk.fill(0)` 代替 panic | 仅越界路径，动机未记录 |
| 6 | I/O 加固（未提交，仅 staged） | `diskann-disk/.../aligned_file_reader/reader/linux.rs` 重写 io_uring 完成等待/短读检测；新增 `diskann-disk/tests/io_completion_regression.rs` | 正确性修复；`scripts/local_runs/pin_diskann_completion_fix.py` 记录动机是"旧二进制出现 allocator heap corruption，归因未确证，acceptance pending" |

处置结果：类别 3/4/6 → 保留为声明式补丁；类别 1/2 → 已撤销（恢复官方结构、版权头、构建接线）；
类别 5 → **已丢弃**。依据：`aggregate_coords`（被打补丁的函数）在整棵树里只有 `compute_pq_distance`
一个调用者，而后者只被 disk 搜索路径使用（`quantizer_preprocess.rs`、`disk_provider.rs`），
传入的 id 来自磁盘索引图、天然 < PQ 码数；我们自己的代码从不调用它。保留它会带来"越界静默填零"
的隐患，因此按规则撤销，并在撤销后重跑等价闸门确认结果不变。

其他核查事实：

- 03 端口 `experiments/03_disk_system/native_diskann/src/{main,cached_reader,query_cache}.rs`
  **只依赖官方公开 API**：逐个核对 `DiskIndexSearcher`、`DiskIndexWriter`、`DiskVertexProviderFactory`、
  `SearchMode`、`A1/AlignedRead`、`AlignedReaderFactory`、`QueryStatistics`、
  `diskann_disk::disk_index_build_parameter::DISK_SECTOR_LEN`（官方经 `build/configuration` 再导出，
  路径不变）、`GeneratePivotArguments/NUM_PQ_CENTROIDS/MAX_PQ_TRAINING_SET_SIZE/NUM_KMEANS_REPS_PQ`
  等，全部在 pinned 树同路径存在 → 可以对着官方树编译，**不需要任何补丁**。
- 该目录里已留有独立 crate 的骨架：`src/` 三个文件 + `target/debug/.fingerprint/qgraph05-diskann-port-*`
  （2026-08-22 构建痕迹），但**没有 Cargo.toml**；端口现在实际由 vendored `Cargo.toml` 的 `[[bin]]` 构建。
- 注册表链路：`src/disk_bench/ports.local.json` 记录 `command[0]` + `binary_sha256` +
  `implementation_fingerprint`；`src/disk_bench/orchestrator.py` 在复用/启动前校验二进制 sha
  （第 466–470、744 行附近），复用判据含 `run_id`、`native_binary_sha256`、
  `implementation_fingerprint` → **换二进制必须换 run ID**。

## 3. 本对话已做的改动（截至 16:00）

**运行时操作**：停旧队列（SIGTERM 进程组 155143 + 独立会话子进程 239318）、解冻 msmarco driver
（SIGCONT 2388321/2388323）；不产生文件改动。

**证据与状态文件**

1. `logs/msmarco_graph_build_20260915/queue.recovered.log`（新增，57 B）—— 从 `/proc/2388321/fd/1`
   恢复的、09-15 被 unlink 的 driver 日志，内容仅一行 `[msmarco][shared] build start: 2026-09-15T22:03:59+08:00`。
2. `logs/msmarco_graph_build_20260915/msmarco_shared.recovered.log`（新增，357 B）—— 从
   `/proc/2388331/fd/1` 恢复的 builder stdout，与既有快照 `diff` 完全一致。
3. `results/diagnostics/aligned_20260921/status.paused_20260922.json`（新增，暂停前原样快照）+
   `status.json`（改为 `paused_for_official_restore_20260922`，dbpedia 05 ram1 标 `abandoned`）。
4. `results/diagnostics/baseline_restore_20260922/`：`before/`（改造前整树快照）、
   `diff_report_raw.txt`（23 条偏差原始清单）、`binary_updates.json`（新旧二进制哈希与指纹）、
   `equivalence_gate.json`（等价闸门证据）、`index_state_before_rerun.json`（重跑前索引 meta/manifest 哈希）。

**官方化改造（代码）**

5. `baselines/diskann`：整树恢复为 pinned commit 官方内容（保留 `target/`），删除多出的
   `diskann-disk/src/build/{disk_index_build_parameter,filter_parameter}.rs`、`diskann-disk/tests/`、
   旧注入二进制；上游 LFS `test_data/` 有意不引入（本仓从未跟踪）。
6. `baselines/patches/diskann-01-internal-api-exposure.patch`、
   `diskann-02-build-distance-counters.patch`、`diskann-03-io-completion-safety.patch`（新增，共 12 个文件）。
7. `baselines/diskann.manifest.json`（新增，1270 个官方文件 sha256）+
   `scripts/verify_diskann_baseline.py`（新增，正向/反向校验，已接入 `scripts/build_formal_local.sh` 前置）。
8. `baselines/DEPENDENCY_LOCK.json`：diskann 条目补 `url/manifest/patch_policy/patches`。
9. `experiments/03_disk_system/native_diskann/{Cargo.toml,.cargo/config.toml}`（新增，独立 crate，
   `target-cpu=x86-64-v3` + `codegen-units=1`）；`src/graph_core/{Cargo.toml,.cargo/config.toml}` 同步对齐。
10. `src/disk_bench/ports.local.json`（经 `scripts/write_05_ports_local.py` 重生成：DiskANN 端口新路径 +
    新 sha + 指纹 `05c-official-diskann-pq-release-io-uring-v2`；shared-graph 端口新 sha）。
11. 路径引用更新：`scripts/build_formal_local.sh`、`scripts/local_runs/{build_03_c0_system_queue.sh,
    build_03_system_graphs_queue.sh,run_agnews_acceptance.py,pin_diskann_completion_fix.py}`、
    `src/disk_bench/tests/test_formal_admission.py`（旧路径 → `experiments/03_disk_system/native_diskann/target/release/`）。
    `experiments/04_*` 里的历史 run.py 与 `results/**/command.json` **有意不改**（历史证据）。
12. `scripts/run_aligned_03_05.py`：新增 `--tag` / `--datasets` / `--experiments`，并在启动前断言
    "注册表 sha == 磁盘二进制"（`verify_registry()`）。
13. 本文件（新增）与 `实验交接文档.md` 顶部指针行。

**关键数值**

- 等价闸门（agnews w10/w40，各 800 查询，与 `aligned_20260921` 存档逐 query 比对）：
  `result_ids` 完全一致、recall 完全一致、`io_requests` 22048/61834 完全一致、
  距离计算次数完全一致；闸门在"含补丁 4"和"去掉补丁 4"两种二进制下各跑一次，结果都不变。
- 准入测试：`python -m unittest discover -s src/disk_bench/tests -p test_formal_admission.py` → 5/5 OK（宿主执行）。
- 最终 03 端口二进制 sha256：`dd5a72b0de38956be1a6d350ae2706f41c7ecb4d4f2a255189c57052bc6892a5`。

## 4. 已定决策（本轮不再讨论）

1. `baselines/diskann` **整树恢复官方**内容；02/05B 必需的改动收敛为**声明式补丁**
   （`baselines/patches/*.patch` + `DEPENDENCY_LOCK.json` 登记，可反向校验、可一键还原）；
   03 端口迁到树外独立 crate，**零补丁**。
2. **保留**建图距离计数指标（`graph_build_distance_evaluations` / `build_distance_computations`），
   作为声明式补丁。
3. **全量重跑**：新 TAG 下重跑 gist/agnews/dbpedia 的 03（四方法）与 05（四档）。
4. **对齐编译选项**：`src/graph_core`（03 的 Ours-Disk 与全部 05B/05C 端口都从这里构建）当前是
   默认 generic x86-64 + `codegen-units=16`，而 DiskANN 侧有 `target-cpu=x86-64-v3` +
   `codegen-units=1` → 两侧对齐后一起重跑。

## 5. 执行计划（1–2 已完成，3 进行中）

### 5.1 恢复官方树 + 声明式补丁（✅ 已完成）

快照在 `results/diagnostics/baseline_restore_20260922/before/`；真源 pin 校验通过；树已恢复
（保留 `target/`），多出文件与旧注入二进制已删；补丁为 `baselines/patches/diskann-0{1,2,3}-*.patch`
（12 个文件，登记于 `baselines/DEPENDENCY_LOCK.json`）；补丁 4（PQ 越界守卫）按第 2 节理由**丢弃**；
`baselines/diskann.manifest.json` + `scripts/verify_diskann_baseline.py` 已建立并接入
`scripts/build_formal_local.sh`。正向校验输出："1270 pinned files + 12 patched files (3 declared patches)
at 3218478b5f7d"；负例（注入未登记改动）会失败，验证后已还原。

### 5.2 端口迁移 + 编译选项对齐（✅ 已完成）

新 crate `experiments/03_disk_system/native_diskann/`（bin `qgraph05_diskann_port`）对官方树编译通过
（55 s，零补丁），`.cargo/config.toml` 与 `[profile.release]` 设 `target-cpu=x86-64-v3` +
`codegen-units=1`；`src/graph_core` 同配置并已重建（2m36s）。注册表、构建脚本与活跃路径引用均已更新
（见第 3 节第 10–12 条）；`experiments/04_*` 历史 run.py 与 `results/**/command.json` 有意不改。

### 5.3 重跑与验收（进行中，改为就地覆盖）

1. ✅ `scripts/run_aligned_03_05.py` 已加 `--tag/--datasets/--experiments` 与启动前 `verify_registry()`。
2. ✅ 行为等价闸门通过（见第 3 节"关键数值"）。
3. ⏳ **就地覆盖重跑**：命令
   `python scripts/run_aligned_03_05.py --tag aligned_20260921 --experiments 03_disk_system,05_memory_budget`。
   只重测 Ours-Disk（03+05 全部）与 DiskANN-PQ-Disk（03）；Glass/Symphony 由编排器判定
   "reused existing artifact" 直接复用（依据：它们的 binary sha 与 implementation fingerprint 未变）。
4. ⏳ 每数据集 03 出表出图、05 四档出表出图；准入不放松（参考一致性、存储预处理、峰值 RSS、
   03=36 行 / 05=36 行、`formal_ready=true`）；旧图会被队列先备份到
   `results/diagnostics/aligned_20260921/previous_outputs/` 再覆盖。
5. ✅ 测试：`verify_diskann_baseline.py` 正/反例通过；03 新 crate、`src/graph_core` release 构建通过；
   准入测试 5/5 通过（宿主执行，沙箱会拦 io_uring）。

**就地覆盖需要绕过 5 类"不许覆盖"保护（已逐项留档，未改动任何校验代码）**

| 保护 | 处置 | 存档位置 |
| --- | --- | --- |
| `protocol.json` / `invocation_*.json` 的 `ports_registry_sha256` 不等即报 "run configuration changed" | 把旧哈希换成新值（只改哈希字段） | `…/superseded_measurements_aligned_20260921/guards/manifests/` |
| `fixed_parameters.lock.json` 不等即报 "fixed parameter lock changed" | 先存档再删除，让管线按新二进制重算 | `…/guards/locks/` |
| 旧 hot-profile（内含旧 `native_binary_sha256`）会让准备阶段报 "hot-record profile mismatch" | 先存档再移走，重新跑 validation 热准备 | `…/hot_profiles/` |
| `.reference` 目录按独占创建（`mkdir(exist_ok=False)`） | 先存档再移走，让参考验收重新生成 | `…/references/` |
| 既有测量文件（`.json/.native.json/.parity.json/.queries.jsonl/.resources.*/.storage_precondition.json/.terminal.log`）触发 "refusing to overwrite existing attempt" | 先存档再移走（137 个文件） | `…/measurement_files/` |

**补充：改 Ours 二进制会连带作废同 dataset 的其余方法（2026-09-22 17:15 发现）**

编排器给**每个方法**的原生命令都传 `--tuning-lock <dataset>/manifests/fixed_parameters.lock.json`，
而该 lock 里含 Ours 的热 profile 哈希（`ours_hot_profile_manifest/sha256`）与路由计划。因此只要
Ours 的准备（=它的二进制）变了，lock 就变，**所有方法**的 `validate_artifact` 都会以
"resource evidence: reference input changed" 判为不可复用 → 必须四方法一起重测。
这也意味着：一旦出现"只想重测 Ours/DiskANN、其余复用"的需求，前提是**不要重建 Ours 二进制**；
一旦重建，同 dataset 的 Glass/Symphony 也逃不掉（dbpedia 上这会多花约 1–1.5 小时）。
结论：**本轮起冻结 Ours 二进制**，不再重建；后续若要改端口代码，先跑完当前 TAG。

**新旧对照（gist · Ours-Disk，同一 TAG 覆盖前后）**：recall 完全一致（w10 0.6830 / w100 0.9527 /
w580 0.9865），`io_requests` 一致（57.47→57.51 / 231.89→231.86 / 785.44→785.43）；
QPS 变为 357→730（w10）、334→424（w100）、147→147（w580）。w10 的翻倍**大于纯编译选项能解释的范围**，
更可能是"旧 TAG 首次测量时设备/缓存状态更冷 + 期间 msmarco 争夺 CPU"的测量态效应；
在论文里不能把这条当成算法改进，待整轮数据出来后再判定。

**重跑后必须补的两项核对**

- 新 run 的 `source_index_manifest_sha256` 与 `index_state_before_rerun.json` 里记录的旧值是否一致：
  若一致说明重跑没有重建索引（只是复用）；若不一致，需要在论文方法里说明索引被重写而非复用，
  并确认旧 TAG 的产物仍以其自身记录的哈希为准（旧结果不因此失效，但其引用的磁盘文件已更新）。
- 每数据集 03=36 行、05=36 行且 `formal_ready=true`，并人工看图（视觉 QA 仍未做）。

## 6. 机器状态与注意事项

- **构图不暂停**：msmarco 构建（pid 2388331，约 300 点/秒，RSS 约 936 GB，占用约 60 核）继续跑，
  会与重跑争抢 CPU/内存；RQ 重跑前若要降噪，只能先与用户确认后再降线程或暂停。
- 不启动第二个 03/05 队列实例；不在正式运行期间重建二进制。
- 旧 TAG `aligned_20260921` 的完成情况：gist 03+05、agnews 03+05、dbpedia 03 已完成；
  dbpedia 05 未完成（已标 abandoned）。
- 磁盘/环境：仓库 `/home/kai3/coco/quantization_graph`，宿主机操作（ps/kill/numactl/O_DIRECT）
  需要 `sandbox_permissions=require_escalated`；沙箱 PID 命名空间看不到宿主进程。
- **git 索引状态**：本仓 HEAD 里 `baselines/diskann` 曾经包含本地改动，且这些改动此前已被 `git add`
  （`git status` 里能看到 `M `/`A ` 之类的暂存项）。这次恢复改的是工作树，**没有动暂存区，也没有提交**；
  将来若要提交，请先 `git add -A baselines/diskann baselines/patches baselines/DEPENDENCY_LOCK.json
  baselines/diskann.manifest.json scripts/verify_diskann_baseline.py`
  并用 `python scripts/verify_diskann_baseline.py` 复核，再一次性提交（不要用 `git checkout/reset` 清工作树）。

## 7. 新对话第一步操作清单

## 8. 图重做（2026-09-22 17:2x，nature-figure 技能）

用户反馈"表无法凸显 Ours 的优势"。按 nature-figure 技能（后端已保存为 python）重做，病根有两条：

1. **x 轴被弱 baseline 拉宽**：旧脚本按"所有方法的最小 recall"设 `xlim`，Glass(49%)/Symphony(53%)
   把横轴拉到 ~38%，而 Ours/DiskANN 从 68/72% 才开始 → 90–99% 这段只占图宽 10%。
2. **QPS 用对数轴**：3–3.6× 的差距在对数轴上不到 0.5 个数量级，视觉上被压平。

新设计（`scripts/plot_gist_formal_recall_qps.py` 重写，技能 Pattern 4/15），已按用户两轮反馈定稿：

- **x 轴窗口统一 80–100% Recall**（两轮反馈：先"看不出优势"，再"左轴刻度要一致"）：
  (a)(c) 都用**线性 QPS**、同一刻度，不再一图线性一图对数；
- (a) 英雄面板：Ours vs DiskANN，95% 门槛虚线 + 双向箭头标 3.0×；
- (b) 同 Recall 加速比条形图（90/93/95/97/98% 门槛，按"首个达到该门槛的 width"，不插值），
  明确标出 Ours 达不到的 ≥99% 区间（重排上限 100）；
- (c) Ours / DiskANN / SymphonyQG 三方法同窗口；
- **Glass-NSG 不进主图**（其 recall 只到 79.6%，与 80% 窗口无关），但**数据一行不删**：
  它的 9 个点仍在 source CSV，并保留在 SI 图 `system_qps_recall.log_si.*`（四方法、全 recall 段、对数轴）。
  主图绘 27/36 点，SI 绘 36/36；provenance.json 里写明了这一点。
- 配色改为技能调色板里饱和分明的四色（Ours 蓝 `#0F4D92`、DiskANN 红 `#B64342`、
  SymphonyQG 青 `#42949E`、Glass 紫 `#9A4D8E`），全部实线 + 不同标记，虚线只留给门槛线。
- 尺寸 180×72 mm、最小字形 6.6 pt、SVG/PDF 可编辑文本、TIFF 600 dpi、PNG 300 dpi，
  同时产出 source.csv / caption.md / provenance.json。
- 技能自检：`validate_figure.py` → 17 pass / 3 warn / 0 fail（READY FOR VISUAL QA）；
  `audit_pdf_text.py` → PASS（低于 5 pt 的文本 0 处）；已按技能要求做最终尺寸目检，
  修掉了 3 处碰撞（标注压门槛线、直接标签出轴、说明框压面板字母），结果记在 figures/QA.md。
- 05（内存预算）保持单面板线性 QPS，同样用技能规范重画。

**同一批数据下 Ours 的优势（gist）**：90% → 1.39×、93% → 1.73×、95% → 2.98×、97% → 3.60×、
98% → 3.03×；≥99% Ours 不可达（上限 98.7%），DiskANN 到 99.8%。

**03 重测的复用流程（脚本化，供 agnews/dbpedia 复用）**

`assemble_reused_03_table.py`：把四个方法的 artifact 摊平 → 用 harness 自己的
`validate_layer_completeness` 校验 → 写 `tables/formal_test_rows.csv` + frontier + manifest，
manifest 里带 `reused_artifacts`（复用方法、artifact 路径、sha256、理由）与 `assembly_note`。
`render_03_dataset.py`：备份旧表图 → 调 summarizer/plotter → 跑技能自检 → 写 QA.md/合同。

由于 run 配置里冻结了方法集，改过的方法要用**专用 run-id**（`<ds>_aligned_20260921_03_pair`，
只含 Ours-Disk + DiskANN-PQ-Disk），再 `--pair-run-id` 指向它组装：见
`results/03_disk_system/agnews/agnews_aligned_20260921_03_pair/`。

**每数据集 03 的状态**

| 数据集 | Ours | DiskANN | Glass | Symphony | 表/图 |
| --- | --- | --- | --- | --- | --- |
| gist | ✅ 重测 | ✅ 重测 | ♻️ 复用 | ♻️ 复用 | ✅ 17:29 重画 |
| agnews | ⏳ pair run | ⏳ pair run | ♻️ 复用 | ♻️ 复用 | 待组装 |
| dbpedia | 待 | 待 | ♻️ 复用 | ♻️ 复用 | 待组装 |

05 只有 Ours：gist/agnews/dbpedia 的 1/2/4/8 GiB 用 `run_aligned_03_05.py --tag aligned_20260921
--experiments 05_memory_budget` 正常跑（05 的 run 配置本来就是 Ours-only，无方法集冲突）。

## 9. 重要发现：AGNews 上 DiskANN 反而快 8–14×（2026-09-22 17:4x）

同设计出图后，agnews 03 的 (b) 面板给出 **DiskANN 在 90–98% 门槛上快 7.9–13.5×**
（95%: 10.3×、97%: 10.6%、98%: 7.9%），且 DiskANN 的 recall 上限 99.9% vs Ours 99.4%。
这与 GIST 相反（GIST 上我们快 1.4–3.6×）。数字来自今天 pair run 的正式测量（formal_ready），
不是旧数据，也不是构建差异（对齐编译后 Ours 只快了 10–25%）。

机制（来自同一批 artifact）：

| | Ours（agnews） | DiskANN（agnews） |
| --- | --- | --- |
| 每次读代价（w10→w580） | **0.27–1.84 ms** | **0.147–0.34 ms** |
| 读次数/查询（w10→w580） | 67.8 → 832 | 27.6 → 1026 |

- DiskANN 的导航完全靠常驻 8-bit PQ 码（AGNews 394 MB），只读图扇区；它的工作集在这台设备的
  缓存里基本驻留 → 每次读 ≈ 0.15 ms（近似命中）。
- 我们为每个 gate 幸存者读 4-bit 载荷页，页面来自 1.05 GB 文件、每页只放 3 条记录（≈29% 有效字节），
  命中不到 → 每次读 0.27–1.84 ms（真实随机读）。
- GIST 上反过来：DiskANN 的 8.27 GB 索引工作集缓存不住（每次读 1.1–1.4 ms），我们的页更便宜
  （0.28–0.65 ms），于是中高 recall 段我们领先。

**结论（写论文时不能回避）**：结果强烈依赖"baseline 的工作集能否驻留在存储缓存里"。
在上面两种极端之间，谁的读更便宜谁就赢；这不是协议不公，而是两种内存/布局设计的直接后果。

**可选后续（待用户决定）**：
1. 按现状同时报告两个数据集（诚实，结论表述为 workload-dependent）。
2. 做"驻留消融"：同一 4 GiB 预算下把 4-bit 载荷常驻（AGNews 只需 407 MB），预计可消掉大部分载荷读——
   这是 AGNews 上最值得做的实验（我们的 `full4-resident/no-gate` 消融已有代码支持）。
3. 读路径优化：compact/residual 分页（3 → 7 条/页）、扩大热缓存、libaio → io_uring 对照。

1. 读本文件 + `实验交接文档.md` + `results/diagnostics/aligned_20260921/status.json`（当前队列状态）。
2. 宿主 `ps` 确认：队列在跑（`run_aligned_03_05.py --tag aligned_20260921`）、无第二个实例；
   msmarco builder 仍在跑；无 `T` 状态遗留进程。
3. 若队列已停止：看 `/tmp/aligned_03_05_rerun20260922.log` 末尾与
   `results/diagnostics/aligned_20260921/<run_id>/run.log` 找真正错误（忽略旧日志里的历史失败），
   修好后用同一命令恢复（脚本支持断点续跑；注意"不许覆盖"类保护需要在**恢复前**再次清空对应文件，
   做法见第 5.3 节表格，存档目录 `…/superseded_measurements_aligned_20260921/`）。
4. 队列完成后：核对每数据集 03=36 行 / 05=36 行与 `formal_ready`，检查新 PDF/PNG，补视觉 QA；
   执行第 5.3 节的两项核对（尤其索引 manifest 哈希是否变化）。
5. 任何时候要重建二进制：先跑 `python scripts/verify_diskann_baseline.py`，再
   `python scripts/write_05_ports_local.py`，并确认没有正式队列在跑。
