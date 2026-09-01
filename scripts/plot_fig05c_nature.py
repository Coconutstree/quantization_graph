#!/usr/bin/env python3
"""Figure 3 (05C system Recall@10-QPS, layer 03) - Nature-style redraw.

Figure contract
---------------
Core conclusion: among the four disk-ported 05C systems on AGNews at a fixed
2 GiB DRAM budget and 32 workers, Ours-Disk sits on the highest recall-QPS
frontier across the full search-width sweep, while SymphonyQG extends to the
highest recall ceiling at a materially lower QPS.

Evidence chain: one panel; every point is one search-width setting of the
40-width sweep (800 queries per width, single repeat). The panel plots the
Pareto frontier of QPS over Recall@10 for each method; no second panel is
needed because the I/O breakdown is a separate claim (Figure 4).

Archetype: single-panel quantitative frontier comparison.
Backend: Python (matplotlib), saved preference.
Journal/export contract: Nature single column (~89 mm = 3.5 in wide), Arial,
7 pt body, 5 pt glyph floor, editable SVG/PDF text, TIFF 600 dpi, PNG 300 dpi
preview.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib as mpl
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

DISPLAY_METHOD = {
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
MARKERS = {"Ours": "o", "SymphonyQG": "D", "OG-LVQ": "^", "Glass-NSG": "s"}
ORDER = ["Ours", "SymphonyQG", "OG-LVQ", "Glass-NSG"]
GRID = "#DDE1E6"


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


def read_rows() -> pd.DataFrame:
    """Assemble the four 05C method sweeps for AGNews at workers=32."""
    base = ROOT / "results/disk_environment/03_system_fair/agnews/csv"
    pub = pd.read_csv(base / "formal_test_rows.csv")
    pub = pub[pub["method"].isin(["Ours-Disk", "OG-LVQ-DiskPort", "Glass-NSG-DiskPort"])].copy()
    fix_agg = (
        ROOT
        / "results/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/"
        "05C_disk_system_fair/agnews/aggregate/formal_test_rows.csv"
    )
    sym = pd.read_csv(fix_agg)
    sym = sym[sym["method"].eq("SymphonyQG-DiskPort")].copy()
    df = pd.concat([pub, sym], ignore_index=True)
    df["recall"] = pd.to_numeric(df["recall"], errors="coerce")
    df["qps"] = pd.to_numeric(df["qps"], errors="coerce")
    df = df[df["recall"].notna() & df["qps"].notna() & (df["qps"] > 0)]
    df["method_display"] = df["method"].map(DISPLAY_METHOD).fillna(df["method"])
    return df


def pareto(g: pd.DataFrame) -> list[pd.Series]:
    """QPS-maximizing Pareto frontier over Recall@10 (monotone, data-derived)."""
    by_recall: dict[float, tuple[pd.Series, float]] = {}
    for _, row in g.iterrows():
        rec = round(float(row["recall"]), 10)
        val = float(row["qps"])
        cur = by_recall.get(rec)
        if cur is None or val > cur[1]:
            by_recall[rec] = (row, val)
    ordered = sorted(by_recall.values(), key=lambda item: float(item[0]["recall"]), reverse=True)
    frontier: list[pd.Series] = []
    best = -math.inf
    for row, val in ordered:
        if val > best:
            frontier.append(row)
            best = val
    return list(reversed(frontier))


def save_pub(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight")


def main() -> int:
    configure()
    df = read_rows()
    fig, ax = plt.subplots(figsize=(3.55, 3.05), constrained_layout=True)

    frontiers: dict[str, list[pd.Series]] = {}
    all_qps: list[float] = []
    all_recalls: list[float] = []
    for method in ORDER:
        g = df[df["method_display"].eq(method)].sort_values("recall")
        pts = pareto(g)
        if not pts:
            continue
        frontiers[method] = pts
        all_qps.extend(float(p["qps"]) for p in pts)
        all_recalls.extend(float(p["recall"]) for p in pts)

    for method, pts in frontiers.items():
        xs = [float(p["recall"]) for p in pts]
        ys = [float(p["qps"]) for p in pts]
        ours = method == "Ours"
        ax.plot(
            xs,
            ys,
            color=COLORS[method],
            linewidth=2.0 if ours else 1.2,
            marker=MARKERS[method],
            markersize=3.6 if ours else 2.7,
            zorder=5 if ours else 2,
        )

    # Reference line at the paper's primary operating recall.
    ax.axvline(0.95, color="#7A7F87", linestyle=":", linewidth=0.8, zorder=0)
    ax.text(
        0.953,
        10 ** (math.log10(max(all_qps)) * 0.92),
        "Recall@10 = 0.95",
        fontsize=6,
        color="#62666D",
        ha="left",
        va="top",
    )

    # Hero marker at the Ours point closest to Recall@10 = 0.95.
    if "Ours" in frontiers:
        near95 = min(frontiers["Ours"], key=lambda p: abs(float(p["recall"]) - 0.95))
        ax.plot(
            [float(near95["recall"])],
            [float(near95["qps"])],
            marker="*",
            markersize=8,
            color=COLORS["Ours"],
            markeredgecolor="white",
            markeredgewidth=0.7,
            zorder=7,
            clip_on=False,
        )

    # Data-derived axis limits (log-y with one-decade padding).
    xmin = max(0.78, min(all_recalls) - 0.02)
    xmax = min(1.005, max(all_recalls) + 0.006)
    ymin = 10 ** (math.floor(math.log10(min(all_qps))) - 0.2)
    ymax = 10 ** (math.ceil(math.log10(max(all_qps))) + 0.15)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(mpl.ticker.LogLocator(base=10.0))
    ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_xlabel("Recall@10")
    ax.set_ylabel("QPS (log scale)")
    ax.grid(True, axis="y", color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=6.5)

    # Direct end labels with white halo, collision-staggered in display space.
    placed: list[tuple[float, float, float]] = []  # (x_disp, y_disp, half_height)
    for method, pts in frontiers.items():
        last = pts[-1]
        x, y = float(last["recall"]), float(last["qps"])
        disp_x, disp_y = ax.transData.transform((x, y))
        step = 0
        while True:
            dy = step * 11
            collide = False
            for px, py, ph in placed:
                if abs(disp_x - px) < 55 and abs((disp_y + dy) - py) < ph + 7:
                    collide = True
                    break
            if not collide:
                break
            step += 1
        placed.append((disp_x, disp_y + dy, 7.0))
        ax.annotate(
            method,
            xy=(x, y),
            xytext=(4.5, dy),
            textcoords="offset points",
            fontsize=7,
            color=COLORS[method],
            fontweight="bold" if method == "Ours" else "normal",
            ha="left",
            va="center",
            path_effects=[
                path_effects.withStroke(linewidth=2.6, foreground="white"),
            ],
        )

    # Panel descriptor and statistics note.
    ax.text(
        0.01,
        0.985,
        "AGNews \u00b7 05C system \u00b7 32 workers",
        transform=ax.transAxes,
        fontsize=6.5,
        color="#62666D",
        ha="left",
        va="top",
    )
    ax.text(
        0.01,
        0.015,
        "2 GiB DRAM budget \u00b7 800 queries per width \u00b7 1 repeat",
        transform=ax.transAxes,
        fontsize=5.5,
        color="#7A7F87",
        ha="left",
        va="bottom",
    )

    save_pub(fig, ROOT / "docs/analysis/fig05c_recall_qps_agnews_paper")
    plt.close(fig)
    print("wrote docs/analysis/fig05c_recall_qps_agnews_paper.{svg,pdf,png,tiff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
