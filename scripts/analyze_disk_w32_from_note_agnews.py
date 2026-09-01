#!/usr/bin/env python3
"""AGNews disk-environment 32-worker analysis (complete SymphonyQG sweep)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path("docs/analysis/.mplconfig").resolve()))

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd


DISPLAY_METHOD = {
    "Ours-Disk": "Ours-Disk",
    "OG-LVQ-DiskPort": "OG-LVQ",
    "Glass-NSG-DiskPort": "Glass-NSG",
    "SymphonyQG-DiskPort": "SymphonyQG",
}

METHOD_COLORS = {
    "Ours-Disk": "#C2417A",
    "OG-LVQ": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
    "SymphonyQG": "#0F766E",
}

PANEL_BG = "#FCFCFD"


def configure_matplotlib() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 8,
            "figure.facecolor": PANEL_BG,
            "axes.facecolor": PANEL_BG,
            "savefig.facecolor": PANEL_BG,
            "text.color": "#202124",
            "axes.labelcolor": "#202124",
            "axes.edgecolor": "#7A7F87",
            "xtick.color": "#62666D",
            "ytick.color": "#62666D",
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.linewidth": 0.8,
            "legend.frameon": False,
            "figure.dpi": 150,
            "xtick.major.size": 3,
            "ytick.major.size": 3,
        }
    )


def save_pub(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")


def soften_axis(ax: plt.Axes) -> None:
    ax.tick_params(labelsize=7)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#7A7F87")
    ax.margins(x=0.04)


def read_rows() -> pd.DataFrame:
    base = Path("results/disk_environment/03_system_fair/agnews/csv")
    old = pd.read_csv(base / "formal_test_rows_formal_diskenv_20260826_114755_bc_agnews.csv")
    old = old[old["method"].ne("SymphonyQG-DiskPort")].copy()
    fixed = pd.read_csv(base / "formal_test_rows.csv")
    fixed = fixed[fixed["method"].eq("SymphonyQG-DiskPort")].copy()
    df = pd.concat([old, fixed], ignore_index=True)
    numeric = [
        "workers",
        "search_width",
        "recall",
        "qps",
        "latency_mean_us",
        "latency_p95_us",
        "index_size_mb",
        "resident_bytes",
        "peak_rss_bytes",
        "io_requests_per_query",
        "bytes_read_per_query",
        "io_wait_us",
        "distance_compute_us",
        "query_prep_us",
        "queue_compute_us",
        "rerank_us",
        "db1_checks",
        "db1_survivors",
        "full4_candidates",
        "full4_page_reads",
        "rerank_candidates",
        "rerank_page_reads",
    ]
    for col in numeric:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["method_display"] = df["method"].map(DISPLAY_METHOD).fillna(df["method"])
    return df


def read_build_stats() -> pd.DataFrame:
    rows = []
    root = Path("results/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957")
    specs = [
        ("05b", root / "05B_diskann_shared_graph/agnews/artifacts/export"),
        ("05c", root / "05C_disk_system_fair/agnews/artifacts/export"),
    ]
    for layer, d in specs:
        for path in sorted(d.glob("*.build_stats.json")):
            obj = json.loads(path.read_text())
            method = path.name.split("__")[0]
            rows.append(
                {
                    "layer": layer,
                    "method": method,
                    "method_display": DISPLAY_METHOD.get(method, method),
                    "wall_seconds": obj.get("wall_seconds", math.nan),
                    "peak_rss_gib": obj.get("peak_rss_bytes", math.nan) / (1024**3),
                    "read_mb": obj.get("read_bytes", math.nan) / (1024**2),
                }
            )
    return pd.DataFrame(rows)


def read_shared_graph_meta() -> dict[str, str]:
    path = Path(
        "results/memory_environment/dataset_artifacts/agnews/indexes/02_diskann_fair/"
        "shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.json"
    )
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    return meta


def plot_query_curves(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.65), constrained_layout=True)
    order = ["Ours-Disk", "OG-LVQ", "Glass-NSG", "SymphonyQG"]
    for method in order:
        g = df[df["method_display"].eq(method)].sort_values("recall")
        if g.empty:
            continue
        color = METHOD_COLORS.get(method, "#777777")
        lw = 2.1 if method == "Ours-Disk" else 1.4
        z = 5 if method == "Ours-Disk" else 3
        axes[0].plot(g["recall"], g["qps"], color=color, linewidth=lw, zorder=z)
        axes[0].scatter(g["recall"], g["qps"], s=9 if method == "Ours-Disk" else 7, color=color, zorder=z)
        axes[1].plot(g["recall"], g["io_requests_per_query"], color=color, linewidth=lw, zorder=z)
        axes[1].scatter(g["recall"], g["io_requests_per_query"], s=9 if method == "Ours-Disk" else 7, color=color, zorder=z)
        end = g.iloc[-1]
        axes[0].text(end["recall"] + 0.006, end["qps"], method, color=color, fontsize=7, va="center")
        axes[1].text(end["recall"] + 0.006, end["io_requests_per_query"], method, color=color, fontsize=7, va="center")

    axes[0].set_xlabel("Recall@10")
    axes[0].set_ylabel("QPS")
    axes[0].set_title("AGNews 05C recall / QPS", fontsize=8)
    axes[0].set_xlim(0.78, 1.02)
    axes[0].set_ylim(0, 24)
    axes[0].set_yticks([0, 5, 10, 15, 20])
    soften_axis(axes[0])

    axes[1].set_xlabel("Recall@10")
    axes[1].set_ylabel("I/O requests per query")
    axes[1].set_title("I/O price of recall", fontsize=8)
    axes[1].set_xlim(0.78, 1.02)
    axes[1].set_ylim(0, 7000)
    axes[1].set_yticks([0, 2000, 4000, 6000])
    soften_axis(axes[1])

    save_pub(fig, out / "fig02_w32_recall_qps_io_curves_agnews")
    plt.close(fig)


def plot_ours_breakdown(df: pd.DataFrame, out: Path) -> None:
    ours = df[df["method"].eq("Ours-Disk")].copy()
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), constrained_layout=True)
    g = ours.sort_values("search_width")

    axes[0].plot(g["search_width"], g["latency_mean_us"] / 1000, color="#272727", linewidth=1.4, linestyle="--", label="Total")
    axes[0].plot(g["search_width"], g["io_wait_us"] / 1000, color=METHOD_COLORS["Ours-Disk"], linewidth=2.1, label="I/O wait")
    axes[0].set_xlabel("Search width")
    axes[0].set_ylabel("Latency (ms/query)")
    axes[0].set_title("Latency is I/O wait", fontsize=8)
    axes[0].legend(fontsize=6.5, loc="upper left", handlelength=1.3)
    soften_axis(axes[0])

    non_io = [
        ("queue_compute_us", "Queue", "#7884B4"),
        ("distance_compute_us", "Distance", "#8BCF8B"),
        ("query_prep_us", "Prep", "#767676"),
        ("rerank_us", "Rerank", "#B64342"),
    ]
    for col, label, color in non_io:
        axes[1].plot(g["search_width"], g[col], color=color, linewidth=1.5, label=label)
    axes[1].set_xlabel("Search width")
    axes[1].set_ylabel("Non-I/O time (us/query)")
    axes[1].set_title("Compute terms are small", fontsize=8)
    axes[1].legend(fontsize=6.3, loc="upper left", handlelength=1.2)
    soften_axis(axes[1])

    mechanism = pd.Series(
        {
            "DB1 checks": g["db1_checks"].mean(),
            "Full4 candidates": g["full4_candidates"].mean(),
            "Full4 page reads": g["full4_page_reads"].mean(),
            "Rerank candidates": g["rerank_candidates"].mean(),
        }
    )
    colors = ["#D7A9BF", "#B7658E", METHOD_COLORS["Ours-Disk"], "#B0892E"]
    axes[2].barh(range(len(mechanism)), mechanism.values, color=colors, height=0.62)
    axes[2].set_yticks(range(len(mechanism)), mechanism.index)
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Mean count per query")
    axes[2].set_title("Where reads come from", fontsize=8)
    soften_axis(axes[2])

    save_pub(fig, out / "fig03_ours_query_time_decomposition_w32_agnews")
    plt.close(fig)


def write_report(out: Path, rows: pd.DataFrame, build: pd.DataFrame, graph_meta: dict[str, str]) -> None:
    def fmt(v: float, digits: int = 2) -> str:
        if pd.isna(v):
            return "NA"
        return f"{v:.{digits}f}"

    def num(method: str, col: str, agg: str = "mean") -> float:
        s = pd.to_numeric(rows[rows["method"].eq(method)][col], errors="coerce").dropna()
        if s.empty:
            return math.nan
        return s.mean() if agg == "mean" else (s.max() if agg == "max" else s.min())

    def rng(method: str) -> str:
        return f"{fmt(num(method, 'recall', 'min'), 4)} - {fmt(num(method, 'recall', 'max'), 4)}"

    def share(method: str, num_col: str, den_col: str) -> float:
        n = pd.to_numeric(rows[rows["method"].eq(method)][num_col], errors="coerce")
        d = pd.to_numeric(rows[rows["method"].eq(method)][den_col], errors="coerce")
        mask = d.notna() & d.ne(0)
        return (n[mask] / d[mask]).mean() * 100 if mask.any() else math.nan

    def cell(method: str, col: str, digits: int = 2, scale: float = 1.0, agg: str = "mean") -> str:
        return fmt(num(method, col, agg) / scale, digits)

    def bcell(method: str, col: str, scale: float = 1.0) -> str:
        sub = build[build["method"].eq(method)]
        if sub.empty:
            return "—"
        return fmt(sub[col].max() / scale, 2)

    def img(feishu: bool, name: str, caption: str) -> str:
        src = f"@./docs/analysis/{name}" if feishu else name
        return f"![{caption}]({src})"

    def make_report(feishu: bool) -> str:
        local_note = (
            "本文件使用普通 Markdown 图片链接，适合 IDE/GitHub 预览；飞书导入版见 "
            "`docs/analysis/disk_w32_agnews_analysis_report_feishu.md`。"
            if not feishu
            else ""
        )
        graph_build_min = (
            float(graph_meta["graph_build_time_ms"]) / 60000
            if graph_meta.get("graph_build_time_ms")
            else math.nan
        )
        graph_build_dist = graph_meta.get("graph_build_distance_evaluations", "—")
        graph_mib = (
            int(graph_meta["graph_bytes"]) / (1024**2)
            if graph_meta.get("graph_bytes")
            else math.nan
        )
        return f"""# AGNews 磁盘环境 32 线程实验分析

{local_note}

数据来源：`results/disk_environment/03_system_fair/agnews/csv/` 的 05C formal 数据。查询对比使用 05C system 层的四个方法：Ours-Disk、OG-LVQ、Glass-NSG、SymphonyQG。其中 SymphonyQG 使用 `fix_w32_diskpayload_symphony_20260831_140957` 修正后的完整 sweep，其余三个方法使用 `formal_diskenv_20260826_114755_bc_agnews` 的 32 线程 sweep。

## 构建

02 shared graph（DiskANN fp32 Vamana，本机重跑）的构建信息记录在 graph meta：总构建时间约 {fmt(graph_build_min)} min，distance evaluations 约 {graph_build_dist}，graph bytes 约 {fmt(graph_mib)} MiB。没有保留 progress-stage 分段日志（GIST 的 02build 日志同样是最终汇总格式，不含分段），所以不能拆 train/encode/graph build 三段。

05 层导出阶段的 `build_stats` 只同步到本机一部分；Ours-Disk / OG-LVQ / Glass-NSG 的 export build_stats 仍在另一台机器的 run 里，本机只有汇总 CSV。

| 方法 | 磁盘索引构建/导出 (min) | 构建 peak RSS (GiB) |
|---|---:|---:|
| PQ-DiskANN | {bcell("PQ-DiskANN-Disk", "wall_seconds", 60)} | {bcell("PQ-DiskANN-Disk", "peak_rss_gib")} |
| SQ-DiskANN | {bcell("SQ-DiskANN-Disk", "wall_seconds", 60)} | {bcell("SQ-DiskANN-Disk", "peak_rss_gib")} |
| SAQ-DiskANN | {bcell("SAQ-DiskANN-Disk", "wall_seconds", 60)} | {bcell("SAQ-DiskANN-Disk", "peak_rss_gib")} |
| SymphonyQG | {bcell("SymphonyQG-DiskPort", "wall_seconds", 60)} | {bcell("SymphonyQG-DiskPort", "peak_rss_gib")} |

说明：SymphonyQG 导出耗时约 {bcell("SymphonyQG-DiskPort", "wall_seconds", 60)} min、peak RSS 约 {bcell("SymphonyQG-DiskPort", "peak_rss_gib")} GiB，是已有数据里最重的；Ours-Disk 的 AGNews 导出耗时本次缺失，无法与它直接比较。

## 查询

### Ours 查询参数与口径

| 参数 | 值 |
|---|---|
| dataset / base_count / dimension | agnews / 769,382 / 1024 |
| workers | 32 |
| search_dram_budget_gib | 2.0 |
| storage_mode / cache_mode | hybrid_disk / standard |
| kernel | ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search |
| direct_io / native_aio | True / True |
| page_size | 4096 |
| search_width sweep | 10 到 580（40 个点），beam_width=1 |
| ablation | db1+coalescing+reuse |
| query_count | 800 |
| index_size_mb | {fmt(num("Ours-Disk", "index_size_mb", "max"), 2)} |
| resident / peak RSS | {fmt(num("Ours-Disk", "resident_bytes", "max") / 1024**3, 2)} GiB / {fmt(num("Ours-Disk", "peak_rss_bytes", "max") / 1024**3, 2)} GiB |

### 05C 方法指标对比

| 指标 | Ours-Disk | OG-LVQ | Glass-NSG | SymphonyQG |
|---|---:|---:|---:|---:|
| Recall@10 范围 | {rng("Ours-Disk")} | {rng("OG-LVQ-DiskPort")} | {rng("Glass-NSG-DiskPort")} | {rng("SymphonyQG-DiskPort")} |
| 最高 QPS | {cell("Ours-Disk", "qps", 2, 1.0, "max")} | {cell("OG-LVQ-DiskPort", "qps", 2, 1.0, "max")} | {cell("Glass-NSG-DiskPort", "qps", 2, 1.0, "max")} | {cell("SymphonyQG-DiskPort", "qps", 2, 1.0, "max")} |
| 平均 latency | {cell("Ours-Disk", "latency_mean_us", 2, 1000)} ms/query | {cell("OG-LVQ-DiskPort", "latency_mean_us", 2, 1000)} ms/query | {cell("Glass-NSG-DiskPort", "latency_mean_us", 2, 1000)} ms/query | {cell("SymphonyQG-DiskPort", "latency_mean_us", 2, 1000)} ms/query |
| I/O wait 占比 | {fmt(share("Ours-Disk", "io_wait_us", "latency_mean_us"))}% | {fmt(share("OG-LVQ-DiskPort", "io_wait_us", "latency_mean_us"))}% | {fmt(share("Glass-NSG-DiskPort", "io_wait_us", "latency_mean_us"))}% | {fmt(share("SymphonyQG-DiskPort", "io_wait_us", "latency_mean_us"))}% |
| distance compute 占比 | {fmt(share("Ours-Disk", "distance_compute_us", "latency_mean_us"))}% | — | — | — |
| I/O requests/query | {cell("Ours-Disk", "io_requests_per_query", 1)} | {cell("OG-LVQ-DiskPort", "io_requests_per_query", 1)} | {cell("Glass-NSG-DiskPort", "io_requests_per_query", 1)} | {cell("SymphonyQG-DiskPort", "io_requests_per_query", 1)} |
| bytes read/query | {cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB | {cell("OG-LVQ-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB | {cell("Glass-NSG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB | {cell("SymphonyQG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB |
| DB1 checks/query | {cell("Ours-Disk", "db1_checks", 1)} | — | — | — |
| full4 page reads/query | {cell("Ours-Disk", "full4_page_reads", 1)} | — | — | — |

注意：OG-LVQ、Glass-NSG、SymphonyQG 没有暴露 DB1 / full4 内部计数，其 distance/queue 字段基本等于总耗时，无法与 Ours 的 distance compute 单独拆分口径对比，所以这些格标为“—”。

### Figure 2：Recall/QPS/I-O 曲线

{img(feishu, "fig02_w32_recall_qps_io_curves_agnews.png", "Figure 2. AGNews 05C Recall/QPS/I-O 曲线")}

图读法：只看 05C system 层。左图是 Recall@10 与 QPS；右图是达到相同 recall 时的 I/O requests/query。

### Figure 3：Ours-Disk 查询时间拆分

{img(feishu, "fig03_ours_query_time_decomposition_w32_agnews.png", "Figure 3. AGNews Ours-Disk 查询时间拆分")}

图读法：只看 Ours-Disk。左图是 total latency 与 I/O wait；中图是非 I/O 的 prep/queue/distance/rerank；右图是每 query 的 DB1/full4/rerank 平均计数。

### 查询差距与不同（重点）

- **Recall 上限**：Ours-Disk 最高，约 {fmt(num("Ours-Disk", "recall", "max"), 4)}；SymphonyQG 约 {fmt(num("SymphonyQG-DiskPort", "recall", "max"), 4)}；OG-LVQ 约 {fmt(num("OG-LVQ-DiskPort", "recall", "max"), 4)}；Glass-NSG 约 {fmt(num("Glass-NSG-DiskPort", "recall", "max"), 4)}。
- **Latency / QPS**：Ours-Disk 平均 latency 约 {cell("Ours-Disk", "latency_mean_us", 2, 1000)} ms/query、最高 QPS 约 {cell("Ours-Disk", "qps", 2, 1.0, "max")}，在四个方法里吞吐最高且延迟最低；SymphonyQG 平均 latency 约 {cell("SymphonyQG-DiskPort", "latency_mean_us", 2, 1000)} ms/query。
- **I/O 成本**：同 recall 下 Ours-Disk 的 I/O requests/query 和 bytes read/query 明显低于其他三个方法。平均来看 Ours-Disk 约 {cell("Ours-Disk", "io_requests_per_query", 1)} 次/query、{cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB；Glass-NSG 约 {cell("Glass-NSG-DiskPort", "io_requests_per_query", 1)} 次/query、{cell("Glass-NSG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB；OG-LVQ 约 {cell("OG-LVQ-DiskPort", "io_requests_per_query", 1)} 次/query、{cell("OG-LVQ-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB；SymphonyQG 约 {cell("SymphonyQG-DiskPort", "io_requests_per_query", 1)} 次/query、{cell("SymphonyQG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB。
- **瓶颈**：四个方法都以 I/O wait 为主（Ours-Disk {fmt(share("Ours-Disk", "io_wait_us", "latency_mean_us"))}%、OG-LVQ {fmt(share("OG-LVQ-DiskPort", "io_wait_us", "latency_mean_us"))}%、Glass-NSG {fmt(share("Glass-NSG-DiskPort", "io_wait_us", "latency_mean_us"))}%、SymphonyQG {fmt(share("SymphonyQG-DiskPort", "io_wait_us", "latency_mean_us"))}%）。Ours-Disk 的 distance compute 只有 {fmt(share("Ours-Disk", "distance_compute_us", "latency_mean_us"))}%，说明 Ours 不是距离核慢，而是随机读盘等待慢。

## 数据与口径

- 查询内存限制：`search_dram_budget_gib=2.0`；05C 均为 workers=32、repeat=0。
- Ours-Disk 为磁盘模式：`direct_io=True`、`native_aio=True`、`page_size=4096`。

## Source Data

- `results/disk_environment/03_system_fair/agnews/csv/formal_test_rows_formal_diskenv_20260826_114755_bc_agnews.csv`
- `results/disk_environment/03_system_fair/agnews/csv/formal_test_rows.csv`
- `results/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/`
"""

    (out / "disk_w32_agnews_analysis_report.md").write_text(make_report(feishu=False))
    (out / "disk_w32_agnews_analysis_report_feishu.md").write_text(make_report(feishu=True))


def main() -> int:
    configure_matplotlib()
    out = Path("docs/analysis")
    out.mkdir(parents=True, exist_ok=True)
    rows = read_rows()
    build = read_build_stats()
    graph_meta = read_shared_graph_meta()
    rows.to_csv(out / "source_query_rows_w32_agnews.csv", index=False)
    build.to_csv(out / "source_build_export_stats_w32_agnews.csv", index=False)
    plot_query_curves(rows, out)
    plot_ours_breakdown(rows, out)
    write_report(out, rows, build, graph_meta)
    print("wrote AGNews analysis to docs/analysis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
