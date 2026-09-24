#!/usr/bin/env python3
"""Paper-grade AGNews 05C figures (Python/matplotlib, nature-figure contract)."""

from __future__ import annotations

import math
import os
import json
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path("docs/analysis/.mplconfig").resolve()))

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd


# --- Figure contract ---
# Core conclusion: on AGNews disk environment (w32), Ours-Disk reaches the
# highest Recall@10 with the fewest I/O requests/query at matched recall, yet
# its latency is almost entirely I/O wait and its QPS remains disk-bound.
# Panels:
#   Fig 2a: Recall@10 vs QPS            -> quality/throughput trade-off
#   Fig 2b: Recall@10 vs I/O req/query  -> I/O price of recall
#   Fig 3a: Ours latency vs width       -> latency is I/O wait
#   Fig 3b: Ours non-I/O terms          -> compute terms are small
#   Fig 3c: Ours mean reads per query   -> where reads come from

METHOD_DISPLAY = {
    "Ours-Disk": "Ours",
    "OG-LVQ-DiskPort": "OG-LVQ",
    "Glass-NSG-DiskPort": "Glass-NSG",
    "SymphonyQG-DiskPort": "SymphonyQG",
}

COLORS = {
    "Ours": "#C2417A",
    "OG-LVQ": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
    "SymphonyQG": "#0F766E",
}

GRID = "#DDE1E6"
PANEL_BG = "#FFFFFF"


def configure() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 7,
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


def soften(ax: plt.Axes) -> None:
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=7)
    ax.margins(x=0.04)


def label_panel(ax: plt.Axes, label: str) -> None:
    ax.text(
        -0.12,
        1.04,
        label,
        transform=ax.transAxes,
        fontsize=8,
        fontweight="bold",
        va="top",
        ha="left",
    )


def _current_rid() -> str:
    rid_path = Path("/tmp/agnews_05c_rerun_rid.txt")
    if rid_path.exists():
        return rid_path.read_text().strip()
    return "agnews_05c_rerun_20260901_152210"


def _ours_local_rows() -> pd.DataFrame:
    p = Path(
        f"results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/{_current_rid()}/05C_disk_system_fair/"
        "agnews/artifacts/test/Ours-Disk__hybrid_disk__B2__standard__w32__r0.json"
    )
    if not p.exists():
        return pd.DataFrame()
    d = json.loads(p.read_text())
    rows = pd.DataFrame(d["summary_rows"])
    constant = {
        "method": d.get("method"),
        "layer": "05c",
        "dataset": d.get("dataset"),
        "workers": d.get("workers"),
    }
    for key, value in constant.items():
        rows[key] = value
    return rows


def read_rows() -> pd.DataFrame:
    base = Path("results/archive/legacy_layout_20260918/disk_environment/03_system_fair/agnews/csv")
    new = pd.read_csv(base / "formal_test_rows.csv")
    new = new[new["method"].isin(["OG-LVQ-DiskPort", "Glass-NSG-DiskPort"])].copy()
    fix_agg = Path(
        "results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/"
        "05C_disk_system_fair/agnews/aggregate/formal_test_rows.csv"
    )
    sym = pd.read_csv(fix_agg)
    sym = sym[sym["method"].eq("SymphonyQG-DiskPort")].copy()
    ours = _ours_local_rows()
    df = pd.concat([new, sym, ours], ignore_index=True)
    numeric = [
        "recall",
        "qps",
        "latency_mean_us",
        "io_wait_us",
        "distance_compute_us",
        "query_prep_us",
        "queue_compute_us",
        "rerank_us",
        "io_requests_per_query",
        "db1_checks",
        "db1_survivors",
        "full4_candidates",
        "full4_page_reads",
        "rerank_candidates",
        "rerank_page_reads",
    ]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["method_display"] = df["method"].map(METHOD_DISPLAY).fillna(df["method"])
    return df


def _pareto(g: pd.DataFrame, yfield: str, maximize: bool) -> list[pd.Series]:
    by_recall: dict[float, tuple[pd.Series, float]] = {}
    for _, row in g.iterrows():
        rec = round(float(row["recall"]), 10)
        val = float(row[yfield])
        cur = by_recall.get(rec)
        if cur is None or (val > cur[1] if maximize else val < cur[1]):
            by_recall[rec] = (row, val)
    ordered = sorted(by_recall.values(), key=lambda item: float(item[0]["recall"]), reverse=True)
    frontier: list[pd.Series] = []
    best = -math.inf if maximize else math.inf
    for row, val in ordered:
        better = val > best if maximize else val < best
        if better:
            frontier.append(row)
            best = val
    return list(reversed(frontier))


def _log_y(ax: plt.Axes) -> None:
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda value, _: f"{value:g}"))
    ax.yaxis.set_minor_formatter(mpl.ticker.NullFormatter())


def plot_fig2(df: pd.DataFrame, out: Path) -> None:
    order = ["Ours", "SymphonyQG", "OG-LVQ", "Glass-NSG"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.7), constrained_layout=True)
    legend_handles: list = []
    legend_labels: list[str] = []
    plotted_recalls: list[float] = []

    for method in order:
        g = df[df["method_display"].eq(method)].sort_values("recall")
        if g.empty:
            continue
        color = COLORS[method]
        ours = method == "Ours"
        lw = 1.8 if ours else 1.1
        z = 5 if ours else 2

        qps_points = _pareto(g, "qps", True)
        if qps_points:
            xs = [float(r["recall"]) for r in qps_points]
            ys = [float(r["qps"]) for r in qps_points]
            plotted_recalls.extend(xs)
            line = axes[0].plot(xs, ys, color=color, linewidth=lw, marker="o" if ours else "s", markersize=3.6 if ours else 2.8, zorder=z)[0]
            if method == "Ours":
                legend_handles.insert(0, line)
                legend_labels.insert(0, method)
            else:
                legend_handles.append(line)
                legend_labels.append(method)
            near95 = min(qps_points, key=lambda r: abs(float(r["recall"]) - 0.95))
            if ours:
                axes[0].plot([float(near95["recall"])], [float(near95["qps"])], marker="*", markersize=7.5, color=color, markeredgecolor="white", markeredgewidth=0.6, zorder=7, clip_on=False)

        io_points = g.sort_values("recall")
        axes[1].plot(
            [float(r) for r in io_points["recall"]],
            [float(r) for r in io_points["io_requests_per_query"]],
            color=color,
            linewidth=lw,
            marker="o" if ours else "s",
            markersize=3.6 if ours else 2.8,
            zorder=z,
        )

    axes[0].axvline(0.95, color="#7A7F87", linestyle=":", linewidth=0.8, zorder=0)
    axes[1].axvline(0.95, color="#7A7F87", linestyle=":", linewidth=0.8, zorder=0)
    if plotted_recalls:
        xmin = max(0.0, min(plotted_recalls) - 0.02)
        xmax = min(1.0, max(plotted_recalls) + 0.01)
    else:
        xmin, xmax = 0.80, 1.0

    axes[0].set_xlabel("Recall@10")
    axes[0].set_ylabel("QPS (32 workers)")
    axes[0].set_xlim(xmin, xmax)
    _log_y(axes[0])
    label_panel(axes[0], "a")
    soften(axes[0])

    axes[1].set_xlabel("Recall@10")
    axes[1].set_ylabel("I/O requests per query")
    axes[1].set_xlim(xmin, xmax)
    _log_y(axes[1])
    label_panel(axes[1], "b")
    soften(axes[1])

    if legend_handles:
        fig.legend(legend_handles, legend_labels, loc="lower center", ncol=4, frameon=False, handlelength=2.0, fontsize=7)

    save_pub(fig, out / "fig02_w32_recall_qps_io_curves_agnews_paper")
    plt.close(fig)


def plot_recall_qps(df: pd.DataFrame, out: Path) -> None:
    order = ["Ours", "SymphonyQG", "OG-LVQ", "Glass-NSG"]
    fig, ax = plt.subplots(figsize=(4.2, 3.0), constrained_layout=True)
    handles: list = []
    labels: list[str] = []
    recalls: list[float] = []
    for method in order:
        g = df[df["method_display"].eq(method)].sort_values("recall")
        if g.empty:
            continue
        color = COLORS[method]
        ours = method == "Ours"
        pts = _pareto(g, "qps", True)
        if not pts:
            continue
        xs = [float(r["recall"]) for r in pts]
        ys = [float(r["qps"]) for r in pts]
        recalls.extend(xs)
        line = ax.plot(
            xs,
            ys,
            color=color,
            linewidth=1.8 if ours else 1.1,
            marker="o" if ours else "s",
            markersize=3.6 if ours else 2.8,
            zorder=5 if ours else 2,
        )[0]
        handles.append(line)
        labels.append(method)
        near95 = min(pts, key=lambda r: abs(float(r["recall"]) - 0.95))
        if ours:
            ax.plot(
                [float(near95["recall"])],
                [float(near95["qps"])],
                marker="*",
                markersize=7.5,
                color=color,
                markeredgecolor="white",
                markeredgewidth=0.6,
                zorder=7,
                clip_on=False,
            )
    ax.axvline(0.95, color="#7A7F87", linestyle=":", linewidth=0.8, zorder=0)
    xmin = max(0.0, min(recalls) - 0.02) if recalls else 0.80
    xmax = min(1.0, max(recalls) + 0.01) if recalls else 1.0
    ax.set_xlim(xmin, xmax)
    ax.set_xlabel("Recall@10")
    ax.set_ylabel("QPS (32 workers)")
    _log_y(ax)
    soften(ax)
    if handles:
        ax.legend(handles, labels, loc="best", frameon=False, fontsize=7, handlelength=2.0)
    save_pub(fig, out / "fig05c_recall_qps_agnews_paper")
    plt.close(fig)


def plot_fig3(df: pd.DataFrame, out: Path) -> None:
    ours = df[df["method"].eq("Ours-Disk")].sort_values("search_width")
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), constrained_layout=True)

    axes[0].plot(
        ours["search_width"],
        ours["latency_mean_us"] / 1000,
        color="#272727",
        linewidth=1.3,
        linestyle="--",
        label="Total",
    )
    axes[0].plot(
        ours["search_width"],
        ours["io_wait_us"] / 1000,
        color=COLORS["Ours"],
        linewidth=2.2,
        label="I/O wait",
    )
    axes[0].set_xlabel("Search width")
    axes[0].set_ylabel("Latency (ms/query)")
    axes[0].set_title("Latency is I/O wait", fontsize=8)
    axes[0].legend(fontsize=7, loc="upper left", handlelength=1.3)
    label_panel(axes[0], "a")
    soften(axes[0])

    non_io = [
        ("queue_compute_us", "Queue", "#7884B4"),
        ("distance_compute_us", "Distance", "#8BCF8B"),
        ("query_prep_us", "Prep", "#767676"),
        ("rerank_us", "Rerank", "#B64342"),
    ]
    for col, label, color in non_io:
        axes[1].plot(ours["search_width"], ours[col], color=color, linewidth=1.4, label=label)
    axes[1].set_xlabel("Search width")
    axes[1].set_ylabel("Non-I/O time (us/query)")
    axes[1].set_title("Compute terms are small", fontsize=8)
    axes[1].legend(fontsize=7, loc="upper left", handlelength=1.2)
    label_panel(axes[1], "b")
    soften(axes[1])

    mechanism = pd.Series(
        {
            "DB1 checks": ours["db1_checks"].mean(),
            "Full4 candidates": ours["full4_candidates"].mean(),
            "Full4 page reads": ours["full4_page_reads"].mean(),
            "Rerank candidates": ours["rerank_candidates"].mean(),
        }
    )
    y = range(len(mechanism))
    colors = ["#D7A9BF", "#B7658E", COLORS["Ours"], "#B0892E"]
    axes[2].barh(list(y), mechanism.values, color=colors, height=0.58)
    axes[2].set_yticks(list(y), mechanism.index)
    axes[2].invert_yaxis()
    axes[2].set_xscale("log")
    axes[2].set_xticks([10, 100, 1000, 10000])
    axes[2].set_xticklabels(["10", "100", "1,000", "10,000"])
    axes[2].set_xlabel("Mean count per query (log)")
    axes[2].set_title("Where reads come from", fontsize=8)
    label_panel(axes[2], "c")
    soften(axes[2])

    save_pub(fig, out / "fig03_ours_query_time_decomposition_w32_agnews_paper")
    plt.close(fig)


def main() -> int:
    configure()
    out = Path("docs/analysis")
    out.mkdir(parents=True, exist_ok=True)
    df = read_rows()
    plot_fig2(df, out)
    plot_fig3(df, out)
    plot_recall_qps(df, out)
    print("wrote paper-grade AGNews figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
