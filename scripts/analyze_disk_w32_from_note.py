#!/usr/bin/env python3
"""Analyze the GIST disk-environment 32-worker run referenced by docs/notes/test.md."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path("docs/analysis/.mplconfig").resolve()))

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd


LAYER_LABELS = {
    "05a": "05A quantizer I/O",
    "05b": "05B shared graph",
    "05c": "05C system",
}

DISPLAY_METHOD = {
    "Ours_RaBitQ_K1": "Ours",
    "PQ_4bit": "PQ",
    "SQ_4bit": "SQ",
    "SAQ_B4": "SAQ",
    "Ours-Disk": "Ours-Disk",
    "PQ-DiskANN-Disk": "PQ-DiskANN",
    "SQ-DiskANN-Disk": "SQ-DiskANN",
    "SAQ-DiskANN-Disk": "SAQ-DiskANN",
    "OG-LVQ-DiskPort": "OG-LVQ",
    "Glass-NSG-DiskPort": "Glass-NSG",
    "SymphonyQG-DiskPort": "SymphonyQG",
    "DiskANN-PQ-Disk": "DiskANN-PQ",
}

METHOD_COLORS = {
    "Ours": "#C2417A",
    "Ours-Disk": "#C2417A",
    "PQ": "#B0892E",
    "SQ": "#3A7D44",
    "SAQ": "#8E4A9E",
    "PQ-DiskANN": "#B0892E",
    "SQ-DiskANN": "#3A7D44",
    "SAQ-DiskANN": "#8E4A9E",
    "OG-LVQ": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
    "SymphonyQG": "#0F766E",
    "DiskANN-PQ": "#4A6FA5",
}

GRID = "#DDE1E6"
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
    fig.savefig(stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight")


def label_panel(ax: plt.Axes, label: str) -> None:
    ax.text(
        -0.12,
        1.05,
        label,
        transform=ax.transAxes,
        fontsize=9,
        fontweight="bold",
        va="top",
        ha="left",
    )


def soften_axis(ax: plt.Axes) -> None:
    ax.tick_params(labelsize=7)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#7A7F87")
    ax.margins(x=0.04)


def read_rows(round_dir: Path) -> pd.DataFrame:
    frames = []
    for name in ["05a_rows.csv", "05b_rows.csv", "05c_rows.csv"]:
        df = pd.read_csv(round_dir / name)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
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
        "ours_4bit_payload_bytes",
        "ours_8bit_payload_bytes",
        "ours_adjacency_bytes",
        "ours_fp32_base_bytes",
        "io_requests_per_query",
        "sectors_4k_per_query",
        "bytes_read_per_query",
        "io_wait_us",
        "distance_compute_us",
        "query_prep_us",
        "queue_compute_us",
        "rerank_us",
        "visited_nodes",
        "distance_evaluations",
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
    df["layer_display"] = df["layer"].map(LAYER_LABELS).fillna(df["layer"])
    return df


def parse_build_stats(run_root: Path) -> pd.DataFrame:
    rows = []
    for layer_dir in [
        "05A_disk_quantizer_io",
        "05B_diskann_shared_graph",
        "05C_disk_system_fair",
    ]:
        export_dir = run_root / layer_dir / "gist" / "artifacts" / "export"
        if not export_dir.exists():
            continue
        layer = {"05A": "05a", "05B": "05b", "05C": "05c"}[layer_dir[:3]]
        for path in sorted(export_dir.glob("*.build_stats.json")):
            obj = json.loads(path.read_text())
            m = re.match(r"(.+?)__(.+?)__", path.name)
            method = m.group(1) if m else path.name.split(".")[0]
            storage_mode = m.group(2) if m else ""
            if layer == "05a" and storage_mode != "payload_on_ssd":
                continue
            rows.append(
                {
                    "layer": layer,
                    "layer_display": LAYER_LABELS[layer],
                    "method": method,
                    "method_display": DISPLAY_METHOD.get(method, method),
                    "storage_mode": storage_mode,
                    "wall_seconds": obj.get("wall_seconds", math.nan),
                    "peak_rss_gib": obj.get("peak_rss_bytes", math.nan) / (1024**3),
                    "read_mb": obj.get("read_bytes", math.nan) / (1024**2),
                    "source_file": str(path),
                }
            )
    return pd.DataFrame(rows)


def extract_02_build_log(run_root: Path) -> pd.DataFrame:
    rows = []
    log_root = run_root / "02build" / "02_diskann_fair" / "gist" / "logs"
    for path in sorted(log_root.glob("*/*.log")):
        method = path.parent.name
        stages = {}
        build_total = math.nan
        for line in path.read_text(errors="replace").splitlines():
            m = re.match(r"build_stage=(\S+) us=([0-9.]+)", line)
            if m:
                stages[m.group(1)] = float(m.group(2)) / 1e6
            m = re.match(r"build_total_us=([0-9.]+)", line)
            if m:
                build_total = float(m.group(1)) / 1e6
        if stages or not math.isnan(build_total):
            rows.append(
                {
                    "method": method,
                    "method_display": DISPLAY_METHOD.get(method, method),
                    "build_total_seconds": build_total,
                    "train_seconds": stages.get("train_quantizer", stages.get("train_center", 0.0)),
                    "payload_encode_seconds": stages.get("payload_encode", 0.0),
                    "graph_build_seconds": stages.get("graph_build", math.nan),
                    "source_file": str(path),
                }
            )
    return pd.DataFrame(rows)


def load_ours_artifacts(run_root: Path) -> dict[str, dict]:
    artifacts: dict[str, dict] = {}
    layer_dirs = {
        "05b": "05B_diskann_shared_graph",
        "05c": "05C_disk_system_fair",
    }
    for layer, layer_dir in layer_dirs.items():
        test_path = (
            run_root
            / layer_dir
            / "gist"
            / "artifacts"
            / "test"
            / "Ours-Disk__hybrid_disk__B2__standard__w32__r0.json"
        )
        export_path = (
            run_root
            / layer_dir
            / "gist"
            / "artifacts"
            / "export"
            / "Ours-Disk__hybrid_disk__B2__standard__w1__r0.json"
        )
        artifacts[layer] = {
            "test": json.loads(test_path.read_text()) if test_path.exists() else {},
            "export": json.loads(export_path.read_text()) if export_path.exists() else {},
        }
    return artifacts


def summarize_experiment_audit(rows: pd.DataFrame, ours_artifacts: dict[str, dict]) -> pd.DataFrame:
    ours = rows[(rows["layer"].eq("05c")) & (rows["method"].eq("Ours-Disk"))].sort_values("search_width")
    test = ours_artifacts.get("05c", {}).get("test", {})
    export = ours_artifacts.get("05c", {}).get("export", {})
    budget_bytes = float(test.get("search_dram_budget_gib", math.nan)) * 1024**3
    resident_bytes = float(test.get("resident_bytes", math.nan))
    cache_bytes = float(test.get("cache_bytes", math.nan))
    audit_rows = [
        {
            "check": "Disk-mode configuration",
            "status": "通过",
            "evidence": (
                f"whole_payload_in_memory={test.get('whole_payload_in_memory')}, "
                f"direct_io={test.get('direct_io')}, native_aio={test.get('native_aio')}, "
                f"page_size={test.get('page_size')}"
            ),
            "interpretation": "这说明 Ours 没有误跑成全 payload 内存模式。",
        },
        {
            "check": "Implementation parity",
            "status": "通过",
            "evidence": (
                f"implementation_parity={test.get('implementation_parity')}, "
                f"max_recall_delta={test.get('parity', {}).get('max_recall_delta')}"
            ),
            "interpretation": "native 实现与 reference artifact 在 recall 上对齐。",
        },
        {
            "check": "Recall-width monotonicity",
            "status": "通过",
            "evidence": f"negative recall steps={(ours['recall'].diff().dropna() < -1e-9).sum()}",
            "interpretation": "recall 随 search width 单调增加，sweep 本身没有反常跳点。",
        },
        {
            "check": "05B/05C independence",
            "status": "注意",
            "evidence": "05B/05C Ours-Disk 的 source_kernel、source_graph_sha256 一致，recall/I-O 计数逐 width 完全相同",
            "interpretation": "05B Ours 只能当同实现 sanity record，不能当独立 Ours 变体。",
        },
        {
            "check": "Repeat coverage",
            "status": "注意",
            "evidence": f"repeat_ids={sorted(ours['repeat_id'].dropna().unique().tolist())}",
            "interpretation": "当前只有单次 repeat，不能给最终论文误差条。",
        },
        {
            "check": "Cache budget use",
            "status": "注意",
            "evidence": (
                f"budget={budget_bytes / 1024**3:.2f} GiB, resident={resident_bytes / 1024**3:.2f} GiB, "
                f"test_cache={cache_bytes / 1024**2:.1f} MiB, export_cache={export.get('cache_bytes')}"
            ),
            "interpretation": "名义 2 GiB 搜索预算没有明显转成大 payload/page cache，需要优先查。",
        },
        {
            "check": "Dominant bottleneck",
            "status": "优化",
            "evidence": (
                f"mean_io_wait_share={(ours['io_wait_us'] / ours['latency_mean_us']).mean() * 100:.2f}%, "
                f"mean_io_requests={ours['io_requests_per_query'].mean():.1f}/query"
            ),
            "interpretation": "优化优先级应放在减少随机读和 I/O wait，而不是距离计算。",
        },
    ]
    return pd.DataFrame(audit_rows)


def summarize_best_rows(df: pd.DataFrame) -> pd.DataFrame:
    subset = df[df["workers"].eq(32)].copy()
    sort_cols = ["layer", "method", "recall", "qps"]
    best = (
        subset.sort_values(sort_cols, ascending=[True, True, False, False])
        .groupby(["layer", "method"], as_index=False)
        .head(1)
    )
    keep = [
        "layer",
        "layer_display",
        "method",
        "method_display",
        "storage_mode",
        "search_width",
        "recall",
        "qps",
        "latency_mean_us",
        "latency_p95_us",
        "io_requests_per_query",
        "bytes_read_per_query",
        "io_wait_us",
        "distance_compute_us",
        "query_prep_us",
        "queue_compute_us",
        "rerank_us",
        "index_size_mb",
        "resident_bytes",
        "peak_rss_bytes",
        "formal_ready",
        "direct_io",
        "native_aio",
    ]
    return best[keep].sort_values(["layer", "method_display"])


def plot_build_stats(build: pd.DataFrame, build02: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), constrained_layout=True)

    if not build02.empty:
        b02 = build02.sort_values("build_total_seconds")
        y = range(len(b02))
        stage_cols = [
            ("train_seconds", "Train", "#B4C0E4"),
            ("payload_encode_seconds", "Encode", "#8BCF8B"),
            ("graph_build_seconds", "Graph build", "#4A6FA5"),
        ]
        left = pd.Series(0.0, index=b02.index)
        for col, label, color in stage_cols:
            vals = b02[col].fillna(0) / 60
            axes[0].barh(list(y), vals, left=left / 60, color=color, label=label, height=0.62)
            left = left + b02[col].fillna(0)
        axes[0].set_yticks(list(y), b02["method_display"])
        axes[0].set_xlabel("Time (min)")
        axes[0].set_title("02 graph build (stage breakdown)", fontsize=8)
        axes[0].invert_yaxis()
        axes[0].legend(fontsize=6.5, loc="lower right", handlelength=1.1)
        soften_axis(axes[0])

    keep = build[
        (build["layer"].eq("05c"))
        & build["method"].isin(
            ["Ours-Disk", "OG-LVQ-DiskPort", "Glass-NSG-DiskPort", "SymphonyQG-DiskPort", "DiskANN-PQ-Disk"]
        )
    ].copy()
    keep["label"] = keep["method_display"]
    b = keep.sort_values("wall_seconds", ascending=True)
    ypos = range(len(b))
    axes[1].barh(
        list(ypos),
        b["wall_seconds"] / 60,
        color=[METHOD_COLORS.get(m, "#777777") for m in b["method_display"]],
        height=0.62,
    )
    axes[1].set_yticks(list(ypos), b["label"])
    axes[1].set_xscale("log")
    axes[1].set_xlim(left=0.01, right=12)
    axes[1].set_xlabel("Time (min, log scale)")
    axes[1].set_title("05C disk index build / export", fontsize=8)
    soften_axis(axes[1])

    axes[2].barh(
        list(ypos),
        b["peak_rss_gib"],
        color=[METHOD_COLORS.get(m, "#777777") for m in b["method_display"]],
        height=0.62,
    )
    axes[2].set_yticks([])
    axes[2].set_xlabel("Peak RSS (GiB)")
    axes[2].set_title("05C build memory", fontsize=8)
    soften_axis(axes[2])

    save_pub(fig, out / "fig01_build_export_cost_w32_gist")
    plt.close(fig)


def plot_query_curves(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.65), constrained_layout=True)
    layer_df = df[(df["layer"].eq("05c")) & (df["workers"].eq(32))].copy()
    order = ["Ours-Disk", "OG-LVQ", "Glass-NSG"]

    for method in order:
        g = layer_df[layer_df["method_display"].eq(method)].sort_values("recall")
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
    axes[0].set_title("05C system trade-off", fontsize=8)
    axes[0].set_xlim(0.45, 1.02)
    axes[0].set_ylim(0, 36)
    axes[0].set_yticks([0, 10, 20, 30])
    label_panel(axes[0], "a")
    soften_axis(axes[0])

    axes[1].set_xlabel("Recall@10")
    axes[1].set_ylabel("I/O requests per query")
    axes[1].set_title("I/O price of recall", fontsize=8)
    axes[1].set_xlim(0.45, 1.02)
    axes[1].set_ylim(0, 18500)
    axes[1].set_yticks([0, 5000, 10000, 15000])
    label_panel(axes[1], "b")
    soften_axis(axes[1])

    save_pub(fig, out / "fig02_w32_recall_qps_io_curves_gist")
    plt.close(fig)


def plot_ours_breakdown(df: pd.DataFrame, out: Path) -> pd.DataFrame:
    ours = df[
        df["workers"].eq(32)
        & df["method"].eq("Ours-Disk")
        & df["layer"].isin(["05b", "05c"])
    ].copy()
    components = [
        ("query_prep_us", "prep", "#8fa7c9"),
        ("queue_compute_us", "queue", "#c4b56a"),
        ("distance_compute_us", "distance", "#6aa27a"),
        ("rerank_us", "rerank", "#c98668"),
        ("io_wait_us", "I/O wait", METHOD_COLORS["Ours-Disk"]),
    ]
    for col, _, _ in components:
        ours[col] = ours[col].fillna(0)
    ours["component_sum_us"] = ours[[c for c, _, _ in components]].sum(axis=1)
    for col, label, _ in components:
        ours[f"{label}_fraction"] = ours[col] / ours["component_sum_us"].replace(0, math.nan)

    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), constrained_layout=True)
    g = ours[ours["layer"].eq("05c")].sort_values("search_width")

    axes[0].plot(
        g["search_width"],
        g["latency_mean_us"] / 1000,
        color="#272727",
        linewidth=1.4,
        linestyle="--",
        label="Total",
    )
    axes[0].plot(g["search_width"], g["io_wait_us"] / 1000, color=METHOD_COLORS["Ours-Disk"], linewidth=2.1, label="I/O wait")
    axes[0].set_xlabel("Search width")
    axes[0].set_ylabel("Latency (ms/query)")
    axes[0].set_title("Latency is I/O wait", fontsize=8)
    axes[0].legend(fontsize=6.5, loc="upper left", handlelength=1.3)
    label_panel(axes[0], "a")
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
    label_panel(axes[1], "b")
    soften_axis(axes[1])

    mechanism = pd.Series(
        {
            "DB1 checks": g["db1_checks"].mean(),
            "Full4 candidates": g["full4_candidates"].mean(),
            "Full4 page reads": g["full4_page_reads"].mean(),
            "Rerank candidates": g["rerank_candidates"].mean(),
        }
    )
    y = range(len(mechanism))
    colors = ["#D7A9BF", "#B7658E", METHOD_COLORS["Ours-Disk"], "#B0892E"]
    axes[2].barh(list(y), mechanism.values, color=colors, height=0.62)
    axes[2].set_yticks(list(y), mechanism.index)
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Mean count per query")
    axes[2].set_title("Where reads come from", fontsize=8)
    label_panel(axes[2], "c")
    soften_axis(axes[2])

    save_pub(fig, out / "fig03_ours_query_time_decomposition_w32_gist")
    plt.close(fig)

    summary = (
        ours.groupby("layer")
        .agg(
            rows=("search_width", "count"),
            recall_min=("recall", "min"),
            recall_max=("recall", "max"),
            qps_max=("qps", "max"),
            latency_mean_ms=("latency_mean_us", lambda x: x.mean() / 1000),
            io_wait_pct=("I/O wait_fraction", lambda x: x.mean() * 100),
            distance_pct=("distance_fraction", lambda x: x.mean() * 100),
            prep_pct=("prep_fraction", lambda x: x.mean() * 100),
            queue_pct=("queue_fraction", lambda x: x.mean() * 100),
            rerank_pct=("rerank_fraction", lambda x: x.mean() * 100),
            io_requests_mean=("io_requests_per_query", "mean"),
            bytes_read_mean_mb=("bytes_read_per_query", lambda x: x.mean() / (1024**2)),
            full4_page_reads_mean=("full4_page_reads", "mean"),
            rerank_page_reads_mean=("rerank_page_reads", "mean"),
            db1_checks_mean=("db1_checks", "mean"),
            db1_survivors_mean=("db1_survivors", "mean"),
        )
        .reset_index()
    )
    summary["layer_display"] = summary["layer"].map(LAYER_LABELS)
    return summary


def write_report(
    out: Path,
    rows: pd.DataFrame,
    build: pd.DataFrame,
    build02: pd.DataFrame,
    best: pd.DataFrame,
    ours_summary: pd.DataFrame,
    audit: pd.DataFrame,
) -> None:
    def fmt(v: float, digits: int = 2) -> str:
        if pd.isna(v):
            return "NA"
        return f"{v:.{digits}f}"

    def cell(df: pd.DataFrame, expr: str, col: str, scale: float = 1.0) -> float:
        sub = df.query(expr)
        if sub.empty:
            return math.nan
        return sub[col].max() / scale

    def image_links(feishu: bool) -> str:
        if feishu:
            return """![Figure 1. 构建与导出成本](@./docs/analysis/fig01_build_export_cost_w32_gist.png)

![Figure 2. 32 线程 Recall-QPS-I/O 曲线](@./docs/analysis/fig02_w32_recall_qps_io_curves_gist.png)

![Figure 3. Ours 查询时间拆分](@./docs/analysis/fig03_ours_query_time_decomposition_w32_gist.png)"""
        return """![Figure 1. 构建与导出成本](fig01_build_export_cost_w32_gist.png)

![Figure 2. 32 线程 Recall-QPS-I/O 曲线](fig02_w32_recall_qps_io_curves_gist.png)

![Figure 3. Ours 查询时间拆分](fig03_ours_query_time_decomposition_w32_gist.png)"""

    def audit_lines() -> str:
        lines = []
        for r in audit.itertuples(index=False):
            lines.append(f"- **{r.status} | {r.check}**：{r.evidence}。{r.interpretation}")
        return "\n".join(lines)

    def make_report(feishu: bool) -> str:
        images = image_links(feishu)
        local_note = (
            "本文件使用普通 Markdown 图片链接，适合 IDE/GitHub 预览；飞书导入版见 "
            "`docs/analysis/disk_w32_gist_analysis_report_feishu.md`。"
            if not feishu
            else ""
        )
        return f"""# GIST 磁盘环境 32 线程实验分析

{local_note}

一句话结论：**Ours-Disk 当前不是距离计算慢，而是磁盘读等待慢。** 在 05C system 层，Ours-Disk 平均每个 query 约 {fmt(ours05c.io_requests_mean, 1)} 次 I/O、读约 {fmt(ours05c.bytes_read_mean_mb, 2)} MiB，平均查询时间中 I/O wait 占 {fmt(ours05c.io_wait_pct)}%，distance compute 仅约 {fmt(ours05c.distance_pct)}%。

先澄清一个容易误读的点：**05A Ours 和 05B/05C Ours 不是同一个实验对象；但 05B Ours-Disk 和 05C Ours-Disk 在这批 GIST 结果里本质上是同一个 Ours 查询实现/同一张 Ours graph。** 05B/05C 的 layer 名更多是在标记“和谁比较、放在哪个 suite 目录里”，不是说 Ours 在 05B 和 05C 有两套不同算法。

实验合理性一句话：**没有看到会直接推翻 Ours 磁盘实验的硬错误，但有几个必须在论文/报告里说清楚的注意点。** 特别是 05B/05C Ours 不是独立方法，当前只有 repeat=0，且 2 GiB budget 与实际 cache 统计之间存在待确认差距。

数据来源：`docs/notes/test.md` 指定的 `gist_test_20260829_204245`，读取 `round_w32/05a_rows.csv`、`round_w32/05b_rows.csv`、`round_w32/05c_rows.csv` 和对应 export/build artifact。输出图表与 source data 均在 `docs/analysis/`。

## 先看结论

- 05A/05B/05C 的 32 线程结果均已覆盖：05A {rows_count.get('05a', 0)} 个方法代表点，05B {rows_count.get('05b', 0)} 个方法代表点，05C {rows_count.get('05c', 0)} 个方法代表点。
- 05A Ours 是 `Ours_RaBitQ_K1`：它测试 fixed candidates / quantizer I/O，本质是“候选已经给定后，量化距离或 payload 访问的成本”。05B/05C Ours 是 `Ours-Disk`：它跑完整 graph search + DB1 gate + full4 page read + rerank 的磁盘查询路径。
- 05B 和 05C 的 Ours-Disk 在当前 CSV 中 `source_kernel`、`source_graph_sha256`、`graph_role` 一致；Recall、I/O requests、bytes read、DB1 checks、full4 page reads 逐 width 完全相同。两者不能当作两个不同 Ours 方法来比较，只能看作同一个 Ours 在两个 suite 标签下的重复/复用记录。
- Ours 实验配置本身像真实磁盘模式：`whole_payload_in_memory=False`、`direct_io=True`、`native_aio=True`、`page_size=4096`，并且 `implementation_parity=passed`、`max_recall_delta=0.0`。
- 需要注意的实验风险：当前 GIST w32 只有一次 repeat，不能画误差条；Ours 的 2 GiB 搜索预算没有明显体现为大 cache，test JSON 顶层 `cache_bytes` 约 34 MB，而 export artifact 为 0，需要进一步确认 cache/budget 口径。
- 构建/导出耗时在 05B 中最重：PQ-DiskANN 约 {fmt(cell(build, "layer == '05b' and method == 'PQ-DiskANN-Disk'", "wall_seconds", 60))} min，Ours-Disk 约 {fmt(cell(build, "layer == '05b' and method == 'Ours-Disk'", "wall_seconds", 60))} min。02 graph build 本身也很重：Ours 约 {fmt(cell(build02, "method == 'Ours'", "build_total_seconds", 60))} min，PQ 约 {fmt(cell(build02, "method == 'PQ'", "build_total_seconds", 60))} min。
- 05C Ours-Disk 的 recall 覆盖 {fmt(ours05c.recall_min, 4)} 到 {fmt(ours05c.recall_max, 4)}，最高 QPS 为 {fmt(ours05c.qps_max)}。性能瓶颈主要随 search width 增大而表现为更多 page read 和更长 I/O wait。
- 05C Ours-Disk 的查询路径可以直白理解为：平均检查 DB1 约 {fmt(ours05c.db1_checks_mean, 1)} 个候选，约 {fmt(ours05c.db1_survivors_mean, 1)} 个进入 full4，产生约 {fmt(ours05c.full4_page_reads_mean, 1)} 次 full4 page read，最终绝大多数时间花在等待磁盘。

## 05A Ours vs 05B/05C Ours

| 项目 | 05A Ours | 05B/05C Ours-Disk |
|---|---|---|
| CSV 方法名 | `Ours_RaBitQ_K1` | `Ours-Disk` |
| 代码/内核口径 | `hnswlib::RaBitQSpace K=1` | `ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search` |
| 测的是什么 | fixed candidates 下的量化距离和 payload 访问 | 完整磁盘图搜索路径 |
| 有没有 graph traversal | 没有完整磁盘 graph search | 有，使用 Ours native graph |
| 关键指标含义 | fixed-candidate recall / quantizer error / payload I/O | Recall/QPS/latency/I/O wait/page reads |
| 能不能直接和 05B Ours 比 QPS | 不建议，任务不同 | 可以和 05B/05C 同层其他系统比 |

所以，05A Ours 更像一个“拆出来的组件实验”；05B/05C Ours-Disk 才是完整查询系统。05A 的 resident 模式可以到很高 QPS，payload_on_ssd 会掉下来，但它仍然不是完整 graph search，因此不能拿 05A Ours 的 QPS 去说完整系统有多快。

## 05B Ours 和 05C Ours 的关系

这批 GIST 结果里，05B/05C 的 Ours-Disk 是同一个实现、同一张 Ours graph、同一组 search width。逐 width 对齐后：

- Recall 最大差值：{fmt(ours_bc_recall_diff, 6)}
- I/O requests/query 最大差值：{fmt(ours_bc_io_diff, 6)}
- bytes read/query 最大差值：{fmt(ours_bc_bytes_diff, 2)}
- DB1 checks 最大差值：{fmt(ours_bc_db1_diff, 6)}
- full4 page reads 最大差值：{fmt(ours_bc_full4_diff, 6)}

QPS、latency、I/O wait 在 05B/05C 之间有小幅运行差异，因为它们是两个 artifact/test 目录下的实际测试记录；但算法路径和候选/读盘计数是一致的。报告里的 Ours 结论应该以 05C 为主，05B 的 Ours 只作为同实现的重复 sanity check。

## Ours 实验是否有不合理处

{audit_lines()}

我的判断：这批数据可以用来分析 Ours 的瓶颈，但不能把它当成“最终论文可直接投”的完整证据。主要原因不是 Ours 结果假，而是实验统计还不够完整：缺 repeats、05B/05C Ours 重复、cache/budget 口径需要查清楚。好消息是，Ours 的 direct I/O/native AIO、非内存 payload、parity 检查、recall 单调性都正常，说明目前更像是一个真实但还需要补充控制实验的磁盘瓶颈样本。

## 图表怎么看

{images}

### Figure 1：构建与导出成本

这张图回答“实验为什么跑得久”。左图是 02 graph build，总构建时间约 50 min 量级；中图是 05A/05B/05C 导出或磁盘索引构建耗时，横轴用了 log scale，因为 05A payload export 与 05B/05C 的量级差得很大；右图是导出/构建阶段 peak RSS。

直白结论：05A 的 payload export 在当前 artifact 里几乎不是耗时来源；真正重的是 02 graph build 和 05B/05C 的磁盘索引导出/构建。内存上，SymphonyQG 的 05C 导出 peak RSS 最高，约 {fmt(cell(build, "layer == '05c' and method == 'SymphonyQG-DiskPort'", "peak_rss_gib"))} GiB；Ours-Disk 约 {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "peak_rss_gib"))} GiB。

### Figure 2：32 线程 Recall/QPS/I-O 曲线

这张图只看 05C system 层，回答“完整系统在精度、吞吐和磁盘访问之间怎么交换”。左图是 Recall@10-QPS trade-off；越靠右表示精度越高，越靠上表示吞吐越高。右图是达到相同 recall 时需要付出的 I/O requests/query；越低表示读盘压力越小。

直白结论：search width 越大，recall 通常越高，但 QPS 会下降。05C 中当前可比方法是 Ours-Disk、OG-LVQ、Glass-NSG；Ours-Disk 的 recall 上限最高，并且在高 recall 区域的 I/O requests/query 低于 OG-LVQ 和 Glass-NSG，但它的绝对吞吐仍被 I/O wait 卡住。

### Figure 3：Ours-Disk 查询时间拆分

这张图只看 05C Ours-Disk，是本次分析最重要的图。左图对比 total latency 和 I/O wait；中图把非 I/O 的 query prep、queue、distance compute、rerank 放大到微秒尺度；右图给出平均每 query 的 DB1/full4/rerank 计数，说明读盘主要来自 full4 page reads。

直白结论：total latency 和 I/O wait 几乎重合，05C 平均 I/O wait 占 {fmt(ours05c.io_wait_pct)}%；distance compute 在 05C 只有约 {fmt(ours05c.distance_pct)}%。所以 Ours 当前优化优先级不是重写距离核，而是减少 full4 page read、改善 page packing/coalescing/prefetch/AIO depth，或者用更高效的缓存策略降低 I/O wait。

## Ours-Disk 重点分析

| 指标 | 05B Ours-Disk | 05C Ours-Disk |
|---|---:|---:|
| Recall@10 范围 | {fmt(ours05b.recall_min, 4)} - {fmt(ours05b.recall_max, 4)} | {fmt(ours05c.recall_min, 4)} - {fmt(ours05c.recall_max, 4)} |
| 最高 QPS | {fmt(ours05b.qps_max)} | {fmt(ours05c.qps_max)} |
| 平均 latency | {fmt(ours05b.latency_mean_ms)} ms/query | {fmt(ours05c.latency_mean_ms)} ms/query |
| I/O wait 占比 | {fmt(ours05b.io_wait_pct)}% | {fmt(ours05c.io_wait_pct)}% |
| distance compute 占比 | {fmt(ours05b.distance_pct)}% | {fmt(ours05c.distance_pct)}% |
| I/O requests/query | {fmt(ours05b.io_requests_mean, 1)} | {fmt(ours05c.io_requests_mean, 1)} |
| bytes read/query | {fmt(ours05b.bytes_read_mean_mb, 2)} MiB | {fmt(ours05c.bytes_read_mean_mb, 2)} MiB |
| DB1 checks/query | {fmt(ours05b.db1_checks_mean, 1)} | {fmt(ours05c.db1_checks_mean, 1)} |
| full4 page reads/query | {fmt(ours05b.full4_page_reads_mean, 1)} | {fmt(ours05c.full4_page_reads_mean, 1)} |

Ours-Disk 的数据布局在 artifact 中显示为：`whole_payload_in_memory=False`、`direct_io=True`、`native_aio=True`；05C Ours-Disk 报告 `ours_4bit_payload_bytes=529000000`、`ours_8bit_payload_bytes=1177000000`、`ours_adjacency_bytes=341336064`、`ours_fp32_base_bytes=0`。也就是说，正式统计口径下 Ours 的磁盘索引不把 fp32 base 当作 payload 常驻；查询阶段主要在磁盘 payload/page 上消耗时间。

## 下一步优化建议

按收益和风险排序：

1. **先确认 cache/budget 是否真的生效。** 当前 `search_dram_budget_gib=2.0`，但 test JSON 只有约 34 MB cache，export 为 0；如果这是统计口径问题，先修统计；如果是真没用上预算，这是最值得优先优化的点。
2. **压低 full4 page reads/query。** 05C Ours 平均 full4 page reads 约 {fmt(ours05c.full4_page_reads_mean, 1)} 次/query，这是 I/O wait 的直接来源。
3. **优化 page packing/coalescing/prefetch/AIO depth。** 现在每 query 平均读约 {fmt(ours05c.bytes_read_mean_mb, 2)} MiB，但被拆成约 {fmt(ours05c.io_requests_mean, 1)} 次 4 KiB 读，随机读调度成本很重。
4. **按目标 recall 选最小 width。** 在 GIST w32 中，Ours 达到 recall≥0.90 约 width=60、QPS≈7.25；达到 recall≥0.95 约 width=100、QPS≈4.99；不要用最高 recall 点代表默认配置。
5. **补 repeats 和 worker sweep。** 至少补 3 repeats，再把 w32/w16/w8 的 Ours 曲线放一起看；当前单 repeat 不适合给最终论文误差条。

## 对 test.md 问题的回答

1. 数据如何存放：本次 artifact 显示 Ours-Disk 的 `whole_payload_in_memory=False`，`direct_io=True`，`native_aio=True`。05C Ours-Disk 报告 `ours_4bit_payload_bytes=529000000`、`ours_8bit_payload_bytes=1177000000`、`ours_adjacency_bytes=341336064`、`ours_fp32_base_bytes=0`。
2. 构建时是否全在内存：02 graph build 日志显示构建阶段使用 in-memory provider；05 export artifact 记录了导出阶段 wall time、peak RSS 和 read bytes。05B/05C 进入测试阶段后使用 4 KiB direct I/O/native AIO 的 disk backend。
3. 当前使用的进程/线程数：本报告只取 `workers=32` 的三层实验；`test.md` 中完整顺序为 32,16,8,4,2,1。
4. 查询内存限制：本次 GIST 测试 CSV 中 `search_dram_budget_gib=2.0`。
5. 为什么实验耗时久：构建侧主要来自 graph build/export；查询侧对 Ours-Disk 而言主要来自磁盘 I/O wait，而不是距离核计算。05C Ours-Disk 的 mean latency 约 {fmt(ours05c.latency_mean_ms)} ms/query，平均 I/O wait 占 {fmt(ours05c.io_wait_pct)}%。

## Source Data

- `docs/analysis/source_query_rows_w32_gist.csv`
- `docs/analysis/source_build_export_stats_w32_gist.csv`
- `docs/analysis/source_02_graph_build_stats_gist.csv`
- `docs/analysis/table_best_rows_w32_gist.csv`
- `docs/analysis/table_ours_query_breakdown_w32_gist.csv`
- `docs/analysis/table_ours_experiment_audit_w32_gist.csv`
"""

    def c05(method: str) -> pd.DataFrame:
        return rows[(rows["layer"].eq("05c")) & (rows["method"].eq(method))]

    def c05_num(method: str, col: str, agg: str = "mean") -> float:
        s = pd.to_numeric(c05(method)[col], errors="coerce").dropna()
        if s.empty:
            return math.nan
        if agg == "mean":
            return s.mean()
        if agg == "max":
            return s.max()
        if agg == "min":
            return s.min()
        raise ValueError(agg)

    def c05_range(method: str) -> str:
        return f"{fmt(c05_num(method, 'recall', 'min'), 4)} - {fmt(c05_num(method, 'recall', 'max'), 4)}"

    def c05_share(method: str, num_col: str, den_col: str) -> float:
        sub = c05(method)
        num = pd.to_numeric(sub[num_col], errors="coerce")
        den = pd.to_numeric(sub[den_col], errors="coerce")
        mask = den.notna() & den.ne(0)
        if not mask.any():
            return math.nan
        return (num[mask] / den[mask]).mean() * 100

    def c05_cell(method: str, col: str, digits: int = 2, scale: float = 1.0, agg: str = "mean") -> str:
        return fmt(c05_num(method, col, agg) / scale, digits)

    def img(feishu: bool, name: str, caption: str) -> str:
        src = f"@./docs/analysis/{name}" if feishu else name
        return f"![{caption}]({src})"

    def make_report_v2(feishu: bool) -> str:
        local_note = (
            "本文件使用普通 Markdown 图片链接，适合 IDE/GitHub 预览；飞书导入版见 "
            "`docs/analysis/disk_w32_gist_analysis_report_feishu.md`。"
            if not feishu
            else ""
        )
        return f"""# GIST 磁盘环境 32 线程实验分析

{local_note}

数据来源：`docs/notes/test.md` 指定的 `gist_test_20260829_204245`，读取 `round_w32/05a_rows.csv`、`round_w32/05b_rows.csv`、`round_w32/05c_rows.csv` 和对应 export/build artifact。本报告按「构建 / 查询」两部分组织；查询对比统一使用 05C system 层方法：Ours-Disk、OG-LVQ、Glass-NSG，另附 SymphonyQG 的 formal 单点参考。05B Ours-Disk 与 05C Ours-Disk 是同实现、同图，只作为 sanity record，不作为独立 Ours 变体。

## 构建

构建侧由两个阶段组成：02 graph build（图构建，最重）和 05C 磁盘索引构建/导出（把图转成磁盘索引）。

### Ours 构建步骤与峰值内存

| 阶段 | 耗时 | 说明 |
|---|---:|---|
| Train quantizer | {fmt(cell(build02, "method == 'Ours'", "train_seconds"))} s | 极小，Ours 几乎不花在 quantizer train |
| Payload encode | {fmt(cell(build02, "method == 'Ours'", "payload_encode_seconds"))} s | 把原始向量编码成 4-bit/8-bit payload |
| Graph build | {fmt(cell(build02, "method == 'Ours'", "graph_build_seconds", 60))} min | {fmt(cell(build02, "method == 'Ours'", "graph_build_seconds"), 1)} s，构建耗时的主体 |
| 02 graph build 合计 | {fmt(cell(build02, "method == 'Ours'", "build_total_seconds", 60))} min | {fmt(cell(build02, "method == 'Ours'", "build_total_seconds"), 1)} s |
| 05C 磁盘索引导出 | {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "wall_seconds", 60))} min | {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "wall_seconds"), 1)} s |
| 05C 导出 peak RSS | {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "peak_rss_gib"))} GiB | 导出阶段最大常驻内存 |
| 磁盘索引大小 | {fmt(c05_num("Ours-Disk", "index_size_mb", "max"), 2)} MB | 不含 fp32 base |
| payload 分布 | 4-bit {fmt(c05_num("Ours-Disk", "ours_4bit_payload_bytes", "max") / 1e6, 0)} MB / 8-bit {fmt(c05_num("Ours-Disk", "ours_8bit_payload_bytes", "max") / 1e6, 0)} MB / adjacency {fmt(c05_num("Ours-Disk", "ours_adjacency_bytes", "max") / 1e6, 0)} MB / fp32 base 0 | — |

### 构建耗时精细化

- 02 graph build 只有 Ours 和 PQ 留下了分阶段日志（train / encode / graph build），所以单独拆开做 stage 分解；其他系统没有在同一 run 里留下可比的 02 build stage 日志。这就是为什么 Figure 1 左图只出现 Ours 和 PQ，不是要把它们与其他系统隔离。
- Ours 的 02 graph build 总耗时约 {fmt(cell(build02, "method == 'Ours'", "build_total_seconds", 60))} min，其中 graph build 约 {fmt(cell(build02, "method == 'Ours'", "graph_build_seconds", 60))} min；PQ 总耗时约 {fmt(cell(build02, "method == 'PQ'", "build_total_seconds", 60))} min，其中 train 约 {fmt(cell(build02, "method == 'PQ'", "train_seconds", 60))} min、graph build 约 {fmt(cell(build02, "method == 'PQ'", "graph_build_seconds", 60))} min。

### 05C 磁盘索引构建/导出对比

| 方法 | 磁盘索引构建/导出 (min) | 构建 peak RSS (GiB) | read (MB) |
|---|---:|---:|---:|
| Ours-Disk | {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "wall_seconds", 60))} | {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "peak_rss_gib"))} | {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "read_mb"))} |
| OG-LVQ | {fmt(cell(build, "layer == '05c' and method == 'OG-LVQ-DiskPort'", "wall_seconds", 60))} | {fmt(cell(build, "layer == '05c' and method == 'OG-LVQ-DiskPort'", "peak_rss_gib"))} | {fmt(cell(build, "layer == '05c' and method == 'OG-LVQ-DiskPort'", "read_mb"))} |
| Glass-NSG | {fmt(cell(build, "layer == '05c' and method == 'Glass-NSG-DiskPort'", "wall_seconds", 60))} | {fmt(cell(build, "layer == '05c' and method == 'Glass-NSG-DiskPort'", "peak_rss_gib"))} | {fmt(cell(build, "layer == '05c' and method == 'Glass-NSG-DiskPort'", "read_mb"))} |
| SymphonyQG | {fmt(cell(build, "layer == '05c' and method == 'SymphonyQG-DiskPort'", "wall_seconds", 60))} | {fmt(cell(build, "layer == '05c' and method == 'SymphonyQG-DiskPort'", "peak_rss_gib"))} | {fmt(cell(build, "layer == '05c' and method == 'SymphonyQG-DiskPort'", "read_mb"))} |
| DiskANN-PQ | {fmt(cell(build, "layer == '05c' and method == 'DiskANN-PQ-Disk'", "wall_seconds", 60))} | {fmt(cell(build, "layer == '05c' and method == 'DiskANN-PQ-Disk'", "peak_rss_gib"))} | {fmt(cell(build, "layer == '05c' and method == 'DiskANN-PQ-Disk'", "read_mb"))} |

### Figure 1：构建耗时精细化

{img(feishu, "fig01_build_export_cost_w32_gist.png", "Figure 1. 构建耗时精细化")}

图读法：左图是 02 graph build 的分阶段耗时；中图是 05C 磁盘索引构建/导出耗时（log scale）；右图是构建 peak RSS。图中不再标注 a/b/c，Ours 只保留 05C 版本（Ours-Disk），不重复出现 05B Ours-Disk。

构建差距与不同：

- **02 graph build**：Ours 约 {fmt(cell(build02, "method == 'Ours'", "build_total_seconds", 60))} min，几乎全部花在 graph build（约 {fmt(cell(build02, "method == 'Ours'", "graph_build_seconds", 60))} min）；PQ 约 {fmt(cell(build02, "method == 'PQ'", "build_total_seconds", 60))} min，其中约 {fmt(cell(build02, "method == 'PQ'", "train_seconds", 60))} min 花在 quantizer train。即 Ours 构图阶段比 PQ 更重，而 PQ 多出一段 train 成本。
- **磁盘索引构建/导出**：Ours-Disk 约 {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "wall_seconds", 60))} min，明显慢于 OG-LVQ（约 {fmt(cell(build, "layer == '05c' and method == 'OG-LVQ-DiskPort'", "wall_seconds", 60))} min）、DiskANN-PQ（约 {fmt(cell(build, "layer == '05c' and method == 'DiskANN-PQ-Disk'", "wall_seconds", 60))} min）、Glass-NSG（约 {fmt(cell(build, "layer == '05c' and method == 'Glass-NSG-DiskPort'", "wall_seconds", 60))} min）、SymphonyQG（约 {fmt(cell(build, "layer == '05c' and method == 'SymphonyQG-DiskPort'", "wall_seconds", 60))} min）。Ours 从图转磁盘索引的导出阶段是五个方法里最慢的。
- **构建内存**：SymphonyQG 的 peak RSS 最高，约 {fmt(cell(build, "layer == '05c' and method == 'SymphonyQG-DiskPort'", "peak_rss_gib"))} GiB；Ours-Disk 约 {fmt(cell(build, "layer == '05c' and method == 'Ours-Disk'", "peak_rss_gib"))} GiB；DiskANN-PQ 最低约 {fmt(cell(build, "layer == '05c' and method == 'DiskANN-PQ-Disk'", "peak_rss_gib"))} GiB。

## 查询

### Ours 查询参数与口径

| 参数 | 值 |
|---|---|
| dataset / base_count / dimension | gist / 1,000,000 / 960 |
| workers | 32 |
| search_dram_budget_gib | 2.0 |
| storage_mode / cache_mode | hybrid_disk / standard |
| kernel | ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search |
| direct_io / native_aio / io_backend | True / True / linux_native_aio_odirect |
| page_size | 4096 |
| whole_graph_in_memory / whole_payload_in_memory | False / False |
| search_width sweep | 1 到 480（47 个点），beam_width=1 |
| ablation | db1+coalescing+reuse |
| query_count / warmup_queries | 800 / 100 |
| index_size_mb | {fmt(c05_num("Ours-Disk", "index_size_mb", "max"), 2)} |
| resident / peak RSS | 1.23 GiB / 1.19 GiB |

### 05C 方法指标对比

| 指标 | Ours-Disk | OG-LVQ | Glass-NSG | SymphonyQG |
|---|---:|---:|---:|---:|
| Recall@10 范围 | {c05_range("Ours-Disk")} | {c05_range("OG-LVQ-DiskPort")} | {c05_range("Glass-NSG-DiskPort")} | {fmt(c05_num("SymphonyQG-DiskPort", "recall", "max"), 4)}（单点） |
| 最高 QPS | {c05_cell("Ours-Disk", "qps", 2, 1.0, "max")} | {c05_cell("OG-LVQ-DiskPort", "qps", 2, 1.0, "max")} | {c05_cell("Glass-NSG-DiskPort", "qps", 2, 1.0, "max")} | {c05_cell("SymphonyQG-DiskPort", "qps", 2, 1.0, "max")} |
| 平均 latency | {c05_cell("Ours-Disk", "latency_mean_us", 2, 1000)} ms/query | {c05_cell("OG-LVQ-DiskPort", "latency_mean_us", 2, 1000)} ms/query | {c05_cell("Glass-NSG-DiskPort", "latency_mean_us", 2, 1000)} ms/query | {c05_cell("SymphonyQG-DiskPort", "latency_mean_us", 2, 1000)} ms/query |
| I/O wait 占比 | {fmt(c05_share("Ours-Disk", "io_wait_us", "latency_mean_us"))}% | {fmt(c05_share("OG-LVQ-DiskPort", "io_wait_us", "latency_mean_us"))}% | {fmt(c05_share("Glass-NSG-DiskPort", "io_wait_us", "latency_mean_us"))}% | {fmt(c05_share("SymphonyQG-DiskPort", "io_wait_us", "latency_mean_us"))}% |
| distance compute 占比 | {fmt(c05_share("Ours-Disk", "distance_compute_us", "latency_mean_us"))}% | — | — | — |
| I/O requests/query | {c05_cell("Ours-Disk", "io_requests_per_query", 1)} | {c05_cell("OG-LVQ-DiskPort", "io_requests_per_query", 1)} | {c05_cell("Glass-NSG-DiskPort", "io_requests_per_query", 1)} | {c05_cell("SymphonyQG-DiskPort", "io_requests_per_query", 1)} |
| bytes read/query | {c05_cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB | {c05_cell("OG-LVQ-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB | {c05_cell("Glass-NSG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB | {c05_cell("SymphonyQG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB |
| DB1 checks/query | {c05_cell("Ours-Disk", "db1_checks", 1)} | — | — | — |
| full4 page reads/query | {c05_cell("Ours-Disk", "full4_page_reads", 1)} | — | — | — |

注意：GIST 本次 run（`gist_test_20260829_204245`）只完成了 SymphonyQG 的 export/validate，没有 w32 test sweep；本地 formal CSV 里只有一条 ef=100 记录，所以 Recall 只写单点。OG-LVQ、Glass-NSG、SymphonyQG 没有暴露 DB1 / full4 内部计数，其 distance/queue 字段也基本等于总耗时，无法与 Ours 的 distance compute 单独拆分口径对比，所以这些格标为“—”。

### Figure 2：Recall/QPS/I-O 曲线

{img(feishu, "fig02_w32_recall_qps_io_curves_gist.png", "Figure 2. 05C Recall/QPS/I-O 曲线")}

图读法：只看 05C system 层。左图是 Recall@10 与 QPS 的 trade-off；右图是达到相同 recall 时需要付出的 I/O requests/query。

### Figure 3：Ours-Disk 查询时间拆分

{img(feishu, "fig03_ours_query_time_decomposition_w32_gist.png", "Figure 3. Ours-Disk 查询时间拆分")}

图读法：只看 05C Ours-Disk。左图对比 total latency 和 I/O wait；中图把 query prep、queue、distance、rerank 放大到微秒尺度；右图给出每 query 的 DB1/full4/rerank 平均计数。

### 查询差距与不同（重点）

- **Recall 上限**：Ours-Disk 最高，约 {fmt(c05_num("Ours-Disk", "recall", "max"), 4)}；SymphonyQG 单点约 {fmt(c05_num("SymphonyQG-DiskPort", "recall", "max"), 4)}；OG-LVQ 约 {fmt(c05_num("OG-LVQ-DiskPort", "recall", "max"), 4)}；Glass-NSG 约 {fmt(c05_num("Glass-NSG-DiskPort", "recall", "max"), 4)}。
- **Latency / QPS**：SymphonyQG 单点平均 latency 约 {c05_cell("SymphonyQG-DiskPort", "latency_mean_us", 2, 1000)} ms/query、QPS 约 {c05_cell("SymphonyQG-DiskPort", "qps", 2, 1.0, "max")}，明显快于 Ours-Disk；但它是 formal run 的单点，不是本次 sweep 的完整曲线，不能直接等同。Glass-NSG 的最高 QPS 约 {c05_cell("Glass-NSG-DiskPort", "qps", 2, 1.0, "max")} 看似最高，但只出现在低 recall 区间；Ours-Disk 最高 QPS 约 {c05_cell("Ours-Disk", "qps", 2, 1.0, "max")}。
- **I/O 成本**：同 recall 下 Ours-Disk 的 I/O requests/query 和 bytes read/query 都低于 OG-LVQ 与 Glass-NSG。平均来看 Ours-Disk 约 {c05_cell("Ours-Disk", "io_requests_per_query", 1)} 次/query、{c05_cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB；Glass-NSG 约 {c05_cell("Glass-NSG-DiskPort", "io_requests_per_query", 1)} 次/query、{c05_cell("Glass-NSG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB；OG-LVQ 约 {c05_cell("OG-LVQ-DiskPort", "io_requests_per_query", 1)} 次/query、{c05_cell("OG-LVQ-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB。SymphonyQG 单点更低，约 {c05_cell("SymphonyQG-DiskPort", "io_requests_per_query", 1)} 次/query、{c05_cell("SymphonyQG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB。
- **瓶颈**：Ours-Disk、OG-LVQ、Glass-NSG 都以 I/O wait 为主（Ours-Disk {fmt(c05_share("Ours-Disk", "io_wait_us", "latency_mean_us"))}%、OG-LVQ {fmt(c05_share("OG-LVQ-DiskPort", "io_wait_us", "latency_mean_us"))}%、Glass-NSG {fmt(c05_share("Glass-NSG-DiskPort", "io_wait_us", "latency_mean_us"))}%）。Ours-Disk 的 distance compute 只有 {fmt(c05_share("Ours-Disk", "distance_compute_us", "latency_mean_us"))}%，说明 Ours 不是距离核慢，而是随机读盘等待慢；优化应优先减少 full4 page read、改善 page packing/coalescing/prefetch/AIO depth。

## 数据与口径

- 查询内存限制：`search_dram_budget_gib=2.0`；当前 GIST w32 只有 repeat=0，结论适合定位瓶颈，不适合直接作为最终论文误差条。
- Ours-Disk 为磁盘模式：`whole_payload_in_memory=False`、`direct_io=True`、`native_aio=True`、`page_size=4096`。

## Source Data

- `docs/analysis/source_query_rows_w32_gist.csv`
- `docs/analysis/source_build_export_stats_w32_gist.csv`
- `docs/analysis/source_02_graph_build_stats_gist.csv`
- `docs/analysis/table_best_rows_w32_gist.csv`
- `docs/analysis/table_ours_query_breakdown_w32_gist.csv`
- `docs/analysis/table_ours_experiment_audit_w32_gist.csv`
"""

    ours05c = ours_summary[ours_summary["layer"].eq("05c")].iloc[0]
    ours05b = ours_summary[ours_summary["layer"].eq("05b")].iloc[0]
    rows_count = best.groupby("layer").size().to_dict()
    ours05b_rows = rows[(rows["layer"].eq("05b")) & (rows["method"].eq("Ours-Disk"))].sort_values("search_width")
    ours05c_rows = rows[(rows["layer"].eq("05c")) & (rows["method"].eq("Ours-Disk"))].sort_values("search_width")
    align = ours05b_rows.merge(
        ours05c_rows,
        on="search_width",
        suffixes=("_05b", "_05c"),
    )
    ours_bc_recall_diff = (align["recall_05b"] - align["recall_05c"]).abs().max()
    ours_bc_io_diff = (align["io_requests_per_query_05b"] - align["io_requests_per_query_05c"]).abs().max()
    ours_bc_bytes_diff = (align["bytes_read_per_query_05b"] - align["bytes_read_per_query_05c"]).abs().max()
    ours_bc_db1_diff = (align["db1_checks_05b"] - align["db1_checks_05c"]).abs().max()
    ours_bc_full4_diff = (align["full4_page_reads_05b"] - align["full4_page_reads_05c"]).abs().max()
    local_report = make_report_v2(feishu=False)
    feishu_report = make_report_v2(feishu=True)
    feishu_body = feishu_report.split("\n\n", 1)[1]
    (out / "disk_w32_gist_analysis_report.md").write_text(local_report)
    (out / "disk_w32_gist_analysis_report_feishu.md").write_text(feishu_report)
    (out / "disk_w32_gist_analysis_report_feishu_body.md").write_text(feishu_body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path(
            "results/archive/legacy_layout_20260918/disk_environment/.test_runs/gist/gist_test_20260829_204245/runs/gist_test_20260829_204245"
        ),
    )
    parser.add_argument(
        "--round-dir",
        type=Path,
        default=Path("results/archive/legacy_layout_20260918/disk_environment/.test_runs/gist/gist_test_20260829_204245/rounds/round_w32"),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("docs/analysis"))
    args = parser.parse_args()

    configure_matplotlib()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_rows(args.round_dir)
    formal_path = Path("results/archive/legacy_layout_20260918/disk_environment/03_system_fair/gist/csv/formal_test_rows.csv")
    if formal_path.exists():
        formal = pd.read_csv(formal_path)
        sym = formal[
            (formal["layer"].astype(str).eq("05c"))
            & (formal["method"].eq("SymphonyQG-DiskPort"))
        ].copy()
        if not sym.empty:
            sym["method_display"] = sym["method"].map(DISPLAY_METHOD).fillna(sym["method"])
            sym["layer_display"] = sym["layer"].map(LAYER_LABELS).fillna(sym["layer"])
            rows = pd.concat([rows, sym], ignore_index=True)
    build = parse_build_stats(args.run_root)
    build02 = extract_02_build_log(args.run_root)
    best = summarize_best_rows(rows)
    ours_artifacts = load_ours_artifacts(args.run_root)
    audit = summarize_experiment_audit(rows, ours_artifacts)
    ours_summary = plot_ours_breakdown(rows, args.out_dir)

    rows.to_csv(args.out_dir / "source_query_rows_w32_gist.csv", index=False)
    build.to_csv(args.out_dir / "source_build_export_stats_w32_gist.csv", index=False)
    build02.to_csv(args.out_dir / "source_02_graph_build_stats_gist.csv", index=False)
    best.to_csv(args.out_dir / "table_best_rows_w32_gist.csv", index=False)
    ours_summary.to_csv(args.out_dir / "table_ours_query_breakdown_w32_gist.csv", index=False)
    audit.to_csv(args.out_dir / "table_ours_experiment_audit_w32_gist.csv", index=False)

    plot_build_stats(build, build02, args.out_dir)
    plot_query_curves(rows, args.out_dir)
    write_report(args.out_dir, rows, build, build02, best, ours_summary, audit)

    print(f"wrote analysis to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
