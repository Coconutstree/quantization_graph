#!/usr/bin/env python3
"""Build the complete local Markdown report for the GIST1M search sweep."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path


CODEC_ORDER = {"full": 0, "b1": 1, "int4": 2, "int8": 3}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def f(row: dict[str, str], key: str) -> float:
    return float(row[key])


def complete_table(rows: list[dict[str, str]]) -> str:
    header = (
        "| ef | codec | Recall@10 | QPS mean | QPS range | latency (µs) | "
        "gate calls/q | remaining 4-bit/q | prune | kernel cost (µs) | QPS/full |\n"
        "|---:|:---|---:|---:|:---|---:|---:|---:|---:|---:|---:|"
    )
    body = []
    for row in sorted(rows, key=lambda r: (int(f(r, "ef_search")), CODEC_ORDER[r["codec"]])):
        body.append(
            "| {ef:d} | {codec} | {recall:.6f} | {qps:.1f} | {qmin:.1f}–{qmax:.1f} | "
            "{latency:.1f} | {gate:.1f} | {remaining:.1f} | {prune:.1%} | {kernel:.1f} | {ratio:.3f}× |".format(
                ef=int(f(row, "ef_search")),
                codec=row["codec"],
                recall=f(row, "recall_at_10"),
                qps=f(row, "qps_mean"),
                qmin=f(row, "qps_min"),
                qmax=f(row, "qps_max"),
                latency=f(row, "latency_mean_us"),
                gate=f(row, "gate_calls"),
                remaining=f(row, "remaining_4bit_calls"),
                prune=f(row, "prune_rate"),
                kernel=f(row, "coarse_kernel_us_mean"),
                ratio=f(row, "qps_vs_full"),
            )
        )
    return header + "\n" + "\n".join(body)


def target_table(rows: list[dict[str, str]]) -> str:
    header = (
        "| Recall 门槛 | codec | 选择 ef | 实测 Recall@10 | QPS mean | QPS range | remaining 4-bit/q |\n"
        "|---:|:---|---:|---:|---:|:---|---:|"
    )
    body = []
    for row in rows:
        body.append(
            "| {target:.3f} | {codec} | {ef:d} | {recall:.6f} | {qps:.1f} | {qmin:.1f}–{qmax:.1f} | {remaining:.1f} |".format(
                target=f(row, "target_recall"),
                codec=row["codec"],
                ef=int(f(row, "selected_ef")),
                recall=f(row, "achieved_recall"),
                qps=f(row, "qps_mean"),
                qmin=f(row, "qps_min"),
                qmax=f(row, "qps_max"),
                remaining=f(row, "remaining_4bit_calls"),
            )
        )
    return header + "\n" + "\n".join(body)


def representative_table(rows: list[dict[str, str]]) -> str:
    selected = [row for row in rows if int(f(row, "ef_search")) in (100, 200, 500)]
    return complete_table(selected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    aggregate = read_csv(args.analysis_dir / "aggregate.csv")
    targets = read_csv(args.analysis_dir / "target_recall.csv")
    validation = json.loads((args.analysis_dir / "validation.json").read_text(encoding="utf-8"))
    max_case = validation["max_qps_range_case"]

    report = f"""# GIST1M 1-bit 数据图搜索：四种 Query Codec 的 Recall–QPS / Recall–cost

生成日期：{date.today().isoformat()}

## 技术摘要

复用现有 `gist_Ours_R64_Lbuild400.graph.bin` 图索引，对 GIST1M test split 的 800 个 query 单线程逐个搜索，并扫描 `efSearch={{20,40,60,80,100,200,300,400,500}}`。正式测量做两轮，codec 顺序互为反向，时间取两轮均值并保留观测范围。

当前实现下，**INT4/INT8 query 已经比 full 快，而 b1 最慢**：

- `ef=100`、Recall@10 约 0.95 时，QPS 为 full 766、b1 506、INT4 813、INT8 819。
- `ef=500`、Recall@10 约 0.986 时，INT4/INT8 比 full 快 13.5%/13.9%，b1 比 full 慢 32.4%。
- full、INT4、INT8 的 Recall–cost 曲线基本重合；b1 因剪枝较弱，需要约 1.9–2.0 倍后续 4-bit 距离计算。

这不表示低 bit 算术天然更快，而是当前 INT4/INT8 direct gate 同时获得了较低 gate 开销与接近 full 的剪枝率。此前图中 INT4/INT8 较慢对应旧的 529B record staging 路径，不能直接外推到当前代码路径。

## 搜索语义和指标定义

四种策略复用同一张 Vamana 图，数据库侧的数据表示不随 query codec 改变：

- `full`：数据库 gate 使用 1-bit MSB sidecar，query 保持 FP32。
- `b1`：数据库 gate 仍为 1-bit，query 使用 1-bit 对称编码。
- `int4`：数据库 gate 仍为 1-bit，query 使用 INT4 编码。
- `int8`：数据库 gate 仍为 1-bit，query 使用 INT8 编码。

搜索是两阶段而不是单一距离从头算到尾：所有新候选先经过 1-bit 数据 gate；未被剪枝的候选再访问 4-bit compact payload 完成遍历距离；最终 top-100 使用 full query / residual rerank。因此：

- `Recall@10`：搜索 top-10 与 ground-truth top-10 的平均命中率。
- `QPS`：`1,000,000 / latency_mean_us`，报告值为两轮算术均值。
- `gate calls/q`：每 query 平均 1-bit gate 调用数。
- `remaining 4-bit/q`：通过 gate 后每 query 平均 4-bit payload 距离次数，是 Recall–cost 的主要算法成本。
- `kernel cost`：`paper_batch_us + flush_us`，用于补充算法调用次数无法表达的实际 CPU 时间。

## Recall–QPS：INT4/INT8 位于主要 Pareto 前沿

横轴是 Recall@10，纵轴是两轮平均 QPS；每条线按 efSearch 从 20 连到 500。垂直范围线表示两轮观测 min/max，而不是置信区间。INT4/INT8 在中高 recall 段持续位于 full 上方，b1 则明显更低。

![Recall@10 vs QPS](figures/recall_qps.svg)

在 `ef=100/200/500`，INT8 相对 full 的 QPS 分别为 `+7.0%/+11.2%/+13.9%`，INT4 为 `+6.2%/+9.8%/+13.5%`。低 ef 下 INT4 与 INT8 的细小名次落在运行波动范围内，不宜宣称其中一个稳定更快。

## Recall–cost：b1 的便宜 gate 被弱剪枝反噬

左图用后续 4-bit 距离次数衡量算法成本，右图用实测粗筛核耗时衡量机器成本。full、INT4、INT8 的算法成本曲线几乎重合；b1 明显右移。

![Recall@10 vs search cost](figures/recall_cost.svg)

以 `ef=100` 为例，四者都检查约 4,783 个 gate 候选；full/INT4/INT8 的剪枝率约 77%，只留下 1,177–1,181 次 4-bit 距离，而 b1 剪枝率为 54.4%，留下 2,234 次。对应粗筛核耗时为 full 941µs、b1 1,570µs、INT4 783µs、INT8 774µs。

`ef=500` 时，b1 需要 7,083 次后续距离，而其他三者约 3,557–3,569 次。b1 的 1-bit query gate 虽然单次便宜，但增加的幸存者把收益全部吃掉。

## 代表点精确结果

下面列出 `ef=100/200/500` 的完整核心指标；全部 36 点见下一节。

{representative_table(aggregate)}

## 全部 36 个搜索点

`QPS range` 是两轮相反 codec 顺序测量的观测范围。完整 25 列聚合数据保存在 [`aggregate.csv`](aggregate.csv)，原始未聚合记录见文末文件清单。

{complete_table(aggregate)}

## 固定 Recall 门槛的最快离散点

这里不做插值：对每种 codec，选择实测 Recall 达到门槛的离散 ef 点中 QPS 最高者。INT4 在 `ef=200` 的 Recall 为 0.977875，只比 0.978 门槛低 0.000125，因此严格规则会把它推进到 `ef=300`；这类细小差异不应解读为稳定质量差距。

{target_table(targets)}

## 实验范围与方法

| 项目 | 设置 |
|:---|:---|
| 数据集 | GIST1M，1,000,000 个 960 维数据点 |
| query / GT | 800 个 test query；每条 GT 含 100 个邻居 |
| 图索引 | Vamana，`R=64`，`Lbuild=400`，复用现有 graph binary |
| 搜索 | `k=10`，rerank candidates=100，单线程逐 query |
| efSearch | `20,40,60,80,100,200,300,400,500` |
| 定位 | payload 在 NUMA node 0 的 20 个物理核 first-touch；搜索固定 CPU 20 |
| 正式重复 | 两轮，codec 顺序 `full,b1,int4,int8` 与反向顺序 |
| QPS 汇总 | 两轮算术均值，同时报告 min/max |
| LUT | 本轮使用当前 direct SIMD gate；未额外加入 LUT 路径 |

每轮会重新确定性编码 payload，但直接复用同一个图；payload 编码和图加载不计入搜索 QPS。第一轮原始结果使用 forward codec 顺序，第二轮反向，以降低 codec 顺序、冷页和缓存热状态的混杂。

## 一致性与鲁棒性

聚合脚本对两轮每个 `(codec, efSearch)` 强制检查 9 个确定性字段：Recall、访问节点、距离调用、gate 检查/剪枝、gate 调用、后续 4-bit 调用、payload 字节量和平均读取 bit 数。36 个点全部通过，差异数为 `{validation['deterministic_mismatch_count']}`。

最大 QPS 轮次范围出现在 `{max_case['codec']}, ef={max_case['ef_search']}`：`{max_case['qps_min']:.1f}–{max_case['qps_max']:.1f}`，相对均值 `{validation['max_qps_range_pct']:.1%}`。只有两轮，因此范围只能用于识别运行顺序/热状态噪声，不能当作置信区间。

## 为什么这次与此前“INT4/INT8 比 full 慢”的图不同

此前结果对应的整数 gate 会对每个候选读取并 staging 529B compact record，然后执行 packed-nibble × int8 核；其随机读取和 staging 成本超过了更强剪枝带来的收益。

当前代码路径让四种策略的数据库 gate 都读取连续的 1-bit MSB sidecar，只改变 query 表示。以 `ef=100` 为例：

- full gate 为 666µs，后续阶段 274µs，总粗筛核 941µs。
- INT8 gate 为 416µs，后续阶段 358µs，总粗筛核 774µs。
- INT4 与 INT8 基本相同，总粗筛核 783µs。

所以当前 INT4/INT8 获胜的直接原因是 direct integer query gate 比 FP32 masked accumulation 更便宜，而剪枝和后续调用数又几乎不变。这是实现路径变化后的实测结论，不是对所有 INT4/INT8 算法的一般结论。

## 限制与后续建议

- 只有两轮正式测量，适合确认大的性能排序，不足以估计稳定置信区间；生产选型建议固定 NUMA 后做 5–10 轮。
- 当前仅覆盖单搜索线程，未验证多线程下的 LLC、内存带宽和 NUMA 扩展性。
- 未采集 PMU、LLC miss、DRAM 带宽和 cycle breakdown；`remaining 4-bit/q` 是算法 cost proxy，不等于硬件 cycle。
- 800-query 下小于约 0.001 的 Recall 差异不宜单独做强结论。
- 若目标是 Recall≥0.978 或 ≥0.985，建议在 `ef=180–240` 和 `ef=360–420` 加密扫描，避免离散门槛放大小差异。

当前实现可优先使用 INT8 作为稳妥默认；若 query 存储/传输更重要，可选 INT4，其搜索速度与 INT8 基本同档。不建议把纯 b1 query 直接作为最终 gate，除非进一步提高剪枝质量或将其限制为第一级 gate。

## 完整复现命令

下面命令从仓库根目录执行。CPU 列表针对本次 Xeon Gold 6248 四 NUMA 机器；换机器时请覆盖 `ENCODE_CPUS` 和 `SEARCH_CPU`。

```bash
cd /home/kai3/coco/quantization_graph

# 1. 构建 benchmark binary（若已存在可跳过）
cargo build --release --manifest-path src/graph_core/Cargo.toml

# 2. query codec kernel / ordering 单元测试
bash Ours/tests/run_query_coarse_codec_test.sh /tmp/qcc_test_gist_sweep_repro

# 3. 正向 codec 顺序；复用已有 graph，不重建图
OUT_ROOT=results/query_coarse_bench_gist_1bit_search_repro_forward \\
CODECS=full,b1,int4,int8 \\
bash legacy/ours_experiments/run_gist_query_codec_search_sweep.sh

# 4. 反向 codec 顺序
OUT_ROOT=results/query_coarse_bench_gist_1bit_search_repro_reverse \\
CODECS=int8,int4,b1,full \\
bash legacy/ours_experiments/run_gist_query_codec_search_sweep.sh

# 5. 确定性校验与聚合
legacy/ours_experiments/analyze_gist_query_codec_search_sweep.py \\
  --run forward=results/query_coarse_bench_gist_1bit_search_repro_forward/02_diskann_fair/gist/csv/diskann_fair_raw.csv \\
  --run reverse=results/query_coarse_bench_gist_1bit_search_repro_reverse/02_diskann_fair/gist/csv/diskann_fair_raw.csv \\
  --out-dir results/query_coarse_bench_gist_1bit_search_repro_analysis

# 6. 生成零依赖 SVG 曲线
legacy/ours_experiments/plot_gist_query_codec_search_sweep.py \\
  --aggregate results/query_coarse_bench_gist_1bit_search_repro_analysis/aggregate.csv \\
  --out-dir results/query_coarse_bench_gist_1bit_search_repro_analysis/figures

# 7. 生成 Markdown 报告
legacy/ours_experiments/build_gist_query_codec_search_markdown.py \\
  --analysis-dir results/query_coarse_bench_gist_1bit_search_repro_analysis \\
  --output results/query_coarse_bench_gist_1bit_search_repro_analysis/query_codec_search_report.md
```

本次正式结果使用以下两个原始目录：

- [`v4_numa` forward raw CSV](../query_coarse_bench_gist_1bit_search_v4_numa/02_diskann_fair/gist/csv/diskann_fair_raw.csv)
- [`v5_numa_reverse` raw CSV](../query_coarse_bench_gist_1bit_search_v5_numa_reverse/02_diskann_fair/gist/csv/diskann_fair_raw.csv)

## 复现代码与产物

- Sweep 与 NUMA/CPU 固定：[`run_gist_query_codec_search_sweep.sh`](../../legacy/ours_experiments/run_gist_query_codec_search_sweep.sh)
- 两轮校验和聚合：[`analyze_gist_query_codec_search_sweep.py`](../../legacy/ours_experiments/analyze_gist_query_codec_search_sweep.py)
- SVG 绘图：[`plot_gist_query_codec_search_sweep.py`](../../legacy/ours_experiments/plot_gist_query_codec_search_sweep.py)
- Markdown 生成：[`build_gist_query_codec_search_markdown.py`](../../legacy/ours_experiments/build_gist_query_codec_search_markdown.py)
- 两轮逐点测量：[`run_measurements.csv`](run_measurements.csv)
- 36 点聚合：[`aggregate.csv`](aggregate.csv)
- Recall 门槛表：[`target_recall.csv`](target_recall.csv)
- 确定性校验：[`validation.json`](validation.json)
- 口径和来源说明：[`source_notes.md`](source_notes.md)

## 图表映射

| 报告段落 | 分析问题 | 图形 | 字段 | 支持的结论 |
|:---|:---|:---|:---|:---|
| Recall–QPS | 同等 Recall 下哪个 codec 吞吐更高 | 四系列有序折线 + marker + 两轮范围线 | `recall_at_10`, `qps_mean`, `qps_min`, `qps_max`, `codec`, `ef_search` | INT4/INT8 位于主要 Pareto 前沿，b1 最慢 |
| Recall–cost | 同等 Recall 需要多少后续计算与实测核时间 | 双面板有序折线 | `remaining_4bit_calls`, `coarse_kernel_us_mean`, `recall_at_10`, `codec` | b1 因弱剪枝付出约两倍后续 4-bit 计算 |

两张图都采用显式颜色、marker 和线型三重编码；SVG 为静态、离线、自包含输出，无外部字体或网络依赖。
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
