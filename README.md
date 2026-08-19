# quantization_graph — Ours-DiskANN（VLDB 2027）

面向论文 **Ours-DiskANN** 的可复现仓库：一种 4-bit 全量化对称 Vamana 图索引方法
（ExRaBitQ4），附带三个实验套件与三个数据集（DBpedia-1M / GIST-1M / AGNews）的
端到端复现脚本。克隆后按本文档安装依赖、编译并运行，即可复现论文全部图表。

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

三个实验按协议（见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md`）分层隔离：

- **实验一（01_quantizer_fair）**：4-bit 量化器公平。共享固定候选
  （`fixed_candidates_k1000`），比较 PQ / SQ / SAQ / Ours_K1 的量化距离误差、
  Recall@10 与压缩距离核吞吐。
- **实验二（02_diskann_fair）**：载荷公平。同一构图协议（R=`M` / L=`L`），
  为 PQ / SQ / SAQ / Ours 各自构建 4-bit 量化图并扫描。
- **实验三（03_system_fair）**：端到端系统公平。Ours / SymphonyQG / OG-LVQ /
  Glass-NSG 使用**同参数固定配置**（不做验证自动选参），直接对比 Recall-QPS、
  延迟与索引。

**固定参数**（默认；可通过 `M` / `L` 环境变量覆盖，四个系统始终同参数）：

| 系统 | 固定配置 |
|---|---|
| Ours | M=`M`，L_build=`L`，alpha=1.2，refine1 + cap256 + back-stop2，paper-prune+sidecar，residual4 rerank(top-100) |
| SymphonyQG | R=`M`，EF=`L`，iters=3 |
| OG-LVQ | R=`M`，W=`L`，alpha=1.2，LVQ4（4 bit/dim） |
| Glass-NSG | R=`M`，L=`L`（构图 beam=`L`），ef 全扫描 |

数据集：DBpedia-1M（1536-d）、GIST-1M（960-d）、AGNews（1024-d）；查询单线程计时，
构图 64 线程。查询切分约定：前 N 条为验证集、其余为测试集（agnews/gist N=200，dbpedia N=1000）。

## 3. 目录结构（Layout）

```text
.
├── Ours/                  # 方法实现与统一入口（core/hnswlib、run_ours.py、tests）
├── experiments/
│   ├── 01_quantizer_fair/ # 实验一：4-bit 量化器公平（C++ + 扫描）
│   ├── 02_diskann_fair/   # 实验二 + 方法实现（Rust + native bridge）
│   └── 03_system_fair/    # 实验三：端到端系统公平（Python adapters）
├── baselines/diskann/     # DiskANN3（vendored）
├── scripts/               # 构建、复现、数据、表格与绘图脚本
├── data/                  # 数据转换脚本与说明（原始数据不提交）
├── requirements.txt       # Python 依赖（pip）
├── environment.yml        # conda 一键环境（含 cmake/g++/Rust）
├── BASELINE_EXPERIMENT_PLAN_MS_V2.md  # 实验协议与基线锁定版本
├── NOTICE.md / LICENSE    # 第三方组件与 Apache-2.0
```

结果布局（按套件分顶层，`raw` 统一为 `logs`）：

```text
results/
├── 01_quantizer_fair/<dataset>/{csv,logs,manifests}
├── 02_diskann_fair/<dataset>/{csv,logs,indexes,manifests}
├── 03_system_fair/<dataset>/{csv,logs,indexes,figures,manifests,audit}
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
- SAQ：第三方仓库锁定 commit，按计划文档构建 `test_fixed_candidates`
  （依赖 glog/fmt 等，见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md`）；
- SymphonyQG 绑定：从官方仓库锁定 commit 构建，运行时以 `SYMPHONYQG_PYTHONPATH` 指定；
- 数据集（私有数据不提交、不外链）：公开数据集用 `bash scripts/download_data.sh` 下载；
  私有/自定义数据集请自行放入 `data/<dataset>/`（格式见 `data/README.md`：
  `<dataset>_base.fvecs`、`<dataset>_query.fvecs`、`<dataset>_groundtruth.ivecs`）。

## 5. 编译（Build）

```bash
# 02：run_diskann_fair（需 Rust 工具链；--offline 可用本地 crate 缓存）
cd experiments/02_diskann_fair && cargo build --release --offline

# 01：faiss_quantizer_smoke / faiss_hard_negative_candidates
cmake -S experiments/01_quantizer_fair -B build/01_quantizer_fair   -DCMAKE_BUILD_TYPE=Release
cmake --build build/01_quantizer_fair -j 16

# SAQ：test_fixed_candidates
cmake -S baselines/saq -B baselines/saq/build_gcc11 \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_UNIT_TESTS=OFF \
  -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-/usr}"   # 依赖默认走系统（apt: libfmt/glog/gflags/gtest）
cmake --build baselines/saq/build_gcc11 --target test_fixed_candidates -j 16
```

## 6. 复现（Reproduction）

一键跑批（01 → 02 → 03，三个实验；数据集可选，`M`/`L` 可选）：

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

## 7. 结果与产物（Outputs）

- 表格：`results/<suite>/<dataset>/csv/`（`system_fair_median.csv` 等）；
- 原始日志：`results/<suite>/<dataset>/logs/`；
- 索引：`results/<suite>/<dataset>/indexes/`；图：`results/03_system_fair/<dataset>/figures/`；
- 审计：`results/03_system_fair/<dataset>/audit/`；汇总表：`results/paper_tables/`；
- 运行日志：`logs/round2.*`（总状态 `logs/round2.status`）。

## 8. 引用与许可（Citation & License）

- 第三方组件与许可见 `NOTICE.md`；基线锁定版本见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md`；
- 仓库采用 Apache-2.0（见 `LICENSE`）。论文引用信息待定稿后补充。
