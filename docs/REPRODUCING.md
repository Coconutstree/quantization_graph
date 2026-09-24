# 磁盘实验复现流程

所有命令在仓库根执行。当前正式入口只有 01/02/03；历史脚本在 `legacy/scripts/`。本说明串联安装、数据准备、建图、验收、测量和绘图；**本次没有在新机器执行联网安装或正式性能实验**。

## 1. 已验证环境与安装边界

`docs/validation/release_layout_20260918/environment.json` 是本机实测环境快照，包含 OS、CPU、Python、编译器、Rust、CMake、包版本和 Git dirty 状态。对应构建/回归记录在 `docs/validation/results_layout_20260918/`。本机是 Ubuntu 22.04，Python 3.13.12；C++ 使用 GCC 11，含 AVX-512 路径，需要 Linux O_DIRECT/io_uring。环境中另有仓库本地解包的 Ubuntu noble 依赖，不能把它解释为纯净 Ubuntu 22.04 已验证安装。

`requirements-tested.txt` 固定实测的直接 Python 依赖；它不是含所有传递依赖和下载哈希的完整 lock。原 `requirements.txt` 保留宽版本开发要求。`environment.yml` 已与实测 Python 版本和直接依赖清单对齐；Conda 安装本身未重新验证。

新机器需准备 Git、wget、Ninja、GCC/G++ 11、Python 3.13、Rust/Cargo、numactl、Linux AIO/OpenSSL/OpenBLAS 开发环境。可使用 Conda 创建隔离环境：

```bash
conda env create -f environment.yml
conda activate quantization-graph
python -m pip check
```

已有 Python 3.13 时，也可使用 `python3.13 -m venv .venv`，激活后 `python -m pip install -r requirements-tested.txt`。Rust 的准确实测版本见环境快照；Cargo 使用仓库的 Cargo.lock，构建带 `--locked`。

## 2. 第三方源码、运行库及构建

源码版本以 `baselines/DEPENDENCY_LOCK.json` 为准，不能用最新分支替代。当前 checkout 中部分第三方目录被 Git 忽略，单独复制主仓库不等于携带了这些依赖。

```bash
JOBS=4 CXX_BIN=g++-11 bash scripts/setup_deps.sh
```

该脚本准备 Faiss、SAQ、SymphonyQG 及本地 C/C++ 依赖。`setup_cpp_deps_local.sh` 使用 apt 下载指定包名（包括 noble 的 t64 包），需要兼容的软件源或预先提供匹配的 `.deb` 到 `baselines/deps/`；不能在不匹配的发行版上直接假定成功。

完整构建还包含补充端口，因而需要下面的源码。仅在目录不存在时执行，已有目录应核对版本，不覆盖：

```bash
git clone https://github.com/zilliztech/pyglass baselines/pyglass
git -C baselines/pyglass checkout d2296ec447d2374ee8f88c6d3b85be1b1e434ad3
git -C baselines/pyglass submodule update --init --recursive
git clone https://github.com/intel/ScalableVectorSearch baselines/svs
git -C baselines/svs checkout 078846e3f76829b60f5c256806da1b90e385cda5
git -C baselines/svs submodule update --init --recursive
git clone https://github.com/google/glog baselines/deps/glog
git -C baselines/deps/glog checkout 53d58e4531c7c90f71ddab503d915e027432447a
python -m pip install --no-deps --target baselines/svs/python scalable-vs==0.4.0
python -m pip install --no-deps --target baselines/pyglass/python glass==2.1.0
```

SVS 端口需要官方 wheel 提供的 `svs/include` 和 `svs/lib64`；仅有开源源码不够。如果当前平台没有相应 wheel，构建仍会受阻，应记录该平台缺口。源码版本和二进制包版本分别核查。

```bash
JOBS=4 FORMAL_CXX_BIN=g++-11 bash scripts/build_formal_local.sh
bash scripts/setup_fio_local.sh
python scripts/write_05_ports_local.py
python scripts/capture_environment.py --output work/reproduction_environment.json
```

fio 安装同样需要匹配的软件包源。正式 03 的 AiSAQ/Starling 安装命令为 `python scripts/setup_disk_baselines.py --jobs 4`，需要 MKL、Boost、libaio、tcmalloc、liburing 开发库；参见 `docs/plans/OFFICIAL_DISK_BASELINES.md`。安装成功不代表正式验收通过。

## 3. 数据准备与建图

新机器可下载 GIST/AGNews；本次整理未执行下载，也未改动当前 `data/`：

```bash
DATASETS="gist agnews" bash scripts/download_data.sh
python scripts/check_datasets.py --datasets gist agnews --data-root data --out-root artifacts/dataset_manifests
```

转换器位于 `scripts/data_tools/convert_hdf5_to_ann.py`，读取 HDF5 的 train/test/neighbors，按原顺序输出 base/query/groundtruth，不重新计算或改变距离语义。

DBpedia 的旧下载脚本引用了仓库中不存在的 NPY/JSONL 转换程序，故不再默认宣称可自动准备。复现该数据集目前需提供原实验相同的三个 fvecs/ivecs 文件并核查来源、维度、metric、划分及哈希；这是尚未闭合的数据准备环节。SIFT10M 等独立准备工具在 `scripts/prepare_*.py`，具体参数用 `--help` 查看。

每个数据集应有 `data/<dataset>/<dataset>_base.fvecs`、`_query.fvecs`、`_groundtruth.ivecs`。输入检查通过不代表准入通过；正式运行还会核验 metric 和预算。

```bash
DATASETS="gist" GRAPH_ROLES="shared ours diskann" BUILD_ONLY=1 THREADS=32 \
  bash scripts/local_runs/build_05_core_graphs.sh
```

建图可能很耗时并占用大量磁盘。图写入 `artifacts/graphs/`；BUILD_ONLY=1 不执行查询性能测量。共享查询划分若存在会复用并检查数量，否则由 export 阶段生成到 `artifacts/query_splits/runs/<run-id>/`。

## 4. 检查与正式运行

```bash
python -m unittest discover -s tests -p 'test_*.py'
python -m unittest discover -s src/disk_bench/tests -p 'test_*.py'
ctest --test-dir build/disk/native --output-on-failure
python scripts/run_disk_experiments.py --phase doctor --layers 01,02 --datasets gist
```

DiskANN 测试需要宿主允许 io_uring。Ours 正式测量需要可写且启用 memory controller 的 cgroup v2 委派目录，指定 `--cgroup-parent`；缺少权限时环境检查应失败，不应跳过内存约束。还需要有效 CPU 集合和 NUMA 节点。

以下是 **01/02 的命令模板**。替换存储路径、实际存储类型、CPU/NUMA 和 cgroup 路径；同一 run-id 的所有阶段保持相同参数。32 workers 需要至少 32 个允许使用的逻辑 CPU。

```bash
RUN_ID=gist_disk_main_01_02
DISK_ROOT=/path/to/experiment-disk
CGROUP_PARENT=/sys/fs/cgroup/your-delegated-group
for PHASE in export validate tune run; do
  python scripts/run_disk_experiments.py \
    --phase "$PHASE" --layers 01,02 --datasets gist --run-id "$RUN_ID" \
    --disk-root "$DISK_ROOT" --disk-profile nvme \
    --cpu-affinity 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31 \
    --numa-node 0 --cgroup-parent "$CGROUP_PARENT" --workers 32 --repeats 1 || break
done
python scripts/plot_disk_experiments.py --results-root results \
  --run-id "$RUN_ID" --layers 01,02 --datasets gist
```

03 使用独立 run-id 和 `--layers 03`。当前 AiSAQ/Starling 仍 pending，完整 03 默认方法集合会被拒绝；不得将减少方法的试验标作完整系统比较。解决端口准入证据后再按同样阶段运行。

## 5. 输出和论文图表

`results/<experiment>/<dataset>/<run-id>/raw/` 保存测量证据，`tables/` 保存通过协议检查的 CSV，`figures/` 保存图表。公共协议、环境和输入哈希在 `results/manifests/<run-id>/`。将环境快照一同保存，发布时保留核验所需证据或提供下载位置，不能只依赖默认 Git 忽略规则。

实际论文图号、命令、run-id 和源数据的对应关系见 `paper/FIGURE_PROVENANCE.md`。新正式运行尚未完成，表中不能填写虚构 run-id。当前草稿图可从冻结快照重绘，但重绘不等于重跑实验或完成验收。
