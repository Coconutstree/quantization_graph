# BASELINE_EXPERIMENT_PLAN_MS_V2.md

## 目标
在当前 `quantized_hnsw` 工程中建立三套彼此独立、可复现的公平实验：  
1. PQ、SQ、SAQ、LVQ 与 Ours 的“4bit 量化方法复现”对比：固定同一批 query 与 candidate vectors，以 exact L2 为真值，比较各方法 4 bit/dim compressed distance 的距离误差、fixed-candidate Recall@10、距离计算 QPS/latency；  
2. PQ-DiskANN、SQ-DiskANN、SAQ-DiskANN、LVQ-DiskANN 与 Ours-DiskANN 的“统一 DiskANN/Vamana 图与搜索流程”对比；  
3. SymphonyQG、NGT-QG、OG-LVQ、Glass-NSG 与 Ours 的“端到端图索引系统”对比。  

不要把三组实验混在一起。第一组不是图索引实验；它的任务是公平比较 4bit 量化距离本身，不能混入 HNSW `efSearch`、DiskANN `search_list_size` 或 Vamana `L_search`。第二组才是 controlled DiskANN payload 实验：使用同一份 DiskANN/Vamana 图、同一套 beam search 代码、同一套搜索终止条件，只替换 4bit payload 和 distance estimator。第三组保留各系统自己的图结构、量化方式、数据布局和搜索策略，做系统级 Pareto 对比。

## 0. 复现入口、数据集参数和产物目录

本计划要求所有实验都通过数据集名称驱动，禁止在代码里写死 DBpedia、SIFT、GIST、DEEP 等路径。编译和运行命令中都必须显式写数据集名称。

### 0.1 数据集目录约定

统一数据目录：

```text
data/${DATASET}/
```

每个数据集必须至少提供：

```text
data/${DATASET}/${DATASET}_base.fvecs
data/${DATASET}/${DATASET}_query.fvecs
data/${DATASET}/${DATASET}_groundtruth.ivecs
```

示例：

```text
data/dbpedia/dbpedia_base.fvecs
data/dbpedia/dbpedia_query.fvecs
data/dbpedia/dbpedia_groundtruth.ivecs

data/sift10m/sift10m_base.fvecs
data/sift10m/sift10m_query.fvecs
data/sift10m/sift10m_groundtruth.ivecs
```

若某数据集当前文件名不符合该约定，必须先转换或建立同名规范副本。例如 `data/glove/glove_base.fvecscs` 不能直接作为主实验输入，必须准备成 `data/glove/glove_base.fvecs`。

### 0.2 复现命令模板

所有命令都从仓库根目录执行：

```bash
cd /home/kai3/coco/quantization_graph
export DATASET=dbpedia
```

编译时必须写数据集名称，用 dataset-specific build dir 防止不同数据集的缓存、默认参数和中间文件混用：

```bash
cmake -S . -B build/experiments-${DATASET} \
  -DCMAKE_BUILD_TYPE=Release \
  -DEXPERIMENT_DATASET=${DATASET}

cmake --build build/experiments-${DATASET} -j 64
```

运行时也必须写同一个数据集名称：

```bash
./build/experiments-${DATASET}/experiments/run_all_suites \
  --dataset ${DATASET} \
  --data-root data \
  --out-root results \
  --threads 1 \
  --build-threads 64 \
  --repeats 5
```

也可以只复现单组实验：

```bash
./build/experiments-${DATASET}/experiments/run_quantizer_fair --dataset ${DATASET} --data-root data --out-root results
./build/experiments-${DATASET}/experiments/run_diskann_fair      --dataset ${DATASET} --data-root data --out-root results
./build/experiments-${DATASET}/experiments/run_system_fair    --dataset ${DATASET} --data-root data --out-root results
```

第二组改为 DiskANN Rust runner 后，正式入口可以由 CMake wrapper 调用，也可以直接用 Cargo 运行。直接运行模板为：

```bash
cargo run --release \
  --manifest-path experiments/02_diskann_fair/Cargo.toml \
  --bin run_diskann_fair \
  -- \
  --dataset ${DATASET} \
  --methods PQ,SQ,SAQ,LVQ,Ours
```

`run_diskann_fair` 的代码默认值已经固定正式配置：`data-root=data`、`out-root=results`、`max_degree=32`、`build_beam=400`、`search_beam_width=1`、`threads=1`、`build_threads=64`、`repeats=5`、`seed=20260813`，以及完整 `search_list_size` sweep：

```text
10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460
```

只有临时 smoke 或 ablation 才允许在命令行覆盖这些参数，且必须写入 manifest。

runner 启动后必须打印并写入 manifest：

```text
dataset=${DATASET}
base_path=data/${DATASET}/${DATASET}_base.fvecs
query_path=data/${DATASET}/${DATASET}_query.fvecs
gt_path=data/${DATASET}/${DATASET}_groundtruth.ivecs
build_dir=build/experiments-${DATASET}
out_root=results/${DATASET}
```

当前先实现数据集检查器，覆盖 `dbpedia` 和 `gist`：

```bash
python scripts/check_datasets.py \
  --datasets dbpedia gist agnews \
  --data-root data \
  --out-root results
```

该脚本只读取 `.fvecs/.ivecs` 的维度头、尾行维度头和文件大小，不把 base 文件全量读入内存。输出：

```text
results/dbpedia/manifests/dbpedia_dataset_manifest.json
results/gist/manifests/gist_dataset_manifest.json
```

当前检查结果：

| Dataset | Base count | Query count | Dimension | Groundtruth k | Status |
|---|---:|---:|---:|---:|---|
| `dbpedia` | 990000 | 10000 | 1536 | 100 | `ok` |
| `gist` | 1000000 | 1000 | 960 | 100 | `ok` |
| `agnews` | 769382 | 1000 | 1024 | 100 | `ok` |

### 0.2.1 第一组实验的正确定义：4bit 量化方法复现

第一组 `01_quantizer_fair` 的目标是复现每个 baseline 的核心 4bit 量化方法，并在相同 fixed candidate query/base pairs 上比较 compressed distance。执行顺序必须是：

1. 阅读该 baseline 的 README、示例和默认脚本。
2. 找到它官方推荐的 4bit 或 4 bit/dim 配置。
3. 使用同一份 `data/${DATASET}` 数据集生成该方法的 4bit index/code。
4. 对同一批 query-candidate pairs 计算 compressed distance，并以 exact L2 排序作为真值。
5. 输出距离误差、排序 overlap、fixed-candidate Recall@10、距离计算 QPS/latency、实际码长、索引大小、训练/编码时间。

第一组禁止把 `efSearch`、`search_list_size` 或其他图搜索参数当作 PQ/SQ/SAQ/LVQ 的通用参数。若某方法只有官方 native QPS 工具，可以作为补充结果保留，但第一组主表必须来自相同 fixed candidates。

### 0.2.2 当前已实现部分：Faiss PQ/SQ 的量化准确度与距离核日志

当前已经接通 Faiss PQ/SQ 的 fixed-candidate 评估链路。它就是第一组主口径：量化准确度、fixed-candidate Recall@10、压缩距离核 QPS/latency：

1. 一键跑三数据集：

```bash
scripts/run_faiss_quantizer_fair.sh
```

输出：

```text
work/01_quantizer_fair/${DATASET}/fixed_candidates_k1000.bin
work/01_quantizer_fair/${DATASET}/fixed_candidates_k1000.meta.json
results/${DATASET}/csv/01_quantizer_fair/faiss_PQ_fixed_candidate_raw.csv
results/${DATASET}/csv/01_quantizer_fair/faiss_SQ_fixed_candidate_raw.csv
results/${DATASET}/csv/01_quantizer_fair/faiss_quantizer_summary.csv
work/01_quantizer_fair/${DATASET}/faiss_hard_negative_candidates.log
work/01_quantizer_fair/${DATASET}/faiss_quantizer_summary.log
```

默认覆盖 `dbpedia gist agnews` 三个数据集。可用环境变量改参数，例如：

```bash
DATASETS="dbpedia gist agnews" \
MAX_TRAIN=100000 \
MAX_QUERIES=0 \
WORK_ROOT=work \
REBUILD_CANDIDATES=0 \
HARD_NEGATIVE_SEARCH_K=2000 \
HARD_NEGATIVE_EF_SEARCH=2000 \
scripts/run_faiss_quantizer_fair.sh
```

`REBUILD_CANDIDATES=0` 表示如果 `work/01_quantizer_fair/${DATASET}/fixed_candidates_k1000.{bin,meta.json}` 已存在，就复用，不重建。需要强制重建候选时设 `REBUILD_CANDIDATES=1`。

2. 分步复现：数据检查和第一组 baseline manifest：

```bash
python scripts/check_datasets.py \
  --datasets dbpedia gist agnews \
  --data-root data \
  --out-root results

python scripts/write_quantizer_manifest.py \
  --datasets dbpedia gist agnews \
  --out-root results
```

输出：

```text
results/${DATASET}/manifests/01_quantizer_fair_manifest.csv
```

3. 分步复现：生成 fixed candidate 集合：

```bash
/home/kai3/miniconda3/envs/ngt-build/bin/cmake \
  -S experiments/01_quantizer_fair \
  -B build/01_quantizer_fair \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-11

/home/kai3/miniconda3/envs/ngt-build/bin/cmake \
  --build build/01_quantizer_fair \
  -j 16

for DATASET in dbpedia gist agnews; do
  ./build/01_quantizer_fair/faiss_hard_negative_candidates \
    --dataset ${DATASET} \
    --data-root data \
    --out-root results \
    --candidate-root work \
    --candidate-size 1000 \
    --search-k 2000 \
    --seed 20260813 \
    --force
done
```

候选集合只用于量化准确度评估，不能解释为某方法的 ANN search 流程。候选文件在所有 quantizer 方法之间共享，不能按方法重新生成。为了避免第一组实验混入 HNSW 搜索参数，正式版候选策略应优先使用 `groundtruth + deterministic fixed random negatives` 或 exact hard negatives；当前旧实现中的 FP32-HNSW hard-negative builder 只能作为临时候选生成器，不能把其中的 `hnsw-M/efConstruction/efSearch` 记录成 PQ/SQ 参数。

4. 分步复现：Faiss PQ/SQ train -> encode -> fixed candidate distance -> CSV：

```bash
for DATASET in dbpedia gist agnews; do
  ./build/01_quantizer_fair/faiss_quantizer_smoke \
    --dataset ${DATASET} \
    --data-root data \
    --out-root results \
    --candidate-root work \
    --candidate-size 1000 \
    --max-train 100000 \
    --max-queries 0 \
    --methods PQ,SQ \
    --repeat-id 0 \
    --seed 20260813 \
    --overwrite-summary
done
```

`--max-queries 0` 表示使用该数据集全部 query。`--max-train 100000` 是当前正式固定训练规模：三套数据的 base count 都大于 100k；它明显高于 Faiss 对 256-centroid clustering 的建议下限 9984 个训练点，且避免不同数据集使用不同训练量造成额外变量。若后续论文主实验希望使用 all-base training，需要三个数据集统一改成 `--max-train` 大于各自 base count，并在 manifest 中重新记录。

如果只想快速验通链路，可以把 quantizer runner 的 `--max-train` 降到 `2000`、`--max-queries` 降到 `2` 或 `5`；此时 Faiss PQ 会提示训练点少于建议值，这是 smoke 的速度取舍，不代表正式设置，不能进入论文表。

Faiss PQ/SQ 输出：

```text
results/${DATASET}/csv/01_quantizer_fair/faiss_PQ_fixed_candidate_raw.csv
results/${DATASET}/csv/01_quantizer_fair/faiss_SQ_fixed_candidate_raw.csv
results/${DATASET}/csv/01_quantizer_fair/faiss_quantizer_summary.csv
```

说明：
- raw CSV 每次运行会覆盖同名文件。
- summary CSV 默认会追加结果行；正式跑时使用 `--overwrite-summary` 得到干净 summary。
- summary CSV 的前置列固定为 `dataset,method,recall,qps,latency_mean_us,latency_p50_us,latency_p95_us,k,search_param,rerank`，这里的 `recall/qps` 是 fixed-candidate ranking 与 compressed-distance throughput，不是 ANN Recall-QPS 曲线。
- 当前 runner 已覆盖 Faiss PQ/SQ 的第一组主口径。PQ 正式默认使用 `pq_m=D, pq_nbits=4`，SQ 使用 `QT_4bit`，二者都是 4 bit/dim。完整第一组还必须把 SAQ/LVQ/Ours 接到相同 fixed-candidate distance-evaluation 口径。

### 0.2.3 第一组每个方法需要生成的 4bit 复现日志

每个 `${DATASET}`、每个 `${METHOD}` 至少生成两类日志：

```text
results/${DATASET}/raw/01_quantizer_fair/${METHOD}/${METHOD}_4bit_accuracy.log
results/${DATASET}/csv/01_quantizer_fair/${METHOD}_4bit_accuracy.csv

results/${DATASET}/raw/01_quantizer_fair/${METHOD}/${METHOD}_4bit_distance_qps.log
results/${DATASET}/csv/01_quantizer_fair/${METHOD}_4bit_distance_qps.csv
```

其中：
- `*_4bit_accuracy.*` 记录量化准确度：distance error、ranking overlap、fixed-candidate recall、actual bytes/vector、index/code size、train/encode time。
- `*_4bit_distance_qps.*` 记录同一批 fixed candidates 上的 compressed-distance QPS/latency。若仍沿用历史文件名 `*_recall_qps.csv`，文件内必须明确 `search_param_name=fixed_candidates_k` 或 `candidate_size`，不能写 `efSearch`。

SAQ README 对 4bit 的官方入口是：

```bash
cd baselines/saq
./bin/create_index -dataset ${DATASET} -B 4
./bin/test_relative_error -dataset ${DATASET} -B 4
./bin/test_qps -dataset ${DATASET} -B 4
```

SAQ 运行前必须先按其 README 准备 IVF/PCA 文件：`*_centroid_4096*.fvecs`、`*_cluster_id_4096.ivecs`、`*_base_pca.fvecs`、`*_query_pca.fvecs`、`*_base_pca.vars.fvecs`。这些文件属于 SAQ 的官方复现依赖，不是 HNSW 建图。

当前已落地的 01 框架目录：

```text
experiments/01_quantizer_fair/
  accuracy_framework/
    README.md
    adapters/
      faiss_pq/README.md
      faiss_sq/README.md
      saq/README.md
      lvq/README.md
      ours/README.md
  recall_qps_framework/
    README.md
    runners/
      run_saq_b4.sh
    parsers/
      parse_saq_accuracy.py
      parse_saq_qps.py
      merge_4bit_results.py
```

SAQ4 当前统一入口：

```bash
DATASETS="gist" \
RUN_PREP=0 \
BUILD_THREADS=64 \
QUERY_THREADS=1 \
FIX_NPROBE=0 \
scripts/run_saq_4bit_reproduction.sh
```

若 SAQ 所需 IVF/PCA 文件不存在，先设 `RUN_PREP=1` 让脚本按 SAQ README 调用 `python/ivf.py` 和 `python/pca.py`。`FIX_NPROBE=0` 表示沿用 SAQ `test_qps` 默认 nprobe sweep；如果只跑单点，设置如 `FIX_NPROBE=200`。

### 0.3 统一输出目录

每个数据集的论文结果、CSV、原始日志和 manifest 写入：

```text
results/${DATASET}/
```

编译产物写入 `build/`；候选集合、可复用中间文件和临时日志写入 `work/`，不要混进 `results/`。

目录结构如下：

```text
results/${DATASET}/
  manifests/
  raw/
    01_quantizer_fair/
    02_diskann_fair/
    03_system_fair/
  csv/
    01_quantizer_fair/
    02_diskann_fair/
    03_system_fair/
  indexes/
    01_quantizer_fair/
    02_diskann_fair/
    03_system_fair/
  figures/
    01_quantizer_fair/
    02_diskann_fair/
    03_system_fair/
  audit/
```

`build/` 只放编译产物和临时 CMake 文件；`work/` 只放可复用中间文件；论文需要引用的日志、CSV、manifest 和图表必须放在 `results/${DATASET}/`。

### 0.4 实验开始前必须准备好的代码文件

开始 full run 前，至少需要先提供以下代码文件或等价实现。没有这些文件时只能做 smoke test，不能声明实验框架完成。

通用代码：

```text
CMakeLists.txt
experiments/CMakeLists.txt
experiments/common/dataset_loader.{h,cpp}
experiments/common/fvecs_ivecs_io.{h,cpp}
experiments/common/groundtruth.{h,cpp}
experiments/common/recall.{h,cpp}
experiments/common/timer.{h,cpp}
experiments/common/rss.{h,cpp}
experiments/common/file_size.{h,cpp}
experiments/common/hardware_info.{h,cpp}
experiments/common/csv_writer.{h,cpp}
experiments/common/manifest.{h,cpp}
experiments/common/repeat_runner.{h,cpp}
experiments/common/pin_threads.{h,cpp}
```

第一组量化器公平实验：

```text
experiments/01_quantizer_fair/run_quantizer_fair.cpp
experiments/01_quantizer_fair/CMakeLists.txt
experiments/01_quantizer_fair/faiss_hard_negative_candidates.cpp
experiments/01_quantizer_fair/faiss_quantizer_smoke.cpp
experiments/01_quantizer_fair/candidate_builder.{h,cpp}
experiments/01_quantizer_fair/quantizer_adapter.{h,cpp}
experiments/01_quantizer_fair/faiss_pq_adapter.{h,cpp}
experiments/01_quantizer_fair/faiss_sq_adapter.{h,cpp}
experiments/01_quantizer_fair/saq_adapter.{h,cpp}
experiments/01_quantizer_fair/lvq_adapter.{h,cpp}
experiments/01_quantizer_fair/ours_adapter.{h,cpp}
experiments/01_quantizer_fair/distance_kernel_bench.{h,cpp}
```

第二组统一 DiskANN/Vamana 实验：

```text
experiments/02_diskann_fair/README.md
experiments/02_diskann_fair/Cargo.toml
experiments/02_diskann_fair/src/main.rs
experiments/02_diskann_fair/src/args.rs
experiments/02_diskann_fair/src/config.rs
experiments/02_diskann_fair/src/dataset.rs
experiments/02_diskann_fair/src/logging.rs
experiments/02_diskann_fair/src/payload/mod.rs
experiments/02_diskann_fair/src/payload/pq.rs
experiments/02_diskann_fair/src/payload/sq.rs
experiments/02_diskann_fair/src/payload/saq.rs
experiments/02_diskann_fair/src/payload/lvq.rs
experiments/02_diskann_fair/src/payload/ours.rs
```

第二组优先写 Rust runner，因为 Microsoft DiskANN3 的主线实现和 `diskann-quantization` 都是 Rust crate。若必须从当前 C++ 工程统一启动，可额外提供一个很薄的 C++ wrapper 或 shell runner，但核心 graph/search/payload 逻辑仍以 Rust side 为准，避免把 DiskANN API 重新手写成另一个算法。

第三组端到端系统实验：

```text
experiments/03_system_fair/run_system_fair.cpp
experiments/03_system_fair/system_adapter.{h,cpp}
experiments/03_system_fair/symphonyqg_adapter.{h,cpp}
experiments/03_system_fair/ngt_qg_adapter.{h,cpp}
experiments/03_system_fair/og_lvq_adapter.{h,cpp}
experiments/03_system_fair/glass_nsg_adapter.{h,cpp}
experiments/03_system_fair/ours_system_adapter.{h,cpp}
experiments/03_system_fair/validation_tuner.{h,cpp}
experiments/03_system_fair/pareto_builder.{h,cpp}
```

结果处理脚本：

```text
scripts/check_datasets.py
scripts/build_fixed_candidates.py  # legacy smoke: groundtruth + random fill
scripts/write_quantizer_manifest.py
scripts/parse_rabitq_log.py
scripts/run_faiss_quantizer_fair.sh
scripts/merge_raw_csv.py
scripts/compute_median_results.py
scripts/interpolate_recall_targets.py
scripts/plot_quantizer_fair.py
scripts/plot_diskann_fair.py
scripts/plot_system_fair.py
scripts/generate_experiment_audit.py
```

外部 baseline 代码或安装位置也必须在 manifest 中记录：

```text
baselines/faiss/
baselines/saq/
baselines/svs/
baselines/diskann/
baselines/symphonyqg/
baselines/ngt/
baselines/glass/
```

若某个 baseline 通过系统包、Python wheel 或 shared library 提供，则 manifest 必须记录 package name、version、commit/source URL、编译 flags 和实际 linked library path。

### 0.5 已锁定 baseline commit

当前 baseline 源码固定如下。后续所有实验 manifest 必须写入这些 commit；如果任何 baseline 更新 commit，必须重新生成 manifest、audit 和对应实验结果。

| Baseline | 本地源码路径 | 官方来源 | Locked commit |
|---|---|---|---|
| Faiss PQ/SQ | `baselines/faiss/upstream/` | `https://github.com/facebookresearch/faiss` | `a424dcb809fd725c44dd976d9063febd4837d16a` |
| SAQ | `baselines/saq/` | `https://github.com/howarlii/saq` | `2163ebcedd0ad9c9f4de326e6ca7a860f9eafe52` |
| SVS / OG-LVQ | `baselines/svs/` | `https://github.com/intel/ScalableVectorSearch` | `078846e3f76829b60f5c256806da1b90e385cda5` |
| DiskANN3 + diskann-quantization | `baselines/diskann/` | `https://github.com/microsoft/DiskANN` | `3218478b5f7d1721840c29309e22d9fd74b1b4bc` |
| SymphonyQG | `baselines/symphonyqg/` | `https://github.com/gouyt13/SymphonyQG` | `6124ddb34ee4d176edea1bd7ad38d1672343df28` |
| NGT-QG | `baselines/ngt/` | `https://github.com/yahoojapan/NGT` | `494f63fdd821cbaf2e21a000b4777661e23cb68e` |
| Glass-NSG | `baselines/pyglass/` | `https://github.com/zilliztech/pyglass` | `d2296ec447d2374ee8f88c6d3b85be1b1e434ad3` |

### 0.6 Baseline 独立构建/import 命令

所有 baseline 的源码目录保持纯官方 clone；构建产物统一放到 `baselines/builds/`，不要写入官方源码目录，除非该项目自己的安装脚本不可避免会生成临时文件。每个 baseline 至少要先通过“独立 build 或 import 验证”，再接入统一实验 wrapper。

当前机器关键信息：

```text
CPU: Intel Xeon Gold 6248, AVX2 + AVX512 available
system cmake: /usr/bin/cmake, 3.22.1
conda cmake: /home/kai3/miniconda3/envs/ngt-build/bin/cmake, 4.3.2
system g++: 9.5.0
available g++-11: /usr/bin/g++-11, 11.4.0
python: 3.13.12
```

#### Faiss：PQ/SQ

用途：第一组 PQ/SQ，第二组 PQ-DiskANN/SQ-DiskANN payload 的参考量化实现。第二组正式优先接 DiskANN `diskann-quantization` 的 product/scalar quantizer；如果某个 4bit payload 暂时只能通过 Faiss 生成 codebook/code，必须在 manifest 中标注 `payload_source=faiss_adapter`。

当前 Faiss commit 要求 CMake 3.24+，不能用系统 `/usr/bin/cmake` 3.22.1；当前机器使用 conda env 中的 CMake 4.3.2。

```bash
/home/kai3/miniconda3/envs/ngt-build/bin/cmake \
  -S baselines/faiss/upstream \
  -B baselines/builds/faiss-cmake43 \
  -DCMAKE_BUILD_TYPE=Release \
  -DFAISS_ENABLE_GPU=OFF \
  -DFAISS_ENABLE_PYTHON=OFF \
  -DFAISS_ENABLE_EXTRAS=OFF \
  -DBUILD_TESTING=OFF \
  -DFAISS_OPT_LEVEL=avx2

/home/kai3/miniconda3/envs/ngt-build/bin/cmake \
  --build baselines/builds/faiss-cmake43 \
  --target faiss \
  -j 64
```

验证：

```bash
test -f baselines/builds/faiss-cmake43/faiss/libfaiss.a
```

当前验证状态：`build_ok`，产物为 `baselines/builds/faiss-cmake43/faiss/libfaiss.a`。如果目标机器支持 AVX512 并且主实验统一采用 AVX512，可把 `FAISS_OPT_LEVEL=avx512`，但必须在 manifest 中记录。

#### SAQ

用途：第一组 SAQ，第二组 SAQ-DiskANN payload。

依赖和硬件要求：

```bash
sudo apt install libfmt-dev libgoogle-glog-dev libgflags-dev libgtest-dev
```

SAQ README/CMake 明确要求 AVX512。当前机器 AVX512 检测通过，并已使用 `/usr/bin/g++-11` 构建成功。默认 GCC 9.5 会在 `_mm_loadu_epi32`、`_mm256_loadu_epi16` 等 intrinsic 处失败。

构建：

```bash
cmake -S baselines/saq -B baselines/builds/saq-gcc11 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-11 \
  -DBUILD_UNIT_TESTS=OFF

cmake --build baselines/builds/saq-gcc11 -j 64
```

验证：

```bash
test -x baselines/saq/bin/create_index
test -x baselines/saq/bin/test_relative_error
test -x baselines/saq/bin/test_qps
```

注意：SAQ 官方程序默认从其 `data/${dataset}/` 读写，接入统一实验时 wrapper 必须把 `data/${DATASET}/` 映射过去，不能复制或改名后造成不同输入。

#### SVS / OG-LVQ

用途：第一组 LVQ，第二组 LVQ-DiskANN，第三组 OG-LVQ。

SVS README 明确说明：当前开源仓库不包含专有 LVQ/LeanVec 实现；官方 LVQ 需要 Intel 发布的 PyPI package 或 release shared library。仅编译 `baselines/svs/` 只能验证 SVS open-source C++ 部分，不能等价于官方 OG-LVQ。

开源 C++ 部分构建。当前机器必须使用 `g++-11`；默认 GCC 9.5 会因为 C++20 flag 失败。第一次 configure 需要网络下载 `jfalcou/eve`。

```bash
cmake -S baselines/svs -B baselines/builds/svs-gcc11 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-11 \
  -DSVS_BUILD_BINARIES=ON \
  -DSVS_BUILD_EXAMPLES=ON \
  -DSVS_BUILD_TESTS=OFF \
  -DSVS_BUILD_BENCHMARK=OFF

cmake --build baselines/builds/svs-gcc11 -j 64
```

验证：

```bash
test -x baselines/builds/svs-gcc11/utils/build_index
test -x baselines/builds/svs-gcc11/utils/search_index
```

当前 SVS open-source 验证状态：`build_ok`。

官方 LVQ 路径二选一：

```bash
python -m pip install scalable-vs
python -c "import svs; print(svs.__file__)"
```

或下载 Intel SVS release shared library，并在 manifest 中记录：

```text
svs_package_or_shared_library_path
svs_package_version
svs_release_url
lvq_enabled=true
```

当前官方 LVQ 验证状态：`import_ok`，当前环境已安装 `scalable-vs 0.4.0`，`python -c "import svs"` 通过。manifest 必须记录 Python package name、version、module path 和是否启用 LVQ。

#### DiskANN3 + diskann-quantization

用途：第二组 controlled DiskANN/Vamana graph 实验。使用 Microsoft DiskANN 当前 Rust 主线代码提供 graph/search/provider 抽象，并从 `diskann-quantization/src` 接入 4bit payload/distance。该 crate 明确包含 scalar、product、minmax、spherical 等 quantizer 模块；其中 scalar quantization 支持 4bit `CompensatedVector<4>`，distance 模块对 4bit/8bit bit-slice 有 SIMD 路径。第二组只使用这些 graph/search/payload building block，不把它作为第三组完整系统 baseline。

第二组真正能和量化结合的框架层是 `diskann-providers` 的 in-memory graph provider，不是 `diskann-disk`：

```text
baselines/diskann/diskann/src/graph/
  DiskANN/Vamana graph、search、insert、prune 的核心逻辑。

baselines/diskann/diskann-quantization/src/
  量化训练、压缩向量表示、4bit/8bit compressed distance kernel。

baselines/diskann/diskann-providers/src/model/graph/provider/async_/inmem/
  把 DiskANN graph/search 和 vector payload 连接起来的 provider 层。
  第二组 controlled payload 实验优先接这一层。
```

具体文件对应：

```text
DiskANN-FP32  -> diskann-providers/src/model/graph/provider/async_/inmem/full_precision.rs
SQ-DiskANN    -> diskann-providers/src/model/graph/provider/async_/inmem/scalar.rs
PQ-DiskANN    -> diskann-providers/src/model/graph/provider/async_/inmem/product.rs

SAQ-DiskANN   -> 新写 SAQ payload/distance adapter，接同一份 in-memory provider/search 框架
LVQ-DiskANN   -> 新写 LVQ payload/distance adapter，接同一份 in-memory provider/search 框架
Ours-DiskANN  -> 新写 Ours payload/distance adapter，接同一份 in-memory provider/search 框架
```

`diskann-disk/` 和 SSD/on-disk provider 不作为第二组主实验入口。它会引入磁盘 I/O、cache、beam prefetch 等系统变量，适合第三组或补充系统实验；第二组只比较同一 DiskANN graph/search 下的 4bit payload 和 distance estimator。

先 clone 并锁 commit：

```bash
git clone https://github.com/microsoft/DiskANN baselines/diskann
cd baselines/diskann
git rev-parse HEAD
cd /home/kai3/coco/quantization_graph
```

把输出的 commit 写回 0.5 的 DiskANN 行。正式实验禁止直接记录 `main`、`HEAD` 或日期代替 commit。

独立构建和测试：

```bash
cd baselines/diskann
cargo test --release -p diskann-quantization
cargo test --release -p diskann
cargo test --release -p diskann-providers
cd /home/kai3/coco/quantization_graph
```

第二组 runner 推荐在 `experiments/02_diskann_fair/Cargo.toml` 中用 path dependency 连接本地源码：

```toml
[dependencies]
diskann = { path = "../../baselines/diskann/diskann" }
diskann-providers = { path = "../../baselines/diskann/diskann-providers" }
diskann-quantization = { path = "../../baselines/diskann/diskann-quantization", features = ["rayon"] }
diskann-vector = { path = "../../baselines/diskann/diskann-vector" }
diskann-utils = { path = "../../baselines/diskann/diskann-utils" }
```

验证状态字段：

```text
diskann_commit
diskann_quantization_commit
cargo_version
rustc_version
enabled_features=rayon
quantization_src=diskann-quantization/src
graph_impl=diskann/src/graph
provider_impl=diskann-providers/src/model/graph/provider/async_/inmem
```

如果 `cargo test --release` 因本机 Rust 版本或 workspace 依赖失败，先记录 `status=build_failed` 和完整 log；不要退回旧 C++ DiskANN `cpp_main` 作为第二组，除非整份计划明确改成 legacy DiskANN。

#### SymphonyQG

用途：第三组 SymphonyQG 系统级 baseline。

依赖和硬件要求：README/CMake 明确要求 AVX512；Python binding 推荐 Python 3.10。当前机器 Python 3.13.12 能临时编译并 import，但后续 full wrapper 仍建议按官方 reproduce 参数再做真实数据 smoke。

Python binding：

```bash
CXX=/usr/bin/g++-11 python -m pip install baselines/symphonyqg/python
python -c "import symphonyqg; print(symphonyqg.__file__)"
```

不污染当前 conda env 的临时验证：

```bash
CXX=/usr/bin/g++-11 python -m pip install \
  --target /tmp/baseline_python/symphonyqg \
  --no-build-isolation \
  --no-deps \
  baselines/symphonyqg/python

PYTHONPATH=/tmp/baseline_python/symphonyqg \
  python -c "import symphonyqg; import numpy as np; print(symphonyqg.__file__)"
```

C++ example 构建。当前 SymphonyQG `CMakeLists.txt` 写的是 `set(CXX_STANDARD 17)`，没有真正设置 CMake 的 C++ 标准；因此当前机器需要显式加 `-DCMAKE_CXX_FLAGS=-std=c++17`。

```bash
cmake -S baselines/symphonyqg -B baselines/builds/symphonyqg-gcc11 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-11 \
  -DCMAKE_CXX_FLAGS=-std=c++17

cmake --build baselines/builds/symphonyqg-gcc11 -j 64
```

验证：

```bash
test -x baselines/symphonyqg/bin/indexing
```

当前验证状态：C++ `build_ok`，Python `import_ok`。注意：官方 README 提到该 repo 将被归档且 SymphonyQG 已进入 RaBitQ-Library。主实验若继续使用此 repo，需要在 audit 中写明 commit 和 repo 状态。

#### NGT-QG

用途：第三组 NGT-QG 系统级 baseline。

依赖：

```bash
sudo apt install libblas-dev liblapack-dev
```

QG/QBG 需要 BLAS/LAPACK；如果使用 `NGT_QBG_DISABLED=ON`，就不能作为 NGT-QG 实验。当前机器已经找到 MKL BLAS/LAPACK。当前机器必须使用 `g++-11`；默认 GCC 9.5 会在 AVX512 intrinsic 处失败。

构建：

```bash
cmake -S baselines/ngt -B baselines/builds/ngt-gcc11 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-11 \
  -DNGTQG_NO_ROTATION=ON \
  -DNGTQG_ZERO_GLOBAL=ON

cmake --build baselines/builds/ngt-gcc11 -j 64
```

验证：

```bash
test -x baselines/builds/ngt-gcc11/bin/ngt/ngt
test -x baselines/builds/ngt-gcc11/bin/qbg/qbg
```

当前验证状态：`build_ok`。Python binding 可选：

```bash
python -m pip install baselines/ngt/python
python -c "import ngtpy; print(ngtpy.__file__)"
```

注意：NGT README 说明 `ngtq`/`ngtqg` 已由 `qbg` command 替代；第三组 wrapper 应优先围绕当前 QG/QBG 推荐 CLI/API，而不是旧命令。

#### Glass / pyglass

用途：第三组 Glass-NSG 系统级 baseline。

推荐使用 Python editable install。当前机器必须指定 `CXX=/usr/bin/g++-11`；默认 GCC 9.5 不支持 pyglass 需要的 C++20。

```bash
CXX=/usr/bin/g++-11 python -m pip install -v -e baselines/pyglass/python
python -c "import glass; print(glass.__file__)"
```

不污染当前 conda env 的临时验证：

```bash
CXX=/usr/bin/g++-11 python -m pip install \
  --target /tmp/baseline_python/pyglass \
  --no-build-isolation \
  --no-deps \
  baselines/pyglass/python

PYTHONPATH=/tmp/baseline_python/pyglass \
  python -c "import glass; print(glass.__file__)"
```

最小 import/search smoke test：

```bash
PYTHONPATH=/tmp/baseline_python/pyglass \
  python -c "import glass, numpy as np; X=np.random.randn(1000,32).astype('float32'); q=X[0]; index=glass.Index(index_type='NSG', metric='L2', R=32, L=50); graph=index.build(X); searcher=glass.Searcher(graph=graph, data=X, metric='L2', quantizer='SQ4U'); searcher.set_ef(32); print(searcher.search(query=q, k=10))"
```

当前验证状态：`import_and_smoke_ok`。Glass README 建议 NSG 建图可配 `R`、`L`，搜索端可配 quantizer 和 `ef`；这些都必须进入 validation tuning manifest。

#### 当前验证状态汇总

| Baseline | 当前状态 | 成功产物或阻塞点 |
|---|---|---|
| Faiss | `build_ok` | `baselines/builds/faiss-cmake43/faiss/libfaiss.a` |
| SAQ | `build_ok` | `baselines/saq/bin/create_index`, `baselines/saq/bin/test_relative_error`, `baselines/saq/bin/test_qps` |
| SVS open-source | `build_ok` | `baselines/builds/svs-gcc11/utils/build_index`, `baselines/builds/svs-gcc11/utils/search_index` |
| SVS official LVQ | `import_ok` | 当前环境已安装 `scalable-vs 0.4.0`，module path 为 `/home/kai3/miniconda3/lib/python3.13/site-packages/svs/__init__.py` |
| DiskANN3 + diskann-quantization | `pending` | 需要 clone 到 `baselines/diskann/`，锁定 commit，并通过 `cargo test --release -p diskann-quantization -p diskann -p diskann-providers` 或等价分步验证 |
| SymphonyQG C++ | `build_ok` | `baselines/symphonyqg/bin/indexing` |
| SymphonyQG Python | `import_ok` | `/tmp/baseline_python/symphonyqg/symphonyqg.cpython-313-x86_64-linux-gnu.so` |
| NGT-QG | `build_ok` | `baselines/builds/ngt-gcc11/bin/ngt/ngt`, `baselines/builds/ngt-gcc11/bin/qbg/qbg` |
| Glass / pyglass | `import_and_smoke_ok` | 当前环境已安装 `glass 2.1.0`，module path 为 `/home/kai3/miniconda3/lib/python3.13/site-packages/glass.cpython-313-x86_64-linux-gnu.so`，NSG search smoke 通过 |

### 0.7 产物生成总顺序

对每个 `${DATASET}`，完整实验按以下顺序生成文件：

| 顺序 | 阶段 | 生成文件 | 存放位置 |
|---:|---|---|---|
| 1 | 数据检查 | `${DATASET}_dataset_manifest.json` | `results/${DATASET}/manifests/` |
| 2 | 硬件/编译信息 | `${DATASET}_hardware.json`, `${DATASET}_build_manifest.json` | `results/${DATASET}/manifests/` |
| 3 | 第一组 manifest | `01_quantizer_fair_manifest.csv`, `${method}_4bit_config.json` | `results/${DATASET}/manifests/` |
| 4 | 第一组官方预处理文件 | 例如 SAQ 的 `*_centroid_4096*.fvecs`, `*_cluster_id_4096.ivecs`, `*_base_pca.fvecs`, `*_query_pca.fvecs`, `*_base_pca.vars.fvecs` | baseline README 指定目录；同时在 manifest 记录绝对路径 |
| 5 | 第一组 quantizer/index/code | README 生成的 4bit index/code/model，例如 `${method}_B4.index`、`${method}_4bit_codes.bin` | baseline README 指定目录或 `results/${DATASET}/indexes/01_quantizer_fair/${method}/` |
| 6 | 第一组准确度原始日志 | `${method}_4bit_accuracy.log` | `results/${DATASET}/raw/01_quantizer_fair/${method}/` |
| 7 | 第一组 Recall-QPS 原始日志 | `${method}_4bit_recall_qps.log` | `results/${DATASET}/raw/01_quantizer_fair/${method}/` |
| 8 | 第一组准确度 CSV | `${method}_4bit_accuracy.csv`；当前 Faiss PQ/SQ fixed-candidate 临时产物：`faiss_PQ_fixed_candidate_raw.csv`, `faiss_SQ_fixed_candidate_raw.csv`, `faiss_quantizer_summary.csv` | `results/${DATASET}/csv/01_quantizer_fair/` |
| 9 | 第一组 Recall-QPS CSV | `${method}_4bit_recall_qps.csv`, `01_quantizer_fair_4bit_recall_qps_merged.csv`, `01_quantizer_fair_4bit_recall_qps_median.csv` | `results/${DATASET}/csv/01_quantizer_fair/` |
| 10 | 第一组图表 | `4bit_recall_qps.png`, `4bit_quantization_error.png`, `4bit_index_size.png`, `4bit_train_encode_time.png` | `results/${DATASET}/figures/01_quantizer_fair/` |
| 11 | 第二组 manifest | `02_diskann_fair_manifest.csv` | `results/${DATASET}/manifests/` |
| 12 | 第二组共享 DiskANN/Vamana 图 | `diskann_fp32_R${R}_LB${Lbuild}_alpha${alpha}.graph.bin`, `diskann_fp32_R${R}_LB${Lbuild}_alpha${alpha}.graph.json`, `diskann_fp32_R${R}_LB${Lbuild}_alpha${alpha}.sha256` | `results/${DATASET}/indexes/02_diskann_fair/shared_graph/` |
| 13 | 第二组各方法 payload | `${method}_bpd${bpd}_payload.bin`, `${method}_bpd${bpd}_payload.json` | `results/${DATASET}/indexes/02_diskann_fair/${method}/` |
| 14 | 第二组组合索引 | `${method}_R${R}_LB${Lbuild}_bpd${bpd}.index` | `results/${DATASET}/indexes/02_diskann_fair/${method}/` |
| 15 | 第二组原始日志 | `${method}_Ls${search_list_size}_Bw${search_beam_width}_repeat${repeat_id}.log`；每个方法额外输出一个汇总全 sweep 的 DiskANN-native log：`${DATASET}_${method}_R${R}_Lbuild${Lbuild}.log` | `results/${DATASET}/raw/02_diskann_fair/${method}/` |
| 16 | 第二组 CSV | `diskann_fair_raw.csv`, `diskann_fair_merged.csv`, `diskann_fair_median.csv`, `diskann_fair_interpolated.csv` | `results/${DATASET}/csv/02_diskann_fair/` |
| 17 | 第二组图表 | `qps_recall_pareto.png`, `memory_recall.png`, `visited_nodes_recall.png`, `distance_calls_recall.png`, `bytes_read_recall.png` | `results/${DATASET}/figures/02_diskann_fair/` |
| 18 | 第三组 manifest | `03_system_fair_manifest.csv`, `03_system_fair_tuning_budget.json` | `results/${DATASET}/manifests/` |
| 19 | 第三组 validation 调参记录 | `${system}_validation_trials.csv`, `${system}_selected_config.json` | `results/${DATASET}/csv/03_system_fair/tuning/` |
| 20 | 第三组系统索引 | `${system}_${config_id}.index` 或官方目录结构 | `results/${DATASET}/indexes/03_system_fair/${system}/` |
| 21 | 第三组原始日志 | `${system}_${config_id}_${search_param}_repeat${repeat_id}.log` | `results/${DATASET}/raw/03_system_fair/${system}/` |
| 22 | 第三组 CSV | `system_fair_raw.csv`, `system_fair_merged.csv`, `system_fair_median.csv`, `system_fair_interpolated.csv` | `results/${DATASET}/csv/03_system_fair/` |
| 23 | 第三组图表 | `system_qps_recall.png`, `system_memory_recall.png`, `system_qps_memory_at_95recall.png`, `system_p95latency_recall.png`, `system_buildtime_ms.png`, `system_index_size.png` | `results/${DATASET}/figures/03_system_fair/` |
| 24 | 审计报告 | `EXPERIMENT_AUDIT.md` | `results/${DATASET}/audit/` |

任一步失败时，必须保留已生成的 manifest、raw log 和 status CSV，并在对应行写 `status=failed` 或 `status=unsupported`。

## 1. 全局公平规则
- 数据必须完全相同：同一 base、query、groundtruth，不允许不同方法重新切分 query。优先使用当前项目的 DBpedia/OpenAI-1536 数据；后续按同一规则扩展 DEEP1M、SIFT1M/GIST1M。
- 主指标统一为 L2、Recall@10、QPS、平均查询延迟、P95 延迟、索引/码本实际占用、构建/训练时间。第一组的 Recall-QPS 来自各方法 README/官方实现的 4bit 搜索流程；量化准确度来自 fixed candidate 或官方 relative-error 测试。Ground truth 全部使用同一份 Float32 精确结果。
- 所有构建相关时间统一使用毫秒（ms）记录和输出，包括 `build_time_ms`、`train_time_ms`、`encode_time_ms`、`graph_build_time_ms`、`payload_import_time_ms`。禁止构建时间使用 us；也不要在不同方法之间混用 s/ms/us。
- 查询延迟可以继续使用微秒（us），例如 `latency_mean_us`、`latency_p50_us`、`latency_p95_us`。
- 查询性能主实验统一单线程；构建线程数固定为同一个值并记录。所有方法运行在同一台机器、同一 NUMA 节点、相同 CPU affinity 下。
- Release 编译统一使用同等级优化；记录 compiler、CMake flags、CPU 型号、AVX2/AVX-512 支持、baseline commit hash、Ours commit hash。
- 每个配置先 warm-up，再正式测试；正式测试至少重复 5 次，报告 median QPS/latency，同时保留所有原始 run。
- 主实验禁止“某个 baseline 用 Float32 rerank，而另一个不用”。第一组按 README 复现时，若某方法原始流程包含 rerank/verification，允许保留，但必须把参数名、原始向量空间和 rerank 时间全部计入，并在结果中明确标注。
- 不能只按理论 bit 数比较内存。必须同时记录：`nominal_bits_per_dim`、`code_bytes_per_vector`、`metadata_bytes_per_vector`、`serialized_index_size`、`peak_RSS`。
- 主公平点统一选择 4 bit/dim：SQ4、LVQ4、SAQ `B=4`，PQ 默认 `M=D, nbits=4`。若某方法只能提供近似 4bit 档，必须在 manifest 中写明实际 `nominal_bpd` 和 `actual_bytes_per_vector`；其他 bit budget 作为补充 rate-distortion 曲线。
- 参数选择不能看测试集结果后手工挑最好点。需要调参时，从 base 中固定划出训练/validation 子集，测试 query 只用于最终报告。
- 第一组 Recall-QPS CSV 统一 schema：`suite,dataset,method,metric,k,nominal_bpd,actual_bytes_per_vector,index_size_mb,peak_rss_mb,build_time_ms,train_time_ms,encode_time_ms,search_param_name,search_param_value,recall,qps,latency_mean_us,latency_p50_us,latency_p95_us,distance_calls,threads,repeat_id,git_commit`。第一组准确度 CSV 统一 schema：`suite,dataset,method,metric,nominal_bpd,actual_bytes_per_vector,index_size_mb,train_time_ms,encode_time_ms,mean_relative_error,p95_relative_error,mean_absolute_error,top10_overlap,pairwise_flip_rate,fixed_candidate_recall,query_count,candidate_size,threads,repeat_id,git_commit`。第二/第三组可以额外包含 `graph_build_time_ms,graph_degree,max_degree,build_beam,search_list_size,search_beam_width,visited_nodes,refine_calls,avg_bits_read` 等图索引字段。
- 所有方法必须保留原始日志。任何“不支持”“API 无法提供”“硬件不满足”的配置明确写 `unsupported`，禁止用估算值伪造。

## 2. PQ、SQ、SAQ、LVQ vs Ours：量化器本身的公平实验

### 2.1 实验目的
这一组是 4bit 量化方法复现实验。对每个方法，先阅读其 README/官方示例，找到官方推荐的核心量化配置，并固定到 4 bit/dim 或最接近的 4bit 档。然后输出两类结果：
- Recall-QPS 曲线：使用该方法 README 中的官方搜索/评估流程，扫它自己的搜索参数。
- 量化准确度：记录 distance error、ranking overlap、fixed-candidate recall、实际存储、训练/编码时间。

这一组不强行统一图结构，也不把所有方法套进 DiskANN。若某方法官方流程依赖 IVF/PCA/cluster id，例如 SAQ，则这些属于该方法复现的一部分；但参数名必须按 README 记录，例如 `nprobe`，不能写成 `search_list_size` 或 `ef`。

### 2.2 方法实现
- PQ：使用 Faiss 官方 `ProductQuantizer/IndexPQ` 或 README/官方教程中最接近的 4bit PQ 搜索入口；若为了生成 Recall-QPS 曲线需要 Faiss IVF-PQ，则记录为 `Faiss-IVFPQ4`，搜索参数为 `nprobe`。
- SQ：使用 Faiss 官方 `ScalarQuantizer/IndexScalarQuantizer` 或 IVF-SQ4 搜索入口；若使用 IVF-SQ，记录为 `Faiss-IVFSQ4`，搜索参数为 `nprobe`。
- SAQ：使用作者官方实现，不改其量化数学、PCA/IVF 预处理和 SIMD kernel；README 主配置为 `-B 4`，Recall-QPS 由 `test_qps` 扫 `nprobe`。
- LVQ：原生实验优先使用 Intel SVS 官方 LVQ package/shared library，按官方参数生成 4bit/LVQ4 结果。
- Ours：直接调用当前工程里的正式 4bit encode/query-distance 或官方搜索入口，不为 baseline 实验另写特殊版本。

### 2.3 主公平配置
对每个数据集设目标 budget = 4 bit/dim。
- SQ：SQ4。
- SAQ：B=4。
- LVQ：LVQ4。
- PQ：正式默认固定为 `M=D, nbits=4`，即每一维一个 16-centroid 子量化器，码长为 4 bit/dim。若补充实验要测更高效的 `M=D/2, nbits=8` 或其他配置，必须单独标注为不同 PQ 配置，不能和 4-bit PQ 混写。
- Ours：配置为实际存储预算约 4 bit/dim；所有 scale、norm、centroid、block metadata 都计入 actual storage。若 Ours 是 progressive/adaptive read，只允许“读取更少”，不能隐瞒完整存储码长。

补充实验可测 1/2/4/8 bit/dim 或各方法原生支持的码率，但论文主表以共同支持的 4 bit/dim 为核心，并额外画 `Memory(or bpd)-Recall` Pareto。

### 2.4 测试 A：量化误差/排序质量
这一项用于解释“同样 4bit 下量化是否准确”。对所有方法使用完全相同的 query-candidate pairs。
- 对每个 query，生成固定候选集合；候选必须包含真实近邻和 method-independent hard negatives，candidate size 固定为 1000。当前实现使用 `groundtruth + FP32 Faiss HNSW hard negatives`，评估时再对固定候选计算 Float32 exact distance。
- 所有方法仅计算这些相同候选的压缩距离。
- 输出：mean relative distance error、P95 relative error、候选排序 pairwise flip rate、Recall@10 within fixed candidate set、Top-10 overlap。

当前已接通 Faiss PQ/SQ hard-negative 固定候选链路：
- 构建入口：`experiments/01_quantizer_fair/CMakeLists.txt`
- candidate builder：`experiments/01_quantizer_fair/faiss_hard_negative_candidates.cpp`
- runner：`experiments/01_quantizer_fair/faiss_quantizer_smoke.cpp`
- 输入：`data/${DATASET}/${DATASET}_base.fvecs`、`data/${DATASET}/${DATASET}_query.fvecs`、`work/01_quantizer_fair/${DATASET}/fixed_candidates_k1000.bin`
- 输出：`results/${DATASET}/csv/01_quantizer_fair/faiss_PQ_fixed_candidate_raw.csv`、`faiss_SQ_fixed_candidate_raw.csv`、`faiss_quantizer_summary.csv`
- 已统计：mean relative distance error、P95 relative error、mean absolute error、fixed-candidate Recall@10、Top-10 overlap、pairwise flip rate、compressed distance QPS、单 query mean/p50/p95 latency、train/encode/fixed-candidate distance time。
- 待补齐：SAQ/LVQ/Ours adapter。若论文最终要求 exact top1000/top5000 hard negatives，可在当前 FP32-HNSW hard-negative builder 基础上替换候选来源，并保持 downstream CSV schema 不变。
- Ours 若有 1→2→4 或 1→2→4→8 progressive 路径，额外输出每一级触发比例、平均实际读取 bit 数和 refinement 次数。

### 2.5 测试 B：README 复现的 Recall-QPS 曲线
这一项是第一组的主曲线。每个方法按 README/官方脚本生成 4bit index/code，然后扫该方法自己的搜索参数。
- SAQ：按 README 运行 `create_index -B 4`、`test_qps -B 4`，搜索参数为 `nprobe`。
- Faiss PQ/SQ：使用 Faiss 官方 quantizer/search 入口生成 4bit PQ/SQ 结果。若使用 IVF-PQ/IVF-SQ，则搜索参数为 `nprobe`；若使用 flat compressed scan，则记录单点 Recall-QPS，并标注 `search_param_name=flat_scan`。
- LVQ/SVS：按 SVS 官方 LVQ 入口生成 LVQ4 结果，搜索参数用 SVS README/API 的原名。
- Ours：按当前工程官方 4bit 查询入口生成曲线，搜索参数用当前工程原名。

输出必须包含 `search_param_name` 和 `search_param_value`，例如 `nprobe=5/10/.../4000`。不要把 `nprobe`、`L`、`search_window_size`、`rerank` 统一改名成 `ef`。

### 2.6 测试 C：压缩距离核性能
- 将同一批 query-candidate pairs 预加载到内存，避免磁盘 I/O。
- 分开记录 query preprocessing 时间和 distance kernel 时间；最终 end-to-end query latency 必须包含实际查询阶段必需的 query transform/LUT 构建。
- 记录 `ns/distance`、`distances/s`、内存带宽相关统计（若已有 profiler）、实际 bytes touched（能准确统计时）。
- 每个方法使用其官方/当前工程可用的正式 SIMD 路径，同时记录 SIMD ISA；不要人为让某个 baseline 退化到 scalar。若某方法官方 4bit code 没有 SIMD 路径，必须如实记录。
- 所有训练、编码和索引构建耗时统一转换为毫秒输出，例如 `train_time_ms`、`encode_time_ms`、`build_time_ms`。

### 2.7 第一组最终图表
- `4bit_recall_qps`
- `4bit_quantization_error`
- `4bit_fixed_candidate_recall`
- `4bit_distance_throughput`
- `4bit_code_memory`
- `4bit_train_encode_time`

主表给出 4 bit/dim 下 PQ、SQ、SAQ、LVQ、Ours 的 README 复现配置、Recall-QPS 曲线摘要、accuracy、kernel speed、actual storage、encode time（ms）。

## 3. PQ-DiskANN、SQ-DiskANN、SAQ-DiskANN、LVQ-DiskANN vs Ours-DiskANN：统一 DiskANN/Vamana 图公平实验

### 3.1 实验目的
这一组专门回答：在完全相同 DiskANN/Vamana graph、相同 beam search、相同候选队列和相同终止条件下，只替换 4bit payload 与 distance estimator 后，哪种量化方式提供更好的 Recall-QPS-Memory trade-off。

第二组不是端到端 DiskANN 系统 benchmark，也不测试 SSD I/O 优势。为了隔离量化 payload，主实验先使用 in-memory provider 和 warm page cache；如果后续要测 SSD/on-disk provider，必须作为第二组补充实验单独标注，不能与主 controlled payload 曲线混写。

### 3.2 DiskANN 接入边界
第二组使用 Microsoft DiskANN3 当前 Rust 主线：
- graph/search/provider 来自 `baselines/diskann/diskann/` 与 `baselines/diskann/diskann-providers/`。
- 4bit scalar/product quantization building block 来自 `baselines/diskann/diskann-quantization/src`。
- `diskann-quantization` 的 scalar quantizer 用 `ScalarQuantizationParameters` 训练，用 `CompensatedVector<4>` 存储 SQ4；product quantizer 优先使用 `diskann-quantization::product`，若 API 不足再临时使用 Faiss PQ 生成 4bit codebook/code，并在 manifest 写 `payload_source=faiss_pq_adapter`。
- SAQ、LVQ、Ours 的量化数学不变：只把它们生成的 4bit code/payload 接到 DiskANN graph search 的 distance adapter 中，不改训练、编码、scale/norm/residual 计算方式。

具体接入层固定为：

```text
graph/search core:
  baselines/diskann/diskann/src/graph/

provider glue:
  baselines/diskann/diskann-providers/src/model/graph/provider/async_/inmem/

quantization kernels:
  baselines/diskann/diskann-quantization/src/
```

先复用 DiskANN 已有 in-memory provider：

```text
DiskANN-FP32  -> inmem/full_precision.rs
SQ-DiskANN    -> inmem/scalar.rs
PQ-DiskANN    -> inmem/product.rs
```

再按同一 provider/search 接口补三个自定义 payload adapter：

```text
SAQ-DiskANN   -> experiments/02_diskann_fair/src/payload/saq.rs
LVQ-DiskANN   -> experiments/02_diskann_fair/src/payload/lvq.rs
Ours-DiskANN  -> experiments/02_diskann_fair/src/payload/ours.rs
```

这些 adapter 只实现 payload 存储、query preprocessing、compressed distance 和 bytes-read 统计；不能实现自己的 graph traversal。

禁止为了方便而把 DiskANN graph 导出后改写成 HNSW search loop；也禁止每个 payload 重新跑一遍 DiskANN build。第二组的公平点是 controlled DiskANN graph，不是 controlled HNSW graph。

### 3.3 第二组完整执行步骤
1. 锁定 DiskANN 源码和构建环境。
   - clone 到 `baselines/diskann/`。
   - 用 `git rev-parse HEAD` 写入 0.5 和 `02_diskann_fair_manifest.csv`。
   - 跑 `cargo test --release -p diskann-quantization`、`cargo test --release -p diskann`、`cargo test --release -p diskann-providers`。
   - 记录 `rustc_version`、`cargo_version`、enabled features、CPU ISA。

2. 准备 Rust runner。
   - 新建 `experiments/02_diskann_fair/Cargo.toml`，通过 path dependency 指向 `baselines/diskann/`。
   - `src/main.rs` 只接受 `--dataset`、`--methods`、`--data-root`、`--out-root`、`--threads`、`--build-threads`、`--repeats`、`--seed` 以及 DiskANN build/search 参数。
   - 从 `data/${DATASET}/${DATASET}_base.fvecs`、`query.fvecs`、`groundtruth.ivecs` 读取，不允许写死任何数据集路径。
   - runner 内部先实例化 DiskANN 的 in-memory provider；不要从 `diskann-disk/`、SSD index 或 legacy C++ `cpp_main` 起步。

3. 先做三个 provider smoke test。
   - `DiskANN-FP32`：用 `inmem/full_precision.rs` 跑 1k base、10 query，确认 graph build/search/recall CSV 能通。
   - `SQ-DiskANN`：用 `inmem/scalar.rs` 跑 `WithBits<4>` 或等价 4bit scalar provider，确认 `CompensatedVector<4>` 存储和 L2 distance 能通。
   - `PQ-DiskANN`：用 `inmem/product.rs` 跑官方 PQ provider；如果 product API 对 4bit/dim 不完整，先写 `status=partial`，临时用 Faiss PQ adapter 生成 codebook/code，但 search 仍走同一个 DiskANN graph。

4. 构建一次 FP32 DiskANN/Vamana graph。
   - metric 固定 L2，base 向量固定为 Float32。
   - 主配置先用 `R/max_degree=32`、`Lbuild/build_beam=400`、`alpha=1.2`、`seed=20260813`；该配置用于对齐旧日志中的 `M=32, ef=400` 构图强度，但日志和 CSV 中必须使用 DiskANN 参数名。
   - 只构建一次 graph，输出 adjacency、starting point/medoid、graph config、node count、degree histogram、checksum。
   - 输出路径：`results/${DATASET}/indexes/02_diskann_fair/shared_graph/`。
   - 记录 `graph_build_time_ms`；这个时间只归共享图，不归任何单个量化方法私有。

5. 为每个方法生成 4bit payload。
   - `DiskANN-FP32`：保留 raw Float32 payload，只作为上界和 sanity check，不参与 4bit 内存公平主结论。
   - `PQ-DiskANN`：4 bit/dim，默认 `M=D, nbits=4`；生成 PQ codebook、base codes、query preprocessing/LUT 逻辑。
   - `SQ-DiskANN`：使用 DiskANN scalar quantizer 的 4bit compensated vector 路径，记录 standard deviations 参数，默认从官方示例起点 `standard_deviations=2.0`，如调参必须只用 validation。
   - `SAQ-DiskANN`：复用 SAQ `B=4` 的训练、PCA/IVF 依赖和 code layout，另写 DiskANN distance adapter。
   - `LVQ-DiskANN`：优先调用 SVS 官方 LVQ4 package/shared library 生成 payload；无法直接接入时标注 `paper-equivalent LVQ-DiskANN`。
   - `Ours-DiskANN`：复用 Ours 正式 4bit encode/query-distance；progressive/adaptive 读取机制可以保留，但必须记录每级读取比例。
   - 每个 payload 输出 `${method}_bpd4_payload.bin` 和 `${method}_bpd4_payload.json`；json 必须包含 actual bytes/vector、metadata bytes/vector、codebook bytes、train/encode time。

6. 组合共享 graph 与 payload。
   - 每个方法只加载同一份 shared graph checksum。
   - runner 启动时比较 `graph_checksum`；任何方法 checksum 不一致直接 `status=failed`。
   - distance adapter 只能实现 `query_preprocess`、`distance(query_state, node_id)`、`payload_bytes_read(node_id)` 这三个职责，不得改变 graph traversal。

7. 跑统一 DiskANN beam search sweep。
   - `k=10`，metric=L2，主实验不做 Float32 rerank。
   - 统一扫 `search_list_size={10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460}`。
   - 统一扫 `search_beam_width={1,2,4,8}`；如果 in-memory provider 中 beam width 不影响 I/O，可以固定 `search_beam_width=1`，但要在 manifest 写明原因。
   - 搜索参数名保留 DiskANN 风格：`search_list_size`、`search_beam_width`。
   - 每个点 warm-up 后正式重复 5 次，单线程查询；构建线程固定并记录。

8. 输出 DiskANN-native plain-text log。
   - 第二组每个 quantized payload 都必须输出一个和旧 `hnsw_rabitq` 日志一样便于审计的 plain-text log，但字段名、参数名、文件名全部改成 DiskANN-native。
   - 固定 DiskANN/Vamana graph 只构建一次；PQ/SQ/SAQ/LVQ/Ours 只能替换 4bit payload 和 distance estimator。
   - 第二组主配置固定为 `R/max_degree=32`、`Lbuild/build_beam=400`、`alpha=1.2`。不要在第二组日志里写 `M=32`、`efConstruction=400` 或 `efSearch` 作为主字段；如需对齐旧日志，可在备注字段写 `legacy_equivalent=M32_ef400`。
   - 每个方法至少输出一个主日志：

```text
results/${DATASET}/raw/02_diskann_fair/${METHOD}/${DATASET}_${METHOD}_R32_Lbuild400.log
```

   - 日志头部必须包含这些字段：

```text
experiment_profile=02_diskann_payload_fair
suite=02_diskann_fair
dataset=${DATASET}
method=${METHOD}
metric=L2
base_count=...
query_count=...
dimension=...
graph_type=DiskANN/Vamana
max_degree=32
build_beam=400
alpha=1.2
legacy_equivalent=M32_ef400
search_beam_width=...
nominal_bits_per_dim=4
actual_bytes_per_vector=...
code_bytes_per_vector=...
metadata_bytes_per_vector=...
graph_checksum=...
payload_checksum=...
index_path=...
payload_path=...
```

   - 构建阶段必须输出：

```text
build_stage=train_quantizer us=...
build_stage=payload_encode us=... count=...
build_stage=payload_import us=...
build_stage=graph_build us=...
build_total_us=...
Graph construction time: ... seconds
Build time: ... seconds
Index storage size: ... MB (graph=... MB, payload=... MB, codebook=... MB, metadata=... MB, total_bytes=...)
Peak RSS: ... MB
```

   - 查询阶段每个 `search_list_size` 一行。第二组 DiskANN-native sweep 固定为：

```text
search_list_size={10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460}
```

   - 每一行至少包含：

```text
search_list_size recall latency_mean_us qps latency_p95_us visited_nodes distance_computations payload_bytes_read avg_bits_read refine_calls
```

   - tabular 行示例：

```text
460  0.99535  6067.77 us  search_list_size=460  search_beam_width=1  total_us_per_query=6067.77  qps=164.805  p95_us=8318  visited_nodes=10243.8  distance_computations=1902.2  payload_bytes_read=...  avg_bits_read=...  refine_calls=...
```

   - CSV 仍以 `results/${DATASET}/csv/02_diskann_fair/diskann_fair_raw.csv` 为准；plain-text log 是可审计原始日志，不替代 CSV。

9. 可选统一 rerank 补充实验。
   - 主实验无 rerank。
   - 补充实验可统一测 `rerank={32,64,100}`，所有 4bit 方法都必须打开同样的 rerank，且 raw Float32 vectors 的内存和 rerank latency 计入结果。
   - 如果某方法无法 rerank，写 `unsupported`，不要只给 Ours 或某个 baseline rerank。

10. 合并、取 median、画图。
   - 原始结果写 `diskann_fair_raw.csv`。
   - 5 次重复取 median 写 `diskann_fair_median.csv`。
   - 插值 Recall@10 = 0.90/0.95/0.97 写 `diskann_fair_interpolated.csv`。
   - 生成 QPS-Recall、Memory-Recall、Visited Nodes-Recall、Distance Calls-Recall、Bytes Read-Recall。

### 3.4 统一方法列表
实现统一 distance adapter，不复制多份 DiskANN 搜索循环：
- `DiskANN-FP32`
- `PQ-DiskANN`
- `SQ-DiskANN`
- `SAQ-DiskANN`
- `LVQ-DiskANN`
- `Ours-DiskANN`

主比较全部约 4 bit/dim，实际 metadata 计入总索引大小。每个 adapter 只负责 encode/load/query preprocess/distance，不得改变 candidate queue、visited list、termination condition、neighbor expansion 顺序。

### 3.5 关键统计
统一统计：
- Recall@10
- QPS
- latency mean/p50/p95
- DiskANN-native profile 的 `search_list_size`
- visited nodes/query
- neighbor expansions/query
- approximate distance calls/query
- refined distance calls/query
- candidate pushes/query
- search list size
- search beam width
- 平均读取 payload bytes/query
- Ours 各 bit stage 触发次数

构建阶段统一统计：
- `graph_build_time_ms`
- `train_time_ms`
- `encode_time_ms`
- `payload_import_time_ms`
- `build_time_ms`

内存统一统计：
- `graph_bytes`
- `payload_code_bytes`
- `payload_metadata_bytes`
- `codebook_bytes`
- `raw_vector_bytes_for_rerank`
- `serialized_index_size`
- `index_size_mb`
- `peak_rss_mb`
- `peak_RSS`

### 3.6 第二组最终图表
主图：
- `DiskANN_QPS-Recall@10 Pareto`
- `DiskANN_Total Index Memory-Recall@10`

解释图：
- `DiskANN_Visited Nodes-Recall`
- `DiskANN_Distance Calls-Recall`
- `DiskANN_Bytes Read-Recall`

表：
- build time（ms）
- graph build time（ms）
- payload encode/import time（ms）
- total index size
- peak RSS
- 在 Recall@10 = 0.90/0.95/0.97（实际能覆盖的阈值）处插值后的 QPS 与内存

## 4. SymphonyQG、NGT-QG、OG-LVQ、Glass-NSG vs Ours：端到端图索引系统公平实验

### 4.1 实验目的
这一组不是“统一 DiskANN”实验，而是完整系统比较。每个系统保留自己的图结构、量化方式、搜索流程、rerank 策略和数据布局，回答：

> 在相同数据、硬件和评价指标下，Ours 与当前高性能 graph-based ANN systems 相比，Recall-QPS、Recall-Memory 和构建开销是否更优？

方法包括：
- `SymphonyQG`
- `NGT-QG (Yahoo Japan)`
- `OG-LVQ (Intel SVS)`
- `Glass-NSG (Zilliz)`
- `Ours`

不要把这些系统强行改造成 DiskANN。第二组已经负责 controlled DiskANN comparison；第三组只做 end-to-end system comparison。

### 4.2 实现来源与版本固定
- SymphonyQG：优先使用作者/当前官方实现；记录实际 repo、commit、编译参数。
- NGT-QG：使用 Yahoo Japan NGT 中的 QG 实现，记录 NGT commit 和实际 QG configuration。
- OG-LVQ：使用 Intel SVS 官方实现，使用其 graph-based index + LVQ 路径，记录 SVS 版本和 LVQ configuration。
- Glass-NSG：使用 Zilliz `pyglass`/Glass 的 NSG 实现，记录版本、NSG optimization level 和所有修复 patch。
- Ours：使用当前正式 Release 实现，不为该实验单独关闭已有优化。

若官方仓库存在版本变化，先锁定 commit，再运行所有数据集，禁止不同数据集使用不同 commit。

### 4.3 复现 SymphonyQG 论文中的 baseline 选择原则
第三组优先采用与 SymphonyQG 论文一致的参数选择思想：
- 对每个系统允许构建多个合法 index configuration。
- 使用 validation query 搜索 indexing 参数。
- 对每个数据集选取在目标高 Recall 区间（主目标 Recall@10≈0.95）QPS 最好的 indexing configuration。
- 固定 indexing configuration 后，再扫 query 参数得到完整 QPS-Recall 曲线。
- 测试 query 不参与 indexing 参数选择。

不要简单要求所有系统使用相同的 `R`、`M`、`EF` 数值，因为不同系统中这些参数语义不完全相同。公平性由相同调参预算、相同 validation、相同目标 Recall 和最终 Pareto 结果保证。

### 4.4 SymphonyQG 设置
索引参数：
- `R ∈ {32, 64, 128}`
- `EF=400` 作为论文复现起点
- graph construction iteration `t=3`；大规模数据如需要可按官方实现/论文配置调整，并记录
- 在 validation 上选择最佳 R，不允许用 test query 选择

查询参数：
- 扫 SymphonyQG 的 beam/search 参数形成完整 Recall-QPS 曲线
- `k=10`
- 不额外加入外部 Float32 rerank
- 统计其内部 implicit refinement / exact-distance 路径实际成本（如果代码可可靠统计）

硬件：
- 运行前检查 SIMD/CPU requirements
- 如果官方实现要求 AVX-512 而机器不支持，则该机器上的 QPS 不能作为最终速度结论；必须在支持官方优化路径的统一机器上重跑

### 4.5 NGT-QG 设置
NGT-QG 参数较多，不强行与其他方法用相同 R/EF。
- 使用 NGT 官方 QG 实现。
- 优先参考官方推荐配置和 ANN-Benchmarks 中 NGT-QG 的 suggested parameter groups。
- 对每个数据集在 validation 上搜索 indexing parameters 和 query parameters。
- NGT-QG 使用 PQ/FastScan 导航及其原生 reranking 流程时，保留原始设计，不人为删除 rerank。
- rerank 候选数、search size、epsilon 等所有影响 Recall/QPS 的参数必须写入 manifest。
- 总内存必须包含 graph、PQ/code data、复制到邻居侧的量化 codes、原始向量（如果 rerank 需要）及其他常驻 metadata。
- 构建时间必须包含 QG graph optimization/quantization 等完整流程，统一输出 `build_time_ms`。
- 如果某数据集在合理资源预算内无法达到目标 Recall，明确报告最高可达 Recall，不隐藏失败点。

查询 sweep：
- 不要求与 Ours 的 `efSearch` 数值一致。
- 扫足够密的 search/rerank 参数组合，构建 NGT-QG 自己的 Pareto frontier。
- 为避免参数组合数量远高于其他方法，给所有系统相同的 validation tuning budget，例如固定最大配置次数。

### 4.6 OG-LVQ 设置
使用 Intel SVS 的 graph-based LVQ 路径。
- graph pruning 参数 `alpha=1.2` 作为论文复现默认值。
- indexing 参数搜索 `R` 和 construction/search window/EF 对应参数。
- LVQ configuration 至少测试官方常用配置：`LVQ-8`、`LVQ-4x8`、`LVQ-8x8`。
- 每个数据集只允许通过 validation 选择最终 LVQ configuration，不允许测试集挑最好值。
- 主系统比较使用官方 SVS 最优化 query kernel 和正式 compression layout，不使用手写低效 LVQ 替代。
- 记录原始向量是否被保留、二级 residual 是否存在以及这些数据对应的实际内存。

查询 sweep：
- 扫 SVS beam/search-window 参数形成 QPS-Recall curve。
- `k=10`。
- 统计 total index size、peak RSS、build time（ms）、QPS、mean/P95 latency、Recall@10。

### 4.7 Glass-NSG 设置
使用 Zilliz Glass/pyglass 提供的 NSG。
- indexing 参数搜索 `R` 和 `EF`/construction 参数。
- Glass 的 optimization level 必须进入 validation 搜索，因为不同 level 可能显著影响性能和 Recall。
- 对每个数据集记录最终选择的 optimization level。
- 如官方版本存在必须修复才能运行的 bug，保存 patch，并在 `EXPERIMENT_AUDIT.md` 中说明；不能静默修改算法。
- 该方法不做额外量化适配，保留原生 Glass-NSG 路径。

查询 sweep：
- 扫 beam/search 参数得到 Recall-QPS 曲线。
- `k=10`。
- 记录 total index size、peak RSS、build time（ms）、QPS、mean/P95 latency、Recall@10。

### 4.8 Ours 设置
Ours 保持最终论文方法的完整路径。
- 使用正式 Float32 build + quantized payload/progressive search 流程。
- 所有 progressive bit stages、residual、scale、metadata 都计入实际 index size。
- 不额外使用 baseline 没有的外部 Float32 rerank。
- 若 Ours 本身的算法定义包含 refinement，则正常启用，并记录 refinement ratio、avg bits read 和 refine calls。
- indexing 参数只能在与其他系统相同的 validation tuning budget 下选择。

查询 sweep：
- 扫 `efSearch` 或 Ours 的主要 search budget 参数。
- `k=10`。
- 输出完整 Recall-QPS 曲线，不只报告单点。

### 4.9 三种公平比较 Track

#### Track A：Best-tuned end-to-end
这是第三组的主实验。
- 每个系统允许使用自身最合适的 indexing 参数。
- 使用完全相同 validation set。
- 每个系统允许相同最大调参次数。
- indexing configuration 选择目标统一为高 Recall 区域下的最佳 QPS。
- 固定 index 后扫 query 参数形成完整 Pareto。
- 最终比较谁在 Recall@10=0.90、0.95、0.97 等共同可达点上 QPS 更高。

#### Track B：Degree-matched
作为解释性实验，不作为唯一主结论。
- SymphonyQG、OG-LVQ、Glass-NSG 等 graph max degree 尽量设置相同 `R`。
- Ours 根据 HNSW 实际 base-layer degree capacity 选择等效 M。
- NGT-QG 若其 topology/degree 参数无法严格等价，使用最接近合法配置并明确记录。
- 比较相近 graph degree 下的 QPS、Recall 和内存。

#### Track C：Memory-matched
这是空间效率的重要实验。
- 调整各系统合法配置，使 total serialized/resident index size 尽量落在目标预算 ±5%。
- 比较同等总索引内存下的 QPS-Recall。
- 不能只匹配 vector-code bytes，必须匹配 graph + codes + raw vectors + duplicated neighbor codes + metadata 的总空间。

### 4.10 统一查询规则
- `k=10`
- 同一 query order
- 同一 ground truth
- 同一线程数
- 同一 CPU affinity / NUMA
- 同一 warm-up 规则
- 每个最终配置重复至少 5 次
- 主要报告 median QPS
- 查询 latency 输出 us；构建时间输出 ms
- 不比较“相同 EF 数字”，因为不同系统的 EF/beam/search-window 语义不同
- 主要比较完整 Pareto 以及固定 Recall 下的插值性能

### 4.11 第三组统一统计
每个系统至少记录：
- `Recall@10`
- `QPS`
- `latency_mean_us`
- `latency_p50_us`
- `latency_p95_us`
- `build_time_ms`
- `train_time_ms` / `encode_time_ms`（方法存在这些阶段时）
- `serialized_index_size_mb`
- `peak_rss_mb`
- graph degree / indexing configuration
- query search parameters
- rerank 参数（方法存在时）
- raw vector 是否常驻
- SIMD ISA
- threads
- commit hash

内部机制统计若定义一致则比较；若定义不同，只用于各方法内部解释：
- visited nodes
- expanded nodes
- distance calls
- quantized distance calls
- exact distance calls
- rerank count
- bytes read
- refinement count

### 4.12 第三组最终图表
必须生成：
- `System_QPS_Recall`: SymphonyQG / NGT-QG / OG-LVQ / Glass-NSG / Ours
- `System_Memory_Recall`
- `System_QPS_Memory_at_95Recall`
- `System_P95Latency_Recall`
- `System_BuildTime_ms`
- `System_IndexSize`

主表：
- 95% Recall 下 QPS
- 95% Recall 下 P95 latency
- total index size
- peak RSS
- build time（ms）
- 是否需要 raw Float32
- 是否显式 rerank
- SIMD requirement

如果某方法达不到 95% Recall，则报告其最大 Recall 和对应 QPS，不进行虚假插值。

## 5. Codex 具体执行顺序
1. 先扫描当前仓库，找到 Ours 的构建入口、query 入口、distance kernel、payload 格式、现有图搜索循环、日志格式和数据路径；不要先改算法。
2. 先补齐 0.4 中列出的代码入口和脚本；如果短期只实现一部分 baseline，对缺失方法必须在 manifest 中写 `unsupported`，不能静默跳过。
3. 对每个 `${DATASET}` 创建：
   - `experiments/01_quantizer_fair/`
   - `experiments/02_diskann_fair/`
   - `experiments/03_system_fair/`
   - `experiments/common/`
   - `results/${DATASET}/manifests/`
   - `results/${DATASET}/raw/`
   - `results/${DATASET}/csv/`
   - `results/${DATASET}/indexes/`
   - `results/${DATASET}/figures/`
   - `results/${DATASET}/audit/`
4. 在 `experiments/common/` 实现统一 dataset loader、groundtruth/recall、timer、RSS/index-size、hardware-info、CSV writer。已有可靠实现优先复用。
5. 统一 timer：构建、训练、编码、图导入等阶段最终全部转换成毫秒写日志；查询延迟继续以微秒输出。
6. 编译必须使用 `build/experiments-${DATASET}`，运行必须传 `--dataset ${DATASET}`，runner 必须从 `data/${DATASET}/` 解析 base/query/groundtruth。
7. 第一组先完成 PQ/SQ/SAQ/LVQ/Ours adapter 和 smoke test，并生成固定 candidate 文件后再跑 full benchmark。
8. 第二组验证所有 DiskANN adapter 读的是同一份 shared Vamana graph，可通过 graph checksum/hash 验证；checksum 不一致直接报错。
9. 第三组依次接入 SymphonyQG、NGT-QG、OG-LVQ、Glass-NSG；每个系统先锁定官方 repo/commit，再建立 wrapper，禁止直接改算法以适配统一接口。
10. 第三组先做 hardware compatibility 检查，尤其记录 AVX2/AVX-512。
11. 为第三组建立统一 validation tuning runner，保证五个系统调参预算一致。
12. 每一组先产出一个 `*_manifest.csv`，列出 dataset、method、参数、commit、binary、hardware、输入文件路径、输出目录和状态，再运行 full experiment。
13. full run 完成后自动合并 CSV、去掉 warm-up、按 5 次重复取 median，生成论文图。原始日志不能删除。
14. 最后生成 `EXPERIMENT_AUDIT.md`：写清每个 baseline 是官方原生实现、统一 DiskANN adapter 还是 paper-equivalent implementation，以及所有与原论文设置不同的地方。

## 6. 时间单位规范
严格执行：
- `build_time_ms`：毫秒
- `graph_build_time_ms`：毫秒
- `train_time_ms`：毫秒
- `encode_time_ms`：毫秒
- `payload_import_time_ms`：毫秒
- `latency_mean_us`：微秒
- `latency_p50_us`：微秒
- `latency_p95_us`：微秒
- `ns_per_distance`：纳秒，仅用于距离 kernel microbenchmark

底层 API 返回秒时，乘以 1000 后写入 `*_ms`；返回微秒时，除以 1000 后写入 `*_ms`。日志和 CSV 中禁止出现 `build_time_us`、`train_time_us`、`encode_time_us`。

## 7. 禁止事项
- 禁止用不同 query/GT。
- 禁止只给 Ours 预热或 CPU pinning。
- 禁止某个方法多线程、另一个单线程后直接比较 QPS。
- 禁止只按 nominal bpd 声称内存优势而不统计 metadata/graph/raw vectors。
- 禁止 PQ/SQ 用 Flat、SAQ 用 IVF、LVQ 用 SVS graph 后把 QPS 放在同一张“量化器公平”图。
- 禁止第二组不同 DiskANN baseline 各自重新构图。
- 禁止第二组把 DiskANN graph 导出后改写成 HNSW 搜索循环再比较。
- 禁止第三组把 NGT-QG、OG-LVQ、Glass-NSG、SymphonyQG 强行改造成 DiskANN。
- 禁止第三组要求所有系统使用相同 EF 数字后宣称公平。
- 禁止 NGT-QG 忽略原生 rerank 所需 raw-vector memory。
- 禁止在不满足官方 SIMD/hardware 要求的机器上用降级结果做最终速度优劣结论。
- 禁止测试集调参和 cherry-pick 单点；主结论来自完整 Pareto。
- 禁止构建时间使用 us；所有构建/训练/编码时间统一为 ms。

## 8. 参考实现/论文（供 Codex 核对）
- Faiss PQ/SQ: https://github.com/facebookresearch/faiss
- SAQ: https://github.com/howarlii/saq
- LVQ / Intel SVS: https://github.com/intel/ScalableVectorSearch
- LVQ paper: https://arxiv.org/abs/2304.04759
- SymphonyQG paper: https://arxiv.org/abs/2411.12229
- SymphonyQG original repo: https://github.com/gouyt13/SymphonyQG
- RaBitQ-Library: https://github.com/VectorDB-NTU/RaBitQ-Library
- NGT / NGT-QG: https://github.com/yahoojapan/NGT
- Glass / Glass-NSG: https://github.com/zilliztech/pyglass
- DiskANN3 / diskann-quantization: https://github.com/microsoft/DiskANN/tree/main/diskann-quantization/src

## 9. 完成标准
只有同时满足以下条件才算实验框架完成：
- 可以用 `DATASET=<name>` 对 `data/${DATASET}/${DATASET}_base.fvecs`、`data/${DATASET}/${DATASET}_query.fvecs`、`data/${DATASET}/${DATASET}_groundtruth.ivecs` 命名规则下的任意数据集复现；
- 编译命令和运行命令都显式包含数据集名称；
- 每个数据集的结果都落在 `results/${DATASET}/`，不与其他数据集混写；
- 0.7 中列出的 manifest、raw log、CSV、index、figure、audit 产物能够按顺序生成或明确标记 `unsupported/failed`；
- 三组实验彼此独立；
- 所有主结果能由统一脚本重新生成；
- 每条结果可追溯到 dataset、参数、binary、commit 和硬件；
- 第二组 DiskANN graph checksum 完全相同；
- 第三组五个系统均使用官方/明确标注的实现；
- 第三组采用统一 validation tuning budget；
- 所有存储都报告 actual total bytes；
- 所有构建/训练/编码时间统一为毫秒；
- SIMD/hardware 条件被明确记录；
- 最终自动生成 QPS-Recall、Memory-Recall、QPS-Memory@95Recall、P95 latency 和 Build Time 图。
