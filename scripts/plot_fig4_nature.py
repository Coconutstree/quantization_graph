#!/usr/bin/env python3
"""Figure 4 (Ours-Disk query-time decomposition) - Nature-style redraw.

Figure contract
---------------
Core conclusion: for Ours-Disk on AGNews 05C, per-query latency is almost
entirely I/O wait; the non-I/O terms (prep/queue/distance/rerank) are at the
microsecond scale; and the reads originate from full4 payload pages after the
DB1 1-bit coarse gate.

Evidence chain:
  a) latency vs search width: Total vs I/O wait coincide -> latency is I/O wait;
  b) non-I/O terms vs search width -> compute/prep terms are negligible;
  c) mean per-query counts (DB1 checks / full4 candidates / full4 page reads /
     rerank candidates) -> reads come from the full4 stage.

Archetype: quantitative grid (three related panels).
Backend: Python (matplotlib), saved preference.
Journal/export contract: double-column (~183 mm = 7.2 in wide), Arial, 7 pt
body, 5 pt glyph floor, editable SVG/PDF text, TIFF 600 dpi, PNG 300 dpi.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

OURS = "#C2417A"
NEUTRAL = "#272727"
GRID = "#DDE1E6"
NON_IO = [
    ("queue_compute_us", "Queue", "#7884B4"),
    ("distance_compute_us", "Distance", "#4E9E6F"),
    ("query_prep_us", "Prep", "#767676"),
    ("rerank_us", "Rerank", "#B64342"),
]
BAR_COLORS = ["#D7A9BF", "#B7658E", OURS, "#B0892E"]


def configure() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 7,
            "figure.facecolor": "#FFFFFF",
            "axes.facecolor": "#FFFFFF",
            "savefig.facecolor": "#FFFFFF",
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


def load_ours() -> pd.DataFrame:
    p = (
        ROOT
        / "results/disk_environment/.formal_runs/runs/agnews_05c_rerun_20260901_152210/"
        "05C_disk_system_fair/agnews/artifacts/test/Ours-Disk__hybrid_disk__B2__standard__w32__r0.json"
    )
    d = json.loads(p.read_text())
    df = pd.DataFrame(d["summary_rows"])
    cols = [
        "search_width",
        "latency_mean_us",
        "io_wait_us",
        "query_prep_us",
        "queue_compute_us",
        "distance_compute_us",
        "rerank_us",
        "db1_checks",
        "db1_survivors",
        "full4_candidates",
        "full4_page_reads",
        "rerank_candidates",
    ]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("search_width")


def save_pub(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight")


def panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(
        -0.14,
        1.04,
        label,
        transform=ax.transAxes,
        fontsize=8,
        fontweight="bold",
        va="top",
        ha="left",
    )


def main() -> int:
    configure()
    ours = load_ours()
    w = ours["search_width"]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), constrained_layout=True)

    # a) latency vs width: Total vs I/O wait
    # log-scale guard: all plotted quantities are strictly positive by construction
    ax = axes[0]
    y_total = np.maximum(ours["latency_mean_us"].to_numpy() / 1000, 1e-9)
    y_io = np.maximum(ours["io_wait_us"].to_numpy() / 1000, 1e-9)
    ax.plot(w, y_total, color=NEUTRAL, linewidth=1.1, linestyle="--", label="Total")
    ax.plot(w, y_io, color=OURS, linewidth=2.0, label="I/O wait")
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(mticker.LogLocator(base=10.0))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_xlabel("Search width")
    ax.set_ylabel("Latency (ms/query)")
    ax.legend(fontsize=6.5, loc="upper left", handlelength=1.4)
    ax.grid(True, axis="y", color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=8)
    panel_label(ax, "a")

    # b) non-I/O terms vs width
    ax = axes[1]
    for col, label, color in NON_IO:
        y = np.maximum(ours[col].to_numpy(), 1e-9)
        ax.plot(w, y, color=color, linewidth=1.3, label=label)
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(mticker.LogLocator(base=10.0))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_xlabel("Search width")
    ax.set_ylabel("Non-I/O time (us/query)")
    ax.legend(fontsize=6.5, loc="upper left", handlelength=1.3)
    ax.grid(True, axis="y", color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=8)
    panel_label(ax, "b")

    # c) mean per-query counts
    ax = axes[2]
    mechanism = pd.Series(
        {
            "DB1 checks": ours["db1_checks"].mean(),
            "Full4 candidates": ours["full4_candidates"].mean(),
            "Full4 page reads": ours["full4_page_reads"].mean(),
            "Rerank candidates": ours["rerank_candidates"].mean(),
        }
    )
    y = range(len(mechanism))
    vals = np.maximum(mechanism.to_numpy(), 1e-9)
    bars = ax.barh(list(y), vals, color=BAR_COLORS, height=0.58, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(mechanism.index, fontsize=6.5)
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(mticker.LogLocator(base=10.0))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_xlabel("Mean count per query (log)")
    for rect, val in zip(bars, vals):
        ax.text(
            val * 1.08,
            rect.get_y() + rect.get_height() / 2,
            f"{val:,.0f}",
            va="center",
            ha="left",
            fontsize=6.5,
            color="#62666D",
        )
    ax.grid(True, axis="x", color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=8)
    panel_label(ax, "c")

    save_pub(fig, ROOT / "docs/analysis/fig03_ours_query_time_decomposition_w32_agnews_paper")
    plt.close(fig)
    print("wrote docs/analysis/fig03_ours_query_time_decomposition_w32_agnews_paper.{svg,pdf,png,tiff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
