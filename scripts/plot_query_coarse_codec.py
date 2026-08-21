#!/usr/bin/env python3
"""Plot the query coarse-filter codec comparison (full / b1 / int4 / int8).

Reads the merged benchmark table written by
Ours/experiments/run_query_coarse_codec_bench.sh (default
results/query_coarse_bench_full_fp32_batch/query_coarse_codec_summary.csv) and exports two figures per
dataset pair to paper/figures/:

  - query_coarse_codec_recall_qps          Recall@10-QPS curves (log-QPS)
  - query_coarse_codec_qps_at_recall       QPS at matched recall targets

Exports SVG (editable text), PDF (TrueType) and PNG (300 dpi).
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib_config")

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams.update({
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
})

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "paper" / "figures"

CODEC_ORDER = ["full", "b1", "int4", "int8", "b1main"]
CODEC_LABELS = {
    "full": "Full (fp32 query)",
    "b1": "1-bit sym (popcount)",
    "int4": "INT4 asym",
    "int8": "INT8 asym",
    "b1main": "1-bit q x 4-bit d (main)",
}
CODEC_COLORS = {
    "full": "#1f77b4",
    "b1": "#ff7f0e",
    "int4": "#2ca02c",
    "int8": "#9467bd",
    "b1main": "#d62728",
}
CODEC_MARKERS = {
    "full": "o",
    "b1": "^",
    "int4": "s",
    "int8": "D",
    "b1main": "P",
}
DATASET_LABELS = {
    "smoke03": "Smoke03",
    "agnews": "AGNews",
}
RECALL_TARGETS = (0.90, 0.95, 0.98)


def read_summary(path: Path) -> list[dict[str, str]]:
    with path.open() as f:
        return list(csv.DictReader(f))


def fnum(row: dict[str, str], key: str) -> float:
    return float(row[key])


def qps_envelope(points: list[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
    """Monotone upper envelope: QPS non-increasing as recall rises."""
    pts = sorted(points, key=lambda t: (t[0], -t[1]))
    out: list[tuple[float, float, int]] = []
    best_qps = float("inf")
    for recall, qps, ls in pts:
        if qps < best_qps:
            out.append((recall, qps, ls))
            best_qps = qps
    return out


def codec_curves(
    rows: list[dict[str, str]], dataset: str
) -> dict[str, list[tuple[float, float, int]]]:
    curves: dict[str, list[tuple[float, float, int]]] = {}
    for codec in CODEC_ORDER:
        sub = [r for r in rows if r["dataset"] == dataset and r["codec"] == codec]
        if not sub:
            continue
        pts = [
            (fnum(r, "recall"), fnum(r, "qps"), int(r["search_list_size"]))
            for r in sub
        ]
        curves[codec] = qps_envelope(pts)
    return curves


def interpolate_qps_at_recall(
    pts: list[tuple[float, float, int]], target: float
) -> float | None:
    if not pts:
        return None
    if target < pts[0][0] or target > pts[-1][0]:
        return None
    for (r0, q0, _), (r1, q1, _) in zip(pts, pts[1:]):
        if r0 <= target <= r1:
            if r1 == r0:
                return q0
            return q0 + (q1 - q0) * (target - r0) / (r1 - r0)
    return pts[-1][1]


def save_fig(fig, stem: str, tag: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "pdf", "png"):
        fig.savefig(OUT_DIR / f"{stem}{tag}.{ext}", dpi=300 if ext == "png" else None)
    print(f"wrote {stem}{tag} -> {OUT_DIR}")


def panel_label(ax, label: str) -> None:
    ax.text(
        0.02, 0.98, label, transform=ax.transAxes, fontsize=8,
        fontweight="bold", va="top",
    )


def plot_recall_qps(rows: list[dict[str, str]], datasets: list[str], tag: str) -> None:
    fig, axes = plt.subplots(1, len(datasets), figsize=(7.2, 2.55), sharey=True)
    if len(datasets) == 1:
        axes = [axes]
    for i, (ax, ds) in enumerate(zip(axes, datasets)):
        curves = codec_curves(rows, ds)
        for codec in CODEC_ORDER:
            pts = curves.get(codec)
            if not pts:
                continue
            ax.plot(
                [p[0] for p in pts],
                [p[1] for p in pts],
                color=CODEC_COLORS[codec],
                marker=CODEC_MARKERS[codec],
                markersize=3,
                linewidth=1.1,
                label=CODEC_LABELS[codec],
            )
        ax.axvline(0.95, color="#BFBFBF", linestyle=":", linewidth=0.8, zorder=0)
        ax.set_yscale("log")
        ax.set_title(DATASET_LABELS.get(ds, ds), fontsize=7.5, pad=4)
        ax.set_xlabel("Recall@10", fontsize=7)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        panel_label(ax, "abcdefgh"[i])
    axes[0].set_ylabel("QPS", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.0),
                   ncol=len(handles), fontsize=6.5, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save_fig(fig, "query_coarse_codec_recall_qps", tag)


def plot_qps_at_recall(rows: list[dict[str, str]], datasets: list[str], tag: str) -> None:
    fig, axes = plt.subplots(1, len(datasets), figsize=(7.2, 2.55), sharey=True)
    if len(datasets) == 1:
        axes = [axes]
    width = 0.2
    for i, (ax, ds) in enumerate(zip(axes, datasets)):
        curves = codec_curves(rows, ds)
        targets = [
            t for t in RECALL_TARGETS
            if all(
                pts[0][0] <= t <= pts[-1][0]
                for pts in curves.values() if pts
            )
        ]
        x = list(range(len(targets)))
        for j, codec in enumerate(CODEC_ORDER):
            pts = curves.get(codec)
            if not pts:
                continue
            values = [interpolate_qps_at_recall(pts, t) for t in targets]
            ax.bar(
                [xi + (j - 1.5) * width for xi in x],
                [v if v is not None else 0.0 for v in values],
                width=width,
                color=CODEC_COLORS[codec],
                label=CODEC_LABELS[codec],
            )
        ax.set_xticks(x)
        ax.set_xticklabels([f"R@{t:.2f}" for t in targets], fontsize=6.5)
        ax.set_title(DATASET_LABELS.get(ds, ds), fontsize=7.5, pad=4)
        ax.set_ylabel("QPS", fontsize=7)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        panel_label(ax, "ab"[i])
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.0),
                   ncol=len(handles), fontsize=6.5, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save_fig(fig, "query_coarse_codec_qps_at_recall", tag)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--summary",
        default=str(
            ROOT
            / "results"
            / "query_coarse_bench_full_fp32_batch"
            / "query_coarse_codec_summary.csv"
        ),
    )
    ap.add_argument("--datasets", nargs="+", default=["smoke03", "agnews"])
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    rows = read_summary(Path(args.summary))
    datasets = [ds for ds in args.datasets if any(r["dataset"] == ds for r in rows)]
    if not datasets:
        print(f"no data for datasets {args.datasets} in {args.summary}")
        return 1
    plot_recall_qps(rows, datasets, args.tag)
    plot_qps_at_recall(rows, datasets, args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
