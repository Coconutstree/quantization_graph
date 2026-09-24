#!/usr/bin/env python3
"""Build the native Data Analytics report artifact for the GIST1M sweep."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, object]]:
    numeric = {
        "ef_search",
        "recall_at_10",
        "qps_mean",
        "qps_min",
        "qps_max",
        "qps_range_pct",
        "latency_mean_us",
        "gate_us_mean",
        "remaining_us_mean",
        "coarse_kernel_us_mean",
        "gate_calls",
        "remaining_4bit_calls",
        "total_distance_stages",
        "prune_rate",
        "qps_vs_full",
        "remaining_calls_vs_full",
        "target_recall",
        "selected_ef",
        "achieved_recall",
    }
    with path.open(newline="", encoding="utf-8") as handle:
        rows: list[dict[str, object]] = []
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    key: float(value) if key in numeric else value
                    for key, value in raw.items()
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.analysis_dir
    aggregate = read_csv(out / "aggregate.csv")
    targets = read_csv(out / "target_recall.csv")
    landmarks = [row for row in aggregate if row["ef_search"] in (100, 200, 500)]
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    aggregate_source = {
        "id": "aggregate_csv",
        "label": "两轮 NUMA 固定搜索的聚合结果",
        "path": "results/query_coarse_bench_gist_1bit_search_analysis/aggregate.csv",
        "query": {
            "engine": "DuckDB",
            "language": "sql",
            "description": "读取经过确定性校验的 36 个 codec × efSearch 聚合点。",
            "executed_at": generated_at,
            "tables_used": [
                "results/query_coarse_bench_gist_1bit_search_analysis/aggregate.csv"
            ],
            "filters": [
                "codec in (full, b1, int4, int8)",
                "ef_search in (20,40,60,80,100,200,300,400,500)",
            ],
            "metric_definitions": [
                "Recall@10 = ground-truth top-10 中被搜索结果 top-10 命中的比例",
                "QPS mean = 两个相反 codec 顺序测量的算术均值",
                "remaining_4bit_calls = 每 query 通过公共 1-bit gate 后继续访问 4-bit compact payload 的平均距离调用数",
                "coarse_kernel_us = paper_batch_us + flush_us",
            ],
            "sql": "SELECT * FROM read_csv_auto('results/query_coarse_bench_gist_1bit_search_analysis/aggregate.csv', header=true) ORDER BY CASE codec WHEN 'full' THEN 1 WHEN 'b1' THEN 2 WHEN 'int4' THEN 3 WHEN 'int8' THEN 4 END, ef_search;",
        },
    }
    target_source = {
        "id": "target_csv",
        "label": "离散 Recall 门槛下的最高 QPS 点",
        "path": "results/query_coarse_bench_gist_1bit_search_analysis/target_recall.csv",
        "query": {
            "engine": "DuckDB",
            "language": "sql",
            "description": "读取每个 Recall 门槛和 codec 下达到门槛的最高 QPS 离散点。",
            "executed_at": generated_at,
            "tables_used": [
                "results/query_coarse_bench_gist_1bit_search_analysis/target_recall.csv"
            ],
            "filters": ["target_recall in (0.95,0.978,0.985)"],
            "metric_definitions": [
                "selected point = achieved_recall >= target_recall 的离散 efSearch 点中 qps_mean 最大者"
            ],
            "sql": "SELECT * FROM read_csv_auto('results/query_coarse_bench_gist_1bit_search_analysis/target_recall.csv', header=true) ORDER BY target_recall, CASE codec WHEN 'full' THEN 1 WHEN 'b1' THEN 2 WHEN 'int4' THEN 3 WHEN 'int8' THEN 4 END;",
        },
    }

    sources = [
        {
            "id": "aggregate_csv",
            "label": "两轮 NUMA 固定搜索的聚合结果",
            "path": "results/query_coarse_bench_gist_1bit_search_analysis/aggregate.csv",
            "query": {
                "engine": "Python standard library",
                "language": "python",
                "description": "按 codec 与 efSearch 聚合两个相反运行顺序；时间取算术均值并保留观测 min/max，确定性搜索指标要求逐点一致。",
                "executed_at": generated_at,
                "tables_used": [
                    "v4_numa/diskann_fair_raw.csv",
                    "v5_numa_reverse/diskann_fair_raw.csv",
                ],
                "filters": [
                    "dataset = gist",
                    "query_coarse_codec in (full, b1, int4, int8)",
                    "search_param_value in (20,40,60,80,100,200,300,400,500)",
                    "threads = 1",
                ],
                "metric_definitions": [
                    "Recall@10 = ground-truth top-10 中被搜索结果 top-10 命中的比例，先按 query 计算再汇总",
                    "QPS = 1,000,000 / latency_mean_us；聚合值是两个相反 codec 顺序测量的算术均值",
                    "remaining_4bit_calls = 每 query 通过 1-bit gate 后继续访问 4-bit compact payload 的平均距离调用数",
                    "coarse_kernel_us = paper_batch_us + flush_us",
                    "prune_rate = paper_would_prune / paper_checked",
                ],
            },
        },
        {
            "id": "target_csv",
            "label": "离散 Recall 门槛下的最高 QPS 点",
            "path": "results/query_coarse_bench_gist_1bit_search_analysis/target_recall.csv",
        },
        {
            "id": "validation_json",
            "label": "跨运行确定性校验",
            "path": "results/query_coarse_bench_gist_1bit_search_analysis/validation.json",
        },
        {
            "id": "forward_raw",
            "label": "正式运行 1：full→b1→int4→int8",
            "path": "results/query_coarse_bench_gist_1bit_search_v4_numa/02_diskann_fair/gist/csv/diskann_fair_raw.csv",
        },
        {
            "id": "reverse_raw",
            "label": "正式运行 2：int8→int4→b1→full",
            "path": "results/query_coarse_bench_gist_1bit_search_v5_numa_reverse/02_diskann_fair/gist/csv/diskann_fair_raw.csv",
        },
        {
            "id": "sweep_script",
            "label": "NUMA/CPU 固定搜索脚本",
            "path": "legacy/ours_experiments/run_gist_query_codec_search_sweep.sh",
        },
        {
            "id": "analysis_script",
            "label": "聚合与校验脚本",
            "path": "legacy/ours_experiments/analyze_gist_query_codec_search_sweep.py",
        },
        {
            "id": "source_notes",
            "label": "实验口径、来源与限制",
            "path": "results/query_coarse_bench_gist_1bit_search_analysis/source_notes.md",
        },
    ]

    manifest = {
        "version": 1,
        "surface": "report",
        "title": "GIST1M 1-bit 数据图搜索：四种 Query Codec 的 Recall–QPS / Cost",
        "description": "复用同一 Vamana 图，在固定 NUMA 与单搜索线程下比较 full、b1、int4、int8 query codec。",
        "generatedAt": generated_at,
        "charts": [
            {
                "id": "recall_qps",
                "title": "Recall@10 与 QPS",
                "subtitle": "中高 recall 区间，INT4/INT8 在相近 recall 下持续高于 full；b1 最慢。",
                "type": "line",
                "dataset": "aggregate",
                "sourceId": "aggregate_csv",
                "source": aggregate_source,
                "encodings": {
                    "x": {
                        "field": "recall_at_10",
                        "type": "quantitative",
                        "label": "Recall@10",
                        "format": "percent",
                    },
                    "y": {
                        "field": "qps_mean",
                        "type": "quantitative",
                        "label": "QPS（两轮均值）",
                        "format": "number",
                    },
                    "color": {
                        "field": "codec",
                        "type": "nominal",
                        "label": "query codec",
                    },
                    "tooltip": [
                        {"field": "ef_search", "type": "quantitative", "label": "efSearch"},
                        {"field": "qps_min", "type": "quantitative", "label": "QPS min"},
                        {"field": "qps_max", "type": "quantitative", "label": "QPS max"},
                        {"field": "remaining_4bit_calls", "type": "quantitative", "label": "剩余 4-bit 调用/query"},
                    ],
                },
                "layout": "full",
            },
            {
                "id": "recall_cost",
                "title": "剩余 4-bit 距离调用与 Recall@10",
                "subtitle": "full、INT4、INT8 的成本曲线几乎重合；b1 为同等 recall 付出约 1.9–2.0 倍后续调用。",
                "type": "line",
                "dataset": "aggregate",
                "sourceId": "aggregate_csv",
                "source": aggregate_source,
                "encodings": {
                    "x": {
                        "field": "remaining_4bit_calls",
                        "type": "quantitative",
                        "label": "剩余 4-bit 距离调用/query",
                        "format": "number",
                    },
                    "y": {
                        "field": "recall_at_10",
                        "type": "quantitative",
                        "label": "Recall@10",
                        "format": "percent",
                    },
                    "color": {
                        "field": "codec",
                        "type": "nominal",
                        "label": "query codec",
                    },
                    "tooltip": [
                        {"field": "ef_search", "type": "quantitative", "label": "efSearch"},
                        {"field": "gate_calls", "type": "quantitative", "label": "1-bit gate 调用/query"},
                        {"field": "coarse_kernel_us_mean", "type": "quantitative", "label": "粗筛核耗时", "unit": "µs"},
                        {"field": "qps_mean", "type": "quantitative", "label": "QPS"},
                    ],
                },
                "layout": "full",
            },
        ],
        "tables": [
            {
                "id": "landmark_table",
                "title": "efSearch=100 / 200 / 500 精确对比",
                "subtitle": "QPS 展示两轮均值与观测范围；搜索结构指标两轮完全一致。",
                "dataset": "landmarks",
                "sourceId": "aggregate_csv",
                "source": aggregate_source,
                "defaultSort": {"field": "ef_search", "direction": "asc"},
                "density": "compact",
                "layout": "full",
                "columns": [
                    {"field": "ef_search", "label": "efSearch", "format": "number"},
                    {"field": "codec", "label": "codec", "type": "text"},
                    {"field": "recall_at_10", "label": "Recall@10", "format": "percent"},
                    {"field": "qps_mean", "label": "QPS mean", "format": "number"},
                    {"field": "qps_min", "label": "QPS min", "format": "number"},
                    {"field": "qps_max", "label": "QPS max", "format": "number"},
                    {"field": "qps_vs_full", "label": "QPS/full (×)", "format": "number"},
                    {"field": "remaining_4bit_calls", "label": "剩余 4-bit 调用", "format": "number"},
                    {"field": "coarse_kernel_us_mean", "label": "粗筛核耗时 (µs)", "format": "number"},
                ],
            },
            {
                "id": "target_table",
                "title": "固定 Recall 门槛的最快实测点",
                "subtitle": "不做插值；选择达到门槛的离散 efSearch 点中 QPS 最高者。",
                "dataset": "targets",
                "sourceId": "target_csv",
                "source": target_source,
                "defaultSort": {"field": "target_recall", "direction": "asc"},
                "density": "compact",
                "layout": "full",
                "columns": [
                    {"field": "target_recall", "label": "Recall 门槛", "format": "percent"},
                    {"field": "codec", "label": "codec", "type": "text"},
                    {"field": "selected_ef", "label": "efSearch", "format": "number"},
                    {"field": "achieved_recall", "label": "实测 Recall@10", "format": "percent"},
                    {"field": "qps_mean", "label": "QPS mean", "format": "number"},
                    {"field": "qps_min", "label": "QPS min", "format": "number"},
                    {"field": "qps_max", "label": "QPS max", "format": "number"},
                    {"field": "remaining_4bit_calls", "label": "剩余 4-bit 调用", "format": "number"},
                ],
            },
        ],
        "sources": sources,
        "blocks": [
            {
                "id": "title",
                "type": "markdown",
                "body": "# GIST1M 1-bit 数据图搜索：四种 Query Codec 的 Recall–QPS / Cost",
            },
            {
                "id": "technical_summary",
                "type": "markdown",
                "body": "## 技术摘要\n\n在这份当前实现上，**INT4/INT8 已经不再比 full 慢**。四种策略复用同一张图、同一组 800 个 query，单线程逐 query 搜索，并用相反 codec 顺序复跑。ef=100 时，INT8 为 819 QPS、INT4 为 813、full 为 766、b1 为 506；对应 Recall@10 均在 0.9505–0.9516。ef=500 时，INT4/INT8 比 full 快约 13.5%–13.9%，b1 则慢约 32.4%。\n\n关键原因不是低 bit 天生更快，而是当前 INT4/INT8 query gate 同时保留了与 full 几乎相同的剪枝能力，并降低了粗筛核耗时；纯 b1 query 的 gate 虽便宜，却让约两倍候选进入后续 4-bit 距离，最终最慢。",
            },
            {
                "id": "semantics",
                "type": "markdown",
                "body": "## 四种策略实际比较的是什么\n\n你的理解是对的：`full` 表示 **数据库点始终使用 1-bit MSB sidecar，query 保持 FP32**。`b1`、`int4`、`int8` 只改变 query 的 gate 表示。当前搜索是两阶段：所有新候选先走 1-bit 数据 gate；未剪掉的候选再访问 4-bit compact payload 完成遍历距离，最终 top-100 用 full query / residual rerank。因此这里不是“1-bit 数据 × 不同 query”从头到尾只算一种点积。",
            },
            {
                "id": "qps_finding",
                "type": "markdown",
                "sourceId": "aggregate_csv",
                "body": "## Recall–QPS：INT4/INT8 构成主 Pareto 前沿\n\n在 ef=100、200、500 三个代表点，INT8 相对 full 的 QPS 分别为 +7.0%、+11.2%、+13.9%；INT4 分别为 +6.2%、+9.8%、+13.5%。两种整数 query 基本同档，低 ef 的细小名次落在运行波动范围内。b1 的 QPS 只有 full 的 66.0%、66.3%、67.6%。",
            },
            {"id": "qps_chart", "type": "chart", "chartId": "recall_qps", "layout": "full"},
            {
                "id": "cost_definition",
                "type": "markdown",
                "body": "## Recall–cost：主图用后续 4-bit 调用数\n\n主 cost 定义为 `paper_remaining_kernel_calls`：每个 query 在公共 1-bit gate 之后，仍需读取 4-bit compact payload 的平均距离调用数。这样能直接衡量 gate 的剪枝质量；它不是 CPU 指令数，所以表中另列 `paper_batch_us + flush_us` 的实际粗筛核耗时。full、INT4、INT8 的 gate 候选数和后续调用数几乎一致，b1 则明显右移。",
            },
            {"id": "cost_chart", "type": "chart", "chartId": "recall_cost", "layout": "full"},
            {
                "id": "cost_finding",
                "type": "markdown",
                "sourceId": "aggregate_csv",
                "body": "## b1 的便宜 gate 被弱剪枝反噬\n\n以 ef=100 为例，四者都检查约 4,783 个 gate 候选；full/INT4/INT8 的剪枝率约 77%，只留下 1,177–1,181 次 4-bit 距离，而 b1 的剪枝率为 54.4%，留下 2,234 次。ef=500 时是 3,557–3,569 次对 7,083 次。结果是 b1 的实际粗筛核耗时在 ef=100 为 1,570µs，显著高于 full 的 941µs及 INT4/INT8 的 783/774µs。",
            },
            {"id": "landmark_block", "type": "table", "tableId": "landmark_table", "layout": "full"},
            {
                "id": "target_interpretation",
                "type": "markdown",
                "body": "## 固定 Recall 门槛要按离散点解释\n\n在 Recall≥0.95 时四种 codec 都选 ef=100，INT8 最快。Recall≥0.978 时，INT4 在 ef=200 的 0.977875 只差 0.000125，却因严格门槛必须跳到 ef=300；这说明离散 sweep 的门槛表适合做保守选型，不应把微小 Recall 差异解读成稳定质量差距。",
            },
            {"id": "target_block", "type": "table", "tableId": "target_table", "layout": "full"},
            {
                "id": "scope",
                "type": "markdown",
                "body": "## 范围、数据与指标\n\n数据为 GIST1M（1,000,000 个 960 维数据点）及现有 test split 的 800 个 query / 100-NN ground truth。图为已建好的 `gist_Ours_R64_Lbuild400.graph.bin`，Vamana `R=64, Lbuild=400`；搜索 `k=10`、rerank candidates=100、`efSearch={20,40,60,80,100,200,300,400,500}`。QPS 取逐 query 平均延迟的倒数；Recall 使用 Recall@10。",
            },
            {
                "id": "methodology",
                "type": "markdown",
                "body": "## 方法\n\n每次运行重新确定性编码 payload，但直接复用同一图，不计图构建时间。payload 在 NUMA node 0 的 20 个物理核上构造并 first-touch；编码完成后同一进程固定到 CPU 20，搜索线程数为 1。正式测量做两轮，codec 顺序分别为 `full,b1,int4,int8` 与反向 `int8,int4,b1,full`。聚合时间取两轮算术均值，并保留 min/max；Recall、访问节点、调用数、剪枝数等 9 个结构字段逐点强制一致。",
            },
            {
                "id": "robustness",
                "type": "markdown",
                "sourceId": "validation_json",
                "body": "## 鲁棒性检查\n\n72 个测量行全部通过确定性一致性检查。最大 QPS 轮次范围出现在 INT8/ef=20：2,428–2,577 QPS，即均值的 6.0%；ef≥100 的代表点范围均明显更小。因只有两轮，这些 min/max 只能反映观测到的顺序/热状态波动，不能当作置信区间。",
            },
            {
                "id": "limitations",
                "type": "markdown",
                "body": "## 限制\n\n这次只验证当前 CPU、当前 direct gate 实现和单线程逐 query 搜索；没有 PMU/带宽/LLC miss 数据，也没有多线程吞吐或尾延迟的充分重复。`remaining_4bit_calls` 衡量算法级昂贵后续距离次数，不等价于硬件 cycle。Recall 的微小差异来自 codec 改变搜索路径；800-query 样本下小于约千分之一的差异不宜单独做强结论。",
            },
            {
                "id": "recommendations",
                "type": "markdown",
                "body": "## 建议\n\n1. 当前实现优先选 **INT8** 作为稳妥默认：Recall 最贴近 full，ef≥100 的 QPS 比 full 高 7%–14%；若更看重 query 存储/传输，再选 INT4，它的搜索速度基本同档。\n2. 不建议把纯 b1 query 当最终 gate：先提升它的剪枝质量，或只把它作为更便宜的第一级并限制进入 4-bit 阶段的候选。\n3. 生产选型应在目标 Recall 上用更细 ef 网格（尤其 180–240、360–420），再做至少 5–10 轮固定 NUMA 复跑并报告 p50/p95。",
            },
            {
                "id": "further_questions",
                "type": "markdown",
                "body": "## 后续问题\n\n- INT8 相对 INT4 的极小 Recall 差异在更多 query / 多轮运行下是否稳定？\n- 多线程时 INT gate 的优势会不会因共享带宽或 LLC 压力缩小？\n- 两级 LUT / bitplane gate 能否在保持 INT8 剪枝率的同时继续减少每候选 gate cycle？",
            },
        ],
    }

    artifact = {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "aggregate": aggregate,
                "landmarks": landmarks,
                "targets": targets,
            },
        },
        "sources": sources,
    }
    (out / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
