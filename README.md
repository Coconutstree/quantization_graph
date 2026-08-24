# quantization_graph — Ours-DiskANN（VLDB 2027）

面向论文 **Ours-DiskANN** 的可复现仓库：一种 4-bit 全量化对称 Vamana 图索引方法
（ExRaBitQ4），附带 01/02/03 内存实验、05 磁盘系统公平实验，以及三个数据集
（DBpedia-1M / GIST-1M / AGNews）的端到端复现脚本。克隆后按本文档安装依赖、
编译并运行，即可复现论文实验表格与图表；05 需要独占本地 NVMe/SSD 和正式 native
disk ports，单独运行。

## 1. 方法（Method）

Ours-DiskANN 由四部分组成：

| 组件 | 说明 |
|---|---|
| 4-bit 载荷 | ExRaBitQ4（block16）：4 bit/dim 主码 + residual-4 元数据，无 fp32 底库 |
| 图 | DiskANN3 / Vamana，`M` 度、`L_build` 构图 beam（默认 M=64、L=400） |
| 构图优化 | refine_passes=1、build_prune_cap=256、build_early_stop_hops=2（对称 4-bit 距离构图） |
| 查询 | paper-prune + sidecar 剪枝估计，residual4 block16（mse/fp16）top-100 重排 |

统一入口：`Ours/experiments/run_ours.py`（`--M 32,64`）；核心算法在 `Ours/core/hnswlib/`。

## 2. 实验设计（Experiments）

01/02/03 三个内存实验按协议（见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md`）分层隔离：

- **实验一（01_quantizer_fair）**：4-bit 量化器公平。共享固定候选
  （`fixed_candidates_k1000`），比较 PQ / SQ / SAQ / Ours_K1 的量化距离误差、
  Recall@10 与压缩距离核吞吐。
- **实验二（02_diskann_fair）**：载荷公平。同一构图协议（R=`M` / L=`L`），
  为 PQ / SQ / SAQ / Ours 各自构建 4-bit 量化图并扫描。
- **实验三（03_system_fair）**：端到端系统公平。Ours / SymphonyQG / OG-LVQ /
  Glass-NSG 使用**同参数固定配置**（不做验证自动选参），直接对比 Recall-QPS、
  延迟与索引。
- **实验五（05_disk_system_fair）**：磁盘系统公平。对应 `DISK_SYSTEM_EXPERIMENT_PLAN.md`，
  在同一独占本地 NVMe/SSD 上运行 05A/05B/05C 三层证据链：固定候选量化载荷 I/O、
  共享图磁盘机制实验、五系统端到端磁盘对比。05 只改变存储后端，保留 01/02/03 的
  图、codec、距离核、搜索循环和参数语义。

**固定参数**（默认；可通过 `M` / `L` 环境变量覆盖，四个系统始终同参数）：

| 系统 | 固定配置 |
|---|---|
| Ours | M=`M`，L_build=`L`，alpha=1.2，refine1 + cap256 + back-stop2，paper-prune+sidecar，residual4 rerank(top-100) |
| SymphonyQG | R=`M`，EF=`L`，iters=3 |
| OG-LVQ | R=`M`，W=`L`，alpha=1.2，LVQ4（4 bit/dim） |
| Glass-NSG | R=`M`，L=`L`（构图 beam=`L`），ef 全扫描 |

数据集：DBpedia-1M（1536-d）、GIST-1M（960-d）、AGNews（1024-d）；查询单线程计时，
构图 64 线程。查询切分约定：前 N 条为验证集、其余为测试集（agnews/gist N=200，dbpedia N=1000）。

**SymphonyQG 基线说明（重要）**：官方 AVX512 FastScan 路径
（`symqglib/qg/qg_scanner.hpp`）用 `_mm512_cvtepi16_epi32` 对 uint16 累加结果做
**符号扩展**。当补零后的维度 ≥ 2048（即原维度 > 1024，如 DBpedia 1536→2048）时，
查询侧 6-bit 点积 Σ(q̃·code) 常超 32767，被当成负数，量化导航距离大面积失真，
DBpedia 端到端 Recall@10 仅约 0.46。将两处 `cvtepi16_epi32` 改为
`cvtepu16_epi32`（零扩展）后，同一索引端到端召回 0.46→0.88（ef=580），
逼近其图 fp32 上限 0.89。本仓库 03 表格/图中 SymphonyQG 的 DBpedia 行即采用
修复后实测值（`results/03_system_fair/dbpedia/csv/`），修复前的归因实验
（PCA-960 恢复 0.997）仍可复现，但机制是实现缺陷而非量化算法或数据问题。

**实验五正式磁盘约束（重要）**：05 的正式结果不能来自 Python/NumPy smoke runner，
也不能回退到 buffered I/O、相邻 checkout 或 `/tmp` binding。所有正式 05 结果必须
通过 `experiments/05_disk_system_fair/orchestrator.py` 的 schema-2 contract 校验：
native port 状态为 `ready`、二进制 SHA-256 固定、4 KiB `O_DIRECT` 和 native async I/O
必需、数据/查询划分/图文件都有 manifest hash，正式 test 阶段固定 5 次重复。

## 3. 目录结构（Layout）

```text
.
├── Ours/                  # 方法实现与统一入口（core/hnswlib、run_ours.py、tests）
├── experiments/
│   ├── 01_quantizer_fair/ # 实验一：4-bit 量化器公平（C++ + 扫描）
│   ├── 02_diskann_fair/   # 实验二 + 方法实现（Rust + native bridge）
│   ├── 03_system_fair/    # 实验三：端到端系统公平（Python adapters）
│   ├── 04_query_codec_1bit_scan/ # 查询 codec 消融
│   └── 05_disk_system_fair/ # 实验五：正式磁盘公平实验（native ports + orchestrator）
├── baselines/diskann/     # DiskANN3（vendored）
├── scripts/               # 构建、复现、数据、表格与绘图脚本
├── data/                  # 数据转换脚本与说明（原始数据不提交）
├── requirements.txt       # Python 依赖（pip）
├── environment.yml        # conda 一键环境（含 cmake/g++/Rust）
├── BASELINE_EXPERIMENT_PLAN_MS_V2.md  # 实验协议与基线锁定版本
├── DISK_SYSTEM_EXPERIMENT_PLAN.md     # 05 磁盘系统公平实验协议
├── NOTICE.md / LICENSE    # 第三方组件与 Apache-2.0
```

结果布局（按套件分顶层，`raw` 统一为 `logs`）：

```text
results/
├── 01_quantizer_fair/<dataset>/{csv,logs,manifests}
├── 02_diskann_fair/<dataset>/{csv,logs,indexes,manifests}
├── 03_system_fair/<dataset>/{csv,logs,indexes,figures,manifests,audit}
├── 05_disk_system_fair/runs/<run-id>/{manifests,05A_disk_quantizer_io,05B_diskann_shared_graph,05C_disk_system_fair}
└── paper_tables/
```

## 4. 依赖与安装（Requirements）

```bash
# Python 运行时
pip install -r requirements.txt
# 或一键 conda 环境（Python + cmake + g++ + Rust + pip 依赖）
conda env create -f environment.yml && conda activate quantization-graph
```

非 pip 依赖一键安装（联网；Faiss / SAQ / SymphonyQG 绑定，数据集可选）：

```bash
bash scripts/setup_deps.sh               # 克隆并编译 Faiss / SAQ / SymphonyQG 绑定
bash scripts/setup_deps.sh --with-data   # 额外下载公开数据集
# 私有数据集：自行放入 data/<dataset>/（见下方数据集说明），不依赖任何本地仓库
```

手动方式（等价于上面脚本）——Python 之外的依赖（非 pip 包）：
- Rust 工具链（构建 `experiments/02_diskann_fair`）；cmake ≥ 3.20、g++（C++17+OpenMP）、BLAS；
- Faiss：`bash scripts/setup_faiss.sh`（pinned commit 源码构建，01 实验链接）；
- SAQ：第三方仓库锁定 commit，构建 `create_index`, `test_qps`, `test_relative_error`；
  glog/fmt 默认从仓库内 `baselines/deps/local` 加载；
- SymphonyQG 绑定：从官方仓库锁定 commit 构建，默认从仓库内
  `baselines/symphonyqg/python` 加载；`SYMPHONYQG_PYTHONPATH` 仅作为显式开发覆盖；
- 数据集（私有数据不提交、不外链）：公开数据集用 `bash scripts/download_data.sh` 下载；
  私有/自定义数据集请自行放入 `data/<dataset>/`（格式见 `data/README.md`：
  `<dataset>_base.fvecs`、`<dataset>_query.fvecs`、`<dataset>_groundtruth.ivecs`）。

公开数据集准备（含 GIST）：

```bash
# 下载并转换 DBpedia-1M / GIST-1M / AGNews，输出到 data/<dataset>/
bash scripts/download_data.sh

# 只准备 GIST-1M 时可单独执行：
mkdir -p data/gist
wget -c http://ann-benchmarks.com/gist-960-euclidean.hdf5 \
  -O data/gist/gist-960-euclidean.hdf5
python data/convert_hdf5_to_ann.py \
  --input data/gist/gist-960-euclidean.hdf5 \
  --output-dir data/gist --prefix gist
python scripts/check_datasets.py --datasets gist --data-root data --out-root results
```

## 5. 编译（Build）

推荐的一键编译入口：

```bash
mkdir -p build/cmake_repro
cd build/cmake_repro
cmake ../..
make -j
```

也可以分目标执行：

```bash
make setup_cpp_deps   # 本地解包 C/C++ 依赖
make setup_deps       # Faiss / SAQ / SymphonyQG
make formal_local     # 01/03/05 native/Rust ports
make write_05_ports   # 生成 05 ports.local.json
make download_data    # 下载并转换公开数据集
```

原始分步命令仍可使用：

```bash
# 02：run_diskann_fair（需 Rust 工具链；--offline 可用本地 crate 缓存）
cd experiments/02_diskann_fair && cargo build --release --offline

# 01：faiss_quantizer_smoke / faiss_hard_negative_candidates
cmake -S experiments/01_quantizer_fair -B build/01_quantizer_fair   -DCMAKE_BUILD_TYPE=Release
cmake --build build/01_quantizer_fair -j 16

# SAQ：create_index / test_qps / test_relative_error
cmake -S baselines/saq -B baselines/saq/build_gcc11 \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_UNIT_TESTS=OFF \
  -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-/usr}"   # 依赖默认走系统（apt: libfmt/glog/gflags/gtest）
cmake --build baselines/saq/build_gcc11 --target create_index test_qps test_relative_error -j 16

# 05：正式磁盘实验 native dependencies / ports（Faiss、SAQ、05 C++ ports、Rust ports）
bash scripts/build_formal_local.sh

# 05：根据本机编译产物重算 binary_sha256，生成正式 port registry
python scripts/write_05_ports_local.py
```

## 6. 复现（Reproduction）

一键跑批（01 → 02 → 03，内存实验；数据集可选，`M`/`L` 可选）：

```bash
# 默认：全部数据集，M=64，L=400
OUT_ROOT=results PYTHON=python3 bash scripts/run_master_round2.sh

# 只跑指定数据集
DATASETS=agnews bash scripts/run_master_round2.sh

# 指定数据集 + 自定义 M/L（03 固定配置自动跟随）
DATASETS="agnews gist" M=32 L=200 OUT_ROOT=results bash scripts/run_master_round2.sh
```

参数：`DATASETS`（空格分隔，默认 agnews gist dbpedia）；`M`（图度，默认 64）；
`L`（构图 beam，默认 400）；`OUT_ROOT`（结果根目录，默认 results）；`PYTHON`（默认 python3）。

单独运行某一步：

```bash
# 01（固定候选 + PQ/SQ/Ours + SAQ + 表格导出）
DATASETS=agnews OUT_ROOT=results bash scripts/run_faiss_quantizer_fair.sh
DATASETS=agnews OUT_ROOT=results bash scripts/run_saq_fixed_candidates_fair.sh

# 02（各方法 4-bit 量化图 + ef 扫描，R=M / L=L）
experiments/02_diskann_fair/target/release/run_diskann_fair \
  --dataset agnews --methods PQ,SQ,SAQ,Ours --max-degree 64 --build-beam 400 \
  --out-root results --repeats 1 --threads 64 --refine-passes 1 \
  --build-prune-cap 256 --build-early-stop-hops 2 \
  --query-path results/03_system_fair/agnews/csv/_query_splits/test_query.fvecs \
  --gt-path results/03_system_fair/agnews/csv/_query_splits/test_gt.ivecs

# 03（固定配置端到端；先由 master 写入固定 selected config，再执行扫描）
python experiments/03_system_fair/run_system_fair.py --dataset agnews \
  --systems Ours,SymphonyQG,OG-LVQ,Glass-NSG --run --repeats 1 --threads 64 --out-root results
```

表格与图（复现论文图表）：

```bash
python scripts/export_paper_quantizer_table.py --datasets dbpedia gist agnews --out-root results --work-root work
python scripts/export_system_fair_table.py --datasets dbpedia,gist,agnews --out-root results
python scripts/plot_vldb2027_figures.py   # 输出 paper/figures/
```

### 6.1 实验五：正式磁盘复现（05A/05B/05C）

05 正式复现按 NVMe 场景编写，需要独占本地 NVMe/SSD 设备路径，不使用原始 `work/05_*_smoke`
产物作为论文结果。正式运行前请确认：

- 数据集已放在 `data/<dataset>/`；
- 01/02/03 已为同一数据集跑过，且 03 query split、02 shared graph / Ours graph 已生成；
- `baselines/DEPENDENCY_LOCK.json` 中的本地依赖存在；
- 已运行 `bash scripts/build_formal_local.sh`；
- 已运行 `python scripts/write_05_ports_local.py` 生成
  `experiments/05_disk_system_fair/ports.local.json`；每次重编译 native ports 后都要重跑该脚本，
  因为 orchestrator 会逐项校验 `binary_sha256`。

GIST-only 最小正式 NVMe 复现链如下，适合先验证一台新机器能完整跑通：

```bash
# 1) 安装依赖和 GIST 数据
pip install -r requirements.txt
bash scripts/setup_deps.sh
mkdir -p data/gist
wget -c http://ann-benchmarks.com/gist-960-euclidean.hdf5 \
  -O data/gist/gist-960-euclidean.hdf5
python data/convert_hdf5_to_ann.py \
  --input data/gist/gist-960-euclidean.hdf5 \
  --output-dir data/gist --prefix gist
python scripts/check_datasets.py --datasets gist --data-root data --out-root results

# 2) 编译 01/02/03/05 所需二进制，并生成本机 ports.local.json
bash scripts/build_formal_local.sh
python scripts/write_05_ports_local.py

# 3) 先跑 GIST 的 01/02/03，生成 05 需要复用的 query split 和 02 图
DATASETS=gist OUT_ROOT=results PYTHON=python3 bash scripts/run_master_round2.sh

# 4) 在独占 NVMe 上跑 GIST 的正式 05
RUN_ID=formal_nvme_gist
PORTS=experiments/05_disk_system_fair/ports.local.json
DISK_ROOT=/mnt/exclusive_nvme/qgraph

python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase doctor --layers all --datasets gist \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "$RUN_ID" --layers all --datasets gist \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase validate --run-id "$RUN_ID" --layers all --datasets gist \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase tune --run-id "$RUN_ID" --layers all --datasets gist \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "$RUN_ID" --layers all --datasets gist \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme \
  --workers 1,16 --repeats 5
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase plot --run-id "$RUN_ID" --layers all --datasets gist
```

正式 05 复现命令如下，所有阶段使用同一个 `RUN_ID`：

```bash
RUN_ID=formal_nvme_20260822
PORTS=experiments/05_disk_system_fair/ports.local.json
DISK_ROOT=/mnt/exclusive_nvme/qgraph

# 只读诊断：工具链、依赖锁、数据、03 query split、02 图、port registry、磁盘 preflight
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase doctor --layers all --datasets agnews,gist,dbpedia \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

# 导出磁盘索引/载荷 artifacts
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "$RUN_ID" --layers all --datasets agnews,gist,dbpedia \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

# validation parity / contract gate
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase validate --run-id "$RUN_ID" --layers all --datasets agnews,gist,dbpedia \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

# validation tuning lock；正式 run 必须先有同 RUN_ID 的 tuning.lock.json
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase tune --run-id "$RUN_ID" --layers all --datasets agnews,gist,dbpedia \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

# 正式 test：workers=1,16，repeats 固定为 5；GIST 自动追加 worker/budget 诊断点
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "$RUN_ID" --layers all --datasets agnews,gist,dbpedia \
  --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme \
  --workers 1,16 --repeats 5

# 聚合 CSV 和 05 图；plot 阶段不需要 disk-root
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase plot --run-id "$RUN_ID" --layers all --datasets agnews,gist,dbpedia
```

只复现某一层或某个数据集时，用 `--layers 05a` / `--layers 05b,05c` 和
`--datasets gist` 缩小范围。正式 README 命令固定使用 `--disk-profile nvme`；
HDD/RAID 不作为本仓库默认复现路径。

## 7. 结果与产物（Outputs）

- 表格：`results/<suite>/<dataset>/csv/`（`system_fair_median.csv` 等）；
- 原始日志：`results/<suite>/<dataset>/logs/`；
- 索引：`results/<suite>/<dataset>/indexes/`；图：`results/03_system_fair/<dataset>/figures/`；
- 审计：`results/03_system_fair/<dataset>/audit/`；汇总表：`results/paper_tables/`；
- 运行日志：`logs/round2.*`（总状态 `logs/round2.status`）。
- 05 正式运行：
  `results/05_disk_system_fair/runs/<run-id>/`，包含 invocation/preflight/fio manifests、
  每个 native artifact、query traces、terminal logs、聚合 CSV 和 SVG/PDF/PNG/TIFF 图。

## 8. 引用与许可（Citation & License）

- 第三方组件与许可见 `NOTICE.md`；基线锁定版本见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md`；
- 仓库采用 Apache-2.0（见 `LICENSE`）。论文引用信息待定稿后补充。
