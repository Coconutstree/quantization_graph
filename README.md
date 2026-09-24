# 磁盘 ANN 论文实验代码

01–03 按从量化组件到完整系统组织；04 为 Ours 优化与消融，05 单独比较不同内存预算。

| 实验 | 入口 | 研究问题 |
|---|---|---|
| 01：量化与 I/O | `experiments/01_disk_quantizer/run.py` | 固定候选条件下的量化精度、计算成本、驻留/SSD payload 和读取开销 |
| 02：共享图检索 | `experiments/02_disk_shared_graph/run.py` | 共享基线图上的 PQ/SQ/SAQ 对比，以及明确标注自身图的 Ours 对照 |
| 03：完整磁盘系统 | `experiments/03_disk_system/run.py` | Ours、DiskANN、Starling、AiSAQ；4 GiB RSS、beam=4、32 workers、单轮 |
| 04：Ours 优化与消融 | `experiments/04_ours_memory_budget/` | 缓存策略、PCA、residual 等机制的独立消融 |
| 05：内存预算 | `experiments/05_memory_budget/run.py` | 相同 0.5/2/4/8 GiB 预算下的跨方法 Recall、QPS、I/O 和 RSS |

02 中 Ours 仍使用自己的量化建图，不能把这一对照描述成所有方法共享同一张图。03 当前不纳入 SymphonyQG；执行规则见 [固定 beam 官方对照方案](docs/plans/03_FIXED_OFFICIAL_BEAM4.md)。各 baseline 的正式准入状态由端口登记和证据校验决定；目录整理不会把 pending/blocked 方法改为已验收。

## 代码结构

```text
experiments/
  01_disk_quantizer/
    run.py                 # 量化与 I/O 实验入口
    native/                # PQ/SQ/Ours/SAQ 磁盘端口和 CMake 目标
    tools/                 # 量化共用实现、固定候选生成器
  02_disk_shared_graph/
    run.py                 # 共享图实验入口
    native/                # Rust 搜索入口、磁盘 provider、Ours 路径、I/O bridge
  03_disk_system/
    run.py                 # 完整系统实验入口
    native/                # Glass/Symphony/OG-LVQ 端口和 CMake 目标
    native_diskann/         # 官方 DiskANN 磁盘端口、缓存与 reader
    adapters/              # 官方 AiSAQ/Starling 调用、诊断和 SVS worker
  04_ours_memory_budget/   # Ours 优化和机制消融
  05_memory_budget/
    run.py                 # 独立内存预算入口，复用现有磁盘系统实现
src/
  disk_bench/              # 共用调度、协议、绘图、I/O 与跨层测试
  graph_core/              # 02/03 共用的建图算法、Rust crate、Ours C++ bridge
Ours/core/                 # 跨层复用的 Ours 量化/Vamana 算法
baselines/                 # 第三方实现、版本锁与补丁
scripts/                   # 安装、构建、数据准备和运行脚本
legacy/                    # 旧实验（含旧 Ours/experiments），非正式入口
logs/                      # 运行终端输出、建图日志、队列状态，非源码
results/                   # 按实验编号保存结果、诊断和历史归档
artifacts/                 # 图、索引、查询划分等可复用的大文件
```

`data/` 中的数据未迁移。图索引迁至 `artifacts/`，文件内容和哈希保持不变；`work/` 保留为工作目录。native 二进制名称、内部 `05a/05b/05c` 协议 ID 保持兼容；01/02/03 分别使用它们，新的独立 05 内存预算实验复用 `05c` 系统端口，以 `experiment=05_memory_budget` 区分。

## 构建

安装与硬件要求沿用 `environment.yml`、`requirements.txt`、`scripts/setup_cpp_deps_local.sh`、`scripts/setup_deps.sh` 及 `baselines/DEPENDENCY_LOCK.json`。代码包含 AVX-512 优化和 Linux direct I/O / io_uring 路径，需要相应 CPU、系统库及权限。

从干净环境准备依赖、数据和图索引，到生成图表的完整步骤见 [复现说明](docs/REPRODUCING.md)。已验证环境快照和直接依赖版本分别在 `docs/validation/release_layout_20260918/environment.json`、`requirements-tested.txt`；新机器全流程安装尚未验证。

完成依赖准备后构建：

```bash
JOBS=4 bash scripts/build_formal_local.sh
python scripts/write_05_ports_local.py
```

C++ 磁盘程序输出到 `build/disk/native/`，候选生成器输出到 `build/disk/01_disk_quantizer/`；Rust 程序在 `src/graph_core/target/release/` 和 `baselines/diskann/target/release/`。顶层 CMake 入口继续可用，正式构建不再构建旧 03 内存检索程序。原生 AiSAQ/Starling 安装与验收见 `docs/plans/OFFICIAL_DISK_BASELINES.md`，其中旧源码路径按 `docs/EXPERIMENT_LAYOUT.md` 映射。

## 运行

先检查所选方法及数据集，不会启动正式测量：

```bash
python experiments/01_disk_quantizer/run.py --phase doctor --datasets gist
python experiments/02_disk_shared_graph/run.py --phase doctor --datasets gist
python experiments/03_disk_system/run.py --phase doctor --datasets gist
python experiments/05_memory_budget/run.py --phase doctor --datasets gist --search-dram-budget-gib 0.5
```

每个独立入口仅允许自己的层。也可统一运行三层：

```bash
python scripts/run_disk_experiments.py --phase doctor --layers 01,02,03 --datasets gist
```

测量阶段为 `export → validate → tune → run → plot`。跨阶段必须保持同一 run-id 和配置；独立入口使用不同 run-id；要将三层放到同一个 run-id，使用统一入口并始终选择同一组 layers。

以下示例需把 SSD 路径、CPU 集合和 NUMA 节点替换成当前机器的有效值；`doctor` 通过及正式准入通过后执行：

```bash
RUN_ID=gist_disk_20260918
SSD_ROOT=/path/to/ssd/qgraph
CPU_LIST=$(seq -s, 0 31)
for PHASE in export validate tune run plot; do
  python scripts/run_disk_experiments.py \
    --phase "$PHASE" --layers 01,02 --datasets gist \
    --run-id "$RUN_ID" --disk-root "$SSD_ROOT" --disk-profile nvme \
    --cpu-affinity "$CPU_LIST" --numa-node 0 --workers 32 --repeats 1 || break
done
```

上面的分阶段示例用于 01/02；03 使用 [独立固定参数队列](experiments/03_disk_system/README.md)，不执行 tune。03 主实验统一使用 4 GiB（[预算预检依据](docs/analysis/03_common_budget_20260921/report.md)）；0.5/2/4/8 GiB 的预算扫描独立使用 [05 内存预算入口](experiments/05_memory_budget/README.md)，每个预算使用新的 run-id。所有方法共用计划 RAM 预算，按实测进程 RSS 验收，不要求 cgroup 或 sudo。CPU/NUMA 与算法准入检查仍保留；新协议不混入旧的 Ours 单独限额结果，不能把 doctor 或小样本测试当作正式论文结果。

## 输出与论文图表

新结果按“实验 → 数据集 → 运行”组织：

```text
results/
  01_disk_quantizer/<dataset>/<run-id>/
  02_disk_shared_graph/<dataset>/<run-id>/
  03_disk_system/<dataset>/<run-id>/
  05_memory_budget/<dataset>/<run-id>/
    manifest.json         # 来源、验收状态、公共运行记录的相对路径
    raw/<method>/<phase>/ # 原生结果、逐查询记录、资源证据及日志
    manifests/            # 调参记录和参数锁定
    tables/               # 汇总 CSV 及其证据清单
    figures/              # 从通过验收的数据生成的图表
  manifests/<run-id>/      # 公共协议、输入哈希和运行环境
  diagnostics/            # 诊断、smoke 测试和失败记录
  archive/                # 旧内存实验及其他历史材料
artifacts/                # 图、索引、查询划分，不是论文结果
```

每个运行只保留一份原始证据。`--out-root` 可替换结果根目录。绘图命令：

```bash
python scripts/plot_disk_experiments.py \
  --results-root results --run-id gist_disk_20260918 \
  --layers 01,02,03 --datasets gist
```

不跨 run-id 混合配置，不把诊断/失败记录当作有效性能点，不从单次运行生成跨运行中位数或误差条。详见 `docs/RESULTS_LAYOUT.md`。

## 验证与历史代码

```bash
python -m unittest discover -s tests -p 'test_*.py'
python src/disk_bench/tests/test_native_contract.py
python src/disk_bench/tests/test_disk_suite.py
python -m unittest discover -s src/disk_bench/tests -p 'test_*.py'
cargo check --offline --locked --manifest-path src/graph_core/Cargo.toml --bins
ctest --test-dir build/disk/native --output-on-failure
```

原生测试需要先构建。DiskANN io_uring 测试需要宿主允许该系统调用。`legacy/` 用于保存旧实验与研究记录，不参与当前主实验构建；旧脚本有历史路径和环境要求，不能作为本版本正式论文运行入口。

本次验证与仍存在的正式运行限制见 `docs/DISK_REFACTOR_VALIDATION_20260918.md`。

脚本职责见 [scripts/README.md](scripts/README.md)，论文图表的命令、run-id 与数据对应见 [paper/FIGURE_PROVENANCE.md](paper/FIGURE_PROVENANCE.md)。
