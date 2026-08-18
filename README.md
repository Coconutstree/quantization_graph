# quantization_graph — Ours-DiskANN 方法与三个实验的可复现仓库

Standalone home of the paper's method **Ours-DiskANN** (ExRaBitQ4 4-bit
symmetric Vamana inside the DiskANN3 framework, paper-pruned search,
residual4 rerank) and its three experiment suites on three datasets
(DBpedia-1M 1536-d, GIST-1M 960-d, AGNews 1024-d). Everything needed to
build and run the method and experiments lives in this repository; raw
dataset files are not committed (see [Datasets](#datasets)).

## Layout

```text
.
├── Ours/
│   ├── core/hnswlib/             # ExRaBitQ4 算法核心（C++ 头文件，vendored）
│   ├── experiments/run_ours.py   # Ours-DiskANN 统一入口（M=32/M=64）
│   ├── config.json               # 正式配置（R=32, L_build=400, K=1, paper-prune, residual4）
│   └── tests/                    # 单元测试
├── experiments/
│   ├── 01_quantizer_fair/        # 实验一：4-bit 量化器公平（PQ/SQ/SAQ/Ours，固定候选）
│   ├── 02_diskann_fair/          # 实验二 + 方法实现：Ours-DiskANN（Rust + native bridge）
│   └── 03_system_fair/           # 实验三：端到端系统公平（Ours/SymphonyQG/OG-LVQ/Glass-NSG/NGT-QG）
├── baselines/
│   └── diskann/                  # DiskANN3 框架（vendored，含本地构图进度插桩）
├── scripts/                      # 构建、复现、表格与绘图脚本
├── data/README.md                # 数据集布局与获取说明
├── BASELINE_EXPERIMENT_PLAN_MS_V2.md  # 实验协议与基线锁定版本
├── NOTICE.md                     # 第三方组件与许可
└── LICENSE                       # Apache-2.0
```

## Prerequisites

* Linux x86-64（论文测量环境：单节点、AVX-512、160 物理核、1.5 TB 内存）
* `g++`（C++17 + OpenMP）、`cmake` ≥ 3.20、`make`
* Rust 工具链（edition 2021），用于构建 `experiments/02_diskann_fair`
* Python ≥ 3.10，含 `numpy`（绘图还需 `matplotlib`）
* Faiss（pinned commit，由 `scripts/setup_faiss.sh` 拉取并构建）
* 实验一 SAQ 官方环境（锁定提交见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md`）
* 实验三各系统官方绑定：pyglass 2.1.0、Intel SVS、SymphonyQG、NGT（锁定提交见协议文档）

## Datasets

`data/<dataset>/` 下放置三个文件：`<dataset>_base.fvecs`、
`<dataset>_query.fvecs`、`<dataset>_groundtruth.ivecs`。三个数据集：
dbpedia（1536-d，约 990k 底库）、gist（960-d，1M）、agnews（1024-d，
约 769k）。查询集切分约定：前 N 条为验证集、其余为测试集（N 由
`--val-queries` 决定；02/03 共用同一测试子集）。公开数据源示例：
<https://www.cse.cuhk.edu.hk/systems/hash/gqr/datasets.html>，转成
fvecs/ivecs 后放入 `data/`；`scripts/check_datasets.py` 会校验。

## Build

```bash
# 1) Faiss（实验一依赖，pinned commit a424dcb80）
bash scripts/setup_faiss.sh

# 2) 实验一：构建 01 二进制 + 生成固定候选 + PQ/SQ/Ours 扫描
DATASETS="dbpedia gist agnews" bash scripts/run_faiss_quantizer_fair.sh
# SAQ 部分（配置好官方 SAQ 环境后）：
DATASETS="dbpedia gist agnews" bash scripts/run_saq_fixed_candidates_fair.sh

# 3) 实验二 / 方法：Ours-DiskANN（vendored DiskANN3，本地编译）
cargo build --release --manifest-path experiments/02_diskann_fair/Cargo.toml

# 4) 实验三：端到端系统公平
python experiments/03_system_fair/run_system_fair.py --dataset dbpedia \
  --systems Ours,SymphonyQG,OG-LVQ,Glass-NSG --validate --run --repeats 1 --threads 64
```

## Reproduce（三个实验完整跑批）

```bash
OUT_ROOT=results PYTHON=python3 bash scripts/run_master_round2.sh
```

脚本按 agnews → gist → dbpedia 顺序依次执行 01（PQ/SQ/Ours_K1 + SAQ +
表格导出）、02（各方法 4-bit 量化构图，R=64/L=400）、03（端到端系统公平 +
绘图 + 审计），输出写入 `results/round2/`。也可单独跑方法入口：

```bash
python Ours/experiments/run_ours.py --dataset dbpedia --M 32,64
```

## Tables & figures

```bash
python scripts/export_paper_quantizer_table.py --datasets dbpedia gist agnews \
  --out-root results --work-root work
python scripts/export_system_fair_table.py --datasets dbpedia,gist,agnews --out-root results
python scripts/plot_vldb2027_figures.py      # 输出 paper/figures/ 全套图
```

## Third-party components

见 [NOTICE.md](NOTICE.md)；各基线的锁定提交见
`BASELINE_EXPERIMENT_PLAN_MS_V2.md`。
