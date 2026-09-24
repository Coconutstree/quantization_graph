# 磁盘实验交接（2026-09-22 · DiskANN 官方化 + 03/05 同 TAG 重跑）

> **新对话先读本文件。** 本文自包含：读完可以直接接手。实时状态以
> `results/diagnostics/aligned_20260921/status.json`、`/tmp/*.log` 和宿主机进程为准；
> 本文写于 2026-09-22 18:0x，之后发生的变化请以文件为准。过程流水账在
> `docs/analysis/diskann_restore_worklog_20260922.md`（不必先读）。

## 0. 30 秒摘要

- 目标：把 DiskANN baseline 从"被本地改过的 vendored 树"恢复成**官方代码**，把 03 端口移出树外，
  两侧编译选项对齐，然后在**同一个 TAG `aligned_20260921` 里就地覆盖**重跑 03/05，
  并让没变的方法（Glass-NSG、SymphonyQG）复用旧测量。
- 已完成：官方化改造 + 验收（等价闸门、准入测试）；**gist 03** 与 **agnews 03** 已重测并出表出图。
- 正在跑：**dbpedia 03 的 pair run**（只测 Ours-Disk + DiskANN-PQ-Disk，17:50 起；Ours 已完成，
  DiskANN 测量中）。
- 还没做：dbpedia 03 的组装+出图；三个数据集的 **05**（1/2/4/8 GiB，只有 Ours）。
- 一个关键发现（会影响论文表述）：**跨数据集的绝对 QPS 不可比**——AGNews 上 DiskANN 快 8–14×，
  GIST 上我们快 1.4–3.6×，原因主要是"各自的索引文件这次有没有被留在设备缓存里"，不是算法差异（详见第 3.3 节）。

## 1. 下一步该做什么（照抄命令）

### 1.1 dbpedia 03 pair run 跑完后：组装 + 出图 + 标记

```bash
cd /home/kai3/coco/quantization_graph
python scripts/assemble_reused_03_table.py --dataset dbpedia \
    --pair-run-id dbpedia_aligned_20260921_03_pair      # 用 harness 自己的完整性校验拼 4 方法表
python scripts/render_03_dataset.py --dataset dbpedia   # 备份旧图 → 重画 → 技能自检 → 写 QA/合同
```

然后把这轮 job 标完成（脚本已对 gist/agnews 做过同样操作，照抄即可）：
把 `results/diagnostics/aligned_20260921/status.json` 里
`dbpedia_aligned_20260921_03_ram4` 的 `status` 改成 `completed`、`formal_ready: true`、
`figure_status` 填 `exported; automated QA passed; visual inspection done`，并加一条
`assembly_note` 说明 Ours/DiskANN 来自 pair run、Glass/Symphony 复用。

### 1.2 三个数据集的 05（Ours-only，走正常队列）

```bash
cd /home/kai3/coco/quantization_graph
setsid nohup python scripts/run_aligned_03_05.py --tag aligned_20260921 \
    --experiments 05_memory_budget >> /tmp/aligned_03_05_05only.log 2>&1 < /dev/null &
```

必须用**宿主执行**（`sandbox_permissions=require_escalated`，`yield_time_ms=1000`）。
05 只有 Ours，它的 run 配置本来就是 Ours-only，不会撞第 4.2 节的"方法集冻结"问题。

## 2. 为什么要做这一轮（官方化）

`baselines/diskann` 之前**不是**官方代码：相对 pinned commit
`3218478b5f7d1721840c29309e22d9fd74b1b4bc`（官方 DiskANN3 v0.55.0）偏离约 20 个源码文件。
逐类偏差与处置：

| 类别 | 内容 | 处置 |
| --- | --- | --- |
| 结构重构 | `diskann-disk/src/build/**` 被拍平、上游 397 行的 builder 被换成 ~30 行 stub、Microsoft 版权头被删 | **撤销**，恢复官方结构与版权头 |
| 构建注入 | `diskann-disk/Cargo.toml` 加 `serde_json.workspace` + `[[bin]] qgraph05_diskann_port` 指向我们的 main.rs | **撤销**，端口移到树外独立 crate |
| 内部 API 放宽 | `inmem/{mod,scalar,spherical}.rs`、`fast_memory_quant_vector_provider.rs`、`spherical/quantizer.rs` 放宽可见性/新增访问器 | 保留为**声明式补丁 01**（02/05B 端口依赖） |
| 仪表 | `diskann/src/graph/{mod,index}.rs` 建图距离计数；`kmeans/{mod,lloyds,plusplus}.rs` PQ 训练距离计数 | 保留为**声明式补丁 02**（只影响上报指标） |
| PQ 越界守卫 | `fixed_chunk_pq_table.rs` 越界 `fill(0)` 代替 panic | **丢弃**（唯一可达路径的 id 天然在范围内；保留反而是"静默填零"隐患） |
| io_uring 加固 | `aligned_file_reader/reader/linux.rs` 完成队列/短读处理 + 新增回归测试 | 保留为**声明式补丁 03** |

现在的状态：官方树 + 3 个补丁（12 个文件），由 `scripts/verify_diskann_baseline.py` +
`baselines/diskann.manifest.json`（1270 个官方文件哈希）+ `baselines/DEPENDENCY_LOCK.json` 强制校验；
构建脚本 `scripts/build_formal_local.sh` 已把它作为前置步骤。

## 3. 协议与公平性（必须理解，否则会误读结果）

### 3.1 四个方法一致的开关

32 workers、CPU `0,4,…,124`、NUMA node 0、4 GiB 进程预算、`cache-mode=standard`、
同一 test split（800 查询）、同一 query order 文件、100 条 warmup（四个方法都是 **32 线程并行**）、
9 档 width（10/20/40/60/100/160/240/400/580）、K=10、1 repeat；**measured 进程一律
`--parity-mode external`**（计时进程里不跑内存 parity），`internal` parity 只在独立的
reference 验收进程里跑。Ours/DiskANN 用 beam=4；Glass/Symphony 保留各自原生 beam=1。

### 3.2 官方性已证

等价闸门：官方树二进制与改造前二进制在 agnews w10/w40 上 `result_ids`、recall、
`io_requests`、距离计算次数**逐条一致**（证据 `results/diagnostics/baseline_restore_20260922/equivalence_gate.json`）；
准入测试 5/5 通过。

### 3.3 核心发现："货架效应"让跨数据集 QPS 不可比

同一方法、同一代码路径，`每次读`的代价：

| 阶段 | AGNews | GIST |
| --- | --- | --- |
| 冷（reference，parity 前） | 2.1–3.3 ms | 3.8–4.4 ms |
| 热（measured） | **0.074–0.13 ms** | **0.95–1.34 ms** |

冷态两边一样（≈介质真实随机读），热态差 ~10×：读请求数、节点加载数、邻居 ID 散布、
顺序扫描吞吐在两个数据集上几乎相同，唯一差异是**各自的索引文件这次有没有被留在设备缓存里**。
而"留没留下"取决于文件大小 vs 控制器缓存（AGNews DiskANN 索引 6.0 GB 装得下、GIST 7.8 GB 装不下），
以及 reference 阶段每档 parity 会把整份索引顺序读一遍（等于往货架上摆）。

后果：

- **同一数据集内**的比较有效（同会话、同设备、同协议、两方法同时测）。
- **跨数据集**的绝对 QPS 不可直接比较。
- AGNews 上 DiskANN 在 90–98% recall 门槛快 **7.9–13.5×**（95%: 10.3×），recall 上限 99.9% vs 我们 99.4%；
  GIST 上我们快 **1.4–3.6×**，但 ≥99% 只有 DiskANN 能到。两个都要如实报告。
- 协议本身已声明 `device_cache_controlled=false`、`cold_storage_claim=false`。

## 4. 重跑必须知道的机制（踩过的坑）

### 4.1 五类"不许覆盖"保护

就地覆盖重跑时，harness 会依次拦下来；做法都是**先存档再移走**（不要改校验代码、不要改已测证据）：

| 触发信息 | 需要清掉/改掉什么 |
| --- | --- |
| `run configuration changed; use a fresh run-id` | `results/manifests/<run_id>/protocol.json` 与 `invocation_*.json` 里的 `ports_registry_sha256` 必须等于当前 `src/disk_bench/ports.local.json` 的哈希 |
| `phase invocation changed` | 同上（invocation 文件同样参与严格相等比较，**不要加额外字段**） |
| `fixed parameter lock changed; refusing to retune` | 先存档再删除 `<run_id>/manifests/fixed_parameters.lock.json` |
| `hot-record profile mismatch: native_binary_sha256` | 先存档再移走 Ours 的 `raw/Ours-Disk/validation/*.hot_profile` |
| `refusing to overwrite existing attempt` | 先存档再移走该方法 `raw/<method>/test/` 下除 `.reference` 外的所有文件；`.reference` 目录是独占创建，也必须先移走 |

存档目录约定：`results/diagnostics/baseline_restore_20260922/superseded_measurements_aligned_20260921/`。

### 4.2 改 Ours 二进制会连带作废同数据集的其它方法

编排器给**每个方法**都传 `--tuning-lock <dataset>/manifests/fixed_parameters.lock.json`，
而该 lock 含 Ours 热 profile 的哈希。**Ours 二进制一变 → profile 变 → lock 变 → 四个方法的
artifact 全部失效**（报 `resource evidence: reference input changed`）。
所以：**本轮起冻结 Ours 二进制**，不要再重建；要改端口代码，先等当前 TAG 跑完。

另外：run 配置里冻结了方法集，**同一 run-id 不能用不同 `--methods` 重进**（会报
`run configuration changed`）。因此"只重测 Ours/DiskANN"必须换一个专用 pair run-id
（`<dataset>_aligned_20260921_03_pair`），再在组装时指回标准目录。

### 4.3 二进制 sha 不可复现

Rust 重新编译后 Ours 二进制哈希会变（即使源码相同）→ artifact 依 sha 绑定 → 必须重测。

## 5. 复现命令

```bash
# 0) 基线自检（任何时候重建二进制后都要先跑）
python scripts/verify_diskann_baseline.py
python scripts/write_05_ports_local.py        # 重算 registry sha，再照 4.1 更新 protocol/invocation

# 1) 只重测两个改动过的方法（换 pair run-id，必须宿主执行 + numactl PATH）
python - <<'PY'
import json
from pathlib import Path
ds = 'dbpedia'                     # 换成要跑的数据集
src = json.loads(Path(f'results/diagnostics/aligned_20260921/{ds}_aligned_20260921_03_ram4/command.json').read_text())
src[src.index('--methods') + 1] = 'Ours-Disk,DiskANN-PQ-Disk'
src[src.index('--run-id') + 1] = f'{ds}_aligned_20260921_03_pair'
Path(f'/tmp/{ds}_03_pair.json').write_text(json.dumps(src, indent=1))
print('wrote', f'/tmp/{ds}_03_pair.json')
PY
setsid nohup env PATH="/home/kai3/coco/quantization_graph/work/tools/numactl/root/usr/bin:$PATH" \
  python -c "import json,subprocess,sys; cmd=json.load(open('/tmp/dbpedia_03_pair.json')); sys.exit(subprocess.run(cmd, cwd='/home/kai3/coco/quantization_graph').returncode)" \
  >> /tmp/dbpedia_03_pair.log 2>&1 &

# 2) 组装 4 方法表（harness 自己的 flatten + validate_layer_completeness + manifest）
python scripts/assemble_reused_03_table.py --dataset <ds> --pair-run-id <ds>_aligned_20260921_03_pair

# 3) 出图（备份旧图 → summarizer + plotter → 技能自检 → QA/合同）
python scripts/render_03_dataset.py --dataset <ds>

# 4) 05（Ours-only，正常队列）
setsid nohup python scripts/run_aligned_03_05.py --tag aligned_20260921 \
  --experiments 05_memory_budget >> /tmp/aligned_03_05_05only.log 2>&1 < /dev/null &
```

## 6. 结果与证据在哪

| 内容 | 路径 |
| --- | --- |
| 队列状态 | `results/diagnostics/aligned_20260921/status.json`（+ `status.paused_20260922.json` 暂停快照） |
| 每方法原始产物 | `results/{03_disk_system,05_memory_budget}/<ds>/<run_id>/raw/<method>/test/` |
| 数据集表格 | `results/<exp>/<ds>/tables/{formal_test_rows.csv,...}` |
| 论文图 | `results/<exp>/<ds>/figures/`（主图 + SI + source.csv + caption.md + provenance.json + QA.md） |
| 官方化证据 | `results/diagnostics/baseline_restore_20260922/`（before 快照、偏差清单、二进制哈希、等价闸门、索引哈希） |
| 被覆盖的旧测量 | `…/superseded_measurements_aligned_20260921/`（measurement_files / references / hot_profiles / guards / tables） |
| 过程流水账 | `docs/analysis/diskann_restore_worklog_20260922.md` |

## 7. 图表规范（nature-figure 技能，Python 后端已保存）

- 主图 `system_qps_recall.{pdf,svg,png,tiff}`：180×72 mm 三面板
  **(a)** Ours vs DiskANN 英雄面板（线性 QPS、95% 门槛虚线 + 双向箭头标加速比）；
  **(b)** 同 Recall 门槛的**方向自适应**加速比条形图（谁快标谁，蓝=Ours、红=DiskANN），
  并标注两边 recall 上限；**(c)** Ours/DiskANN/SymphonyQG 同窗口同轴。
  **x 轴统一 80–100% recall**；**Glass-NSG 不进主图**（recall 只到 79.6%），
  它的 9 个点保留在 source CSV 与 SI 图 `system_qps_recall.log_si.*`（四方法、全 recall、对数轴）。
  主图 27/36 点，SI 36/36 点，provenance.json 里写明。
- 05 图 `memory_qps_recall.*`：单面板线性 QPS，四个预算。
- 每次出图都要跑：`validate_figure.py`（源预检）+ `audit_pdf_text.py`（PDF 字形 ≥5 pt）
  + **最终尺寸逐面板目检**（自动检查不能替代），结果写进 `figures/QA.md`。

## 8. 未决问题与建议的下一步

1. **缓存受控/重复实验**（建议优先）：同数据集内 A-B-A 交错 3 次（例如 gist 与 agnews 各在
   85%/95% 两个 recall 点），报中位数与波动 → 让小差异（±25% 内）也可辩护。
2. **驻留消融**：同一 4 GiB 预算下把 4-bit 载荷常驻（AGNews 只需 407 MB，我们目前只驻留 98 MB 的
   1-bit 码）→ 直接回答"AGNews 的差距有多少来自我们选择分页"。
3. **读路径优化**（可选）：compact/residual 分页（现在每页 3 条记录、约 29% 有效）、扩大热缓存、
   libaio → io_uring 对照。
4. **协议顺序**：是否把 reference 验收挪到 measured 之后（或测量前显式清缓存），让测量起点对所有
   方法都接近"冷"，从而消除第 3.3 节的货架效应。代价：协议变更 → 需重跑。
5. 05 跑完后要核对：每数据集 05=36 行、`formal_ready`、图表重生成；03 的 36 行与 pair-run 来源一致。

## 9. 环境与踩坑清单

- 仓库 `/home/kai3/coco/quantization_graph`；沙箱只允许写仓库与 `/tmp`；**ps/kill/numactl/O_DIRECT/
  io_uring 都必须宿主执行**（`sandbox_permissions=require_escalated`），沙箱 PID 命名空间看不到宿主进程。
- 队列工具：`scripts/run_aligned_03_05.py --tag … [--datasets …] [--experiments …]`；启动前会断言
  "registry sha == 磁盘二进制"。
- 机器上**还有 msmarco 图构建**（tmux `msmarco_graphs_20260915`，占 ~60 核 / ~1 TB 内存）在跑，
  本轮所有测量都在它的竞争下完成 → 不要再叠加并发测量任务。
- 不要启第二个 03/05 队列实例；不要在正式测量期间重建二进制。
- 旧 TAG 的历史数字只读保留；本轮所有新数字写在 `aligned_20260921` 下。
