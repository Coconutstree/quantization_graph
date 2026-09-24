#!/usr/bin/env python3
"""Nature-style single-panel GIST 03 Recall-QPS figure at the supplied reference scale."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gist-paper-matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results/03_disk_system/gist/tables/recall_qps_summary.csv"
OUT = ROOT / "results/03_disk_system/gist/figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 8,
    "axes.labelsize": 8.5,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.fontsize": 8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 1.0,
})

rows = list(csv.DictReader(DATA.open()))
specs = [
    ("DiskANN-PQ-Disk", "DiskANN-PQ", "#6B6B6B", "o", ":", 2.5),
    ("Glass-NSG-DiskPort", "Glass-NSG", "#C86A2D", "s", "-", 2.6),
    ("Ours-Disk", "Ours", "#3A9688", "D", "-", 2.8),
    ("SymphonyQG-DiskPort", "SymphonyQG", "#1976B8", "^", "-", 2.6),
]

fig, ax = plt.subplots(figsize=(7.2, 4.91), dpi=300)
for method, label, color, marker, linestyle, linewidth in specs:
    curve = sorted((r for r in rows if r["method"] == method), key=lambda r: int(r["search_width"]))
    if len(curve) != 9:
        raise SystemExit(f"expected 9 widths for {method}, found {len(curve)}")
    ax.plot(
        [float(r["recall"]) for r in curve],
        [float(r["qps"]) for r in curve],
        color=color,
        marker=marker,
        linestyle=linestyle,
        linewidth=linewidth * 0.7,
        markersize=4.5,
        markeredgewidth=0.8,
        markeredgecolor=color,
        markerfacecolor=color,
        label=label,
        zorder=3 if method == "Ours-Disk" else 2,
    )

ax.set_xlim(0.62, 1.00)
max_qps = max(float(r["qps"]) for r in rows)
ax.set_ylim(0, max(1600, max_qps * 1.06))
ax.yaxis.set_major_locator(MultipleLocator(500))
ax.xaxis.set_major_locator(MultipleLocator(0.05))
ax.grid(axis="y", color="#D9DDE0", linewidth=1.0, alpha=0.8)
ax.set_xlabel("Recall@10")
ax.set_ylabel("QPS")

fig.suptitle("GIST: Disk Search Comparison", fontsize=15, fontweight="bold", y=0.985)
fig.text(
    0.5,
    0.915,
    "800 test queries per width; 4 GiB budget; beam=4; 32 workers; one reference run",
    ha="center",
    fontsize=9,
)
ax.legend(
    loc="upper center",
    bbox_to_anchor=(0.5, 1.10),
    ncol=2,
    frameon=False,
    handlelength=2.1,
    columnspacing=2.2,
    handletextpad=0.6,
)
fig.text(
    0.5,
    0.018,
    "Measured QPS at nine widths (10–580); no smoothing/interpolation.\n"
    "External parity stopped by user request; Ours/DiskANN replacement rows are diagnostic.",
    ha="center",
    fontsize=6.8,
)
fig.subplots_adjust(left=0.13, right=0.96, bottom=0.16, top=0.70)

stem = OUT / "system_qps_recall"
fig.savefig(stem.with_suffix(".png"), dpi=300)
fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
fig.savefig(stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight")
plt.close(fig)

(OUT / "system_qps_recall.scale.json").write_text(json.dumps({
    "canvas_ratio": 7.2 / 4.91,
    "xlim": [0.62, 1.0],
    "ylim": [0, max(1600, max_qps * 1.06)],
    "reference_scale": "gist_hybrid_final",
    "data_rows": len(rows),
    "formal_ready": False,
}, indent=2) + "\n")
print(stem.with_suffix(".png"))
