#!/usr/bin/env python3
"""VLDB 2027 draft figures for the ExRaBitQ experiments.

Reads the canonical per-suite CSVs under results/<dataset>/csv/ and the
codebook-size (K) ablation summary, and renders eleven publication figures as a
3-panel-per-dataset quantitative grid (DBpedia-1M / GIST-1M / AGNews):

  fig01_quantizer_fair_recall_qps        -- 01 QPS at matched recall bars
  fig02_quantizer_error                  -- 01 mean relative distance error
  fig03_quantizer_error_qps              -- 01 error-throughput Pareto
  fig04_diskann_fair_recall_qps          -- 02 shared-graph payload curves
  fig05_endtoend_recall_qps              -- 03 end-to-end Recall@10-QPS (M=64)
  fig06_endtoend_recall_index            -- 03 recall ceiling + index size
  fig07_kmeans_ablation_recall_qps       -- K ablation R@10/QPS vs K
  fig08_kmeans_ablation_error_train      -- K ablation mean error + train time
  fig10_build_time_02_03                 -- 02/03 construction time

Exports SVG (editable text), PDF (editable TrueType), PNG (300 dpi) and TIFF
(600 dpi) under paper/figures/.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib_config")

import matplotlib

matplotlib.use("Agg")

# MANDATORY font + SVG rules (editable text in SVG output)
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.colors import to_rgba

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams.update({
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 7,
    "figure.facecolor": "#FCFCFD",
    "axes.facecolor": "#FCFCFD",
    "savefig.facecolor": "#FCFCFD",
    "text.color": "#202124",
    "axes.labelcolor": "#202124",
    "axes.edgecolor": "#7A7F87",
    "xtick.color": "#62666D",
    "ytick.color": "#62666D",
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
})

ROOT = Path(__file__).resolve().parents[1]
# Read experiment results from an alternative root (e.g. results/03_system_fair) by
# setting PAPER_RESULTS_ROOT=/abs/path; defaults to <repo>/results.
RESULTS_ROOT = Path(os.environ.get("PAPER_RESULTS_ROOT", ROOT / "results"))
OUT_DIR = ROOT / "paper" / "figures"

# Optional override for archived experiment data. The default is repository
# local so figure generation never silently reads a sibling checkout.
QH_DIR = Path(os.environ.get("QH_DIR", RESULTS_ROOT))

DATASETS = [
    ("DBpedia-1M", "dbpedia"),
    ("GIST-1M", "gist"),
    ("AGNews", "agnews"),
]

# Unified method families, matching the compact SVG benchmark report. Ours is
# the magenta hero series; competitors use restrained, distinguishable colors.
INK = "#202124"
MUTED = "#62666D"
GRID = "#DDE1E6"
AXIS = "#7A7F87"
BG = "#FCFCFD"

COLORS = {
    # The formal method now uses the INT8 query codec, so it inherits the
    # report's magenta hero color throughout the paper.
    "Ours": "#C2417A",
    "PQ": "#2563EB",
    "SQ": "#7A7F87",
    "SAQ": "#D97706",
    "SymphonyQG": "#0F766E",
    "OG-LVQ": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
}

MARKERS = {
    "Ours": "o",
    "PQ": "s",
    "SQ": "^",
    "SAQ": "D",
    "SymphonyQG": "x",
    "OG-LVQ": "+",
    "Glass-NSG": "v",
}

LINESTYLES = {
    "Ours": "-",
    "PQ": "--",
    "SQ": ":",
    "SAQ": "-.",
    "SymphonyQG": "--",
    "OG-LVQ": "-.",
    "Glass-NSG": ":",
}

Q01_METHODS = [
    ("Ours", "Ours (ExRaBitQ-4bit, K=1)", "Ours_RaBitQ_K1"),
    ("PQ", "PQ-4bit", "PQ_4bit"),
    ("SQ", "SQ-4bit", "SQ_4bit"),
    ("SAQ", "SAQ (B=4)", "SAQ_B4"),
]

Q03_METHODS = [
    ("Ours", "Ours (DB1 × INT8 query)"),
    ("Glass-NSG", "Glass-NSG"),
    ("OG-LVQ", "OG-LVQ"),
    ("SymphonyQG", "SymphonyQG"),
]

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def median_aggregate(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse duplicate (method, search_param_value) rows to one median row.

    The per-suite raw CSVs currently contain every ef point twice (two
    "aggregate" measurements of the same run with identical recall but slightly
    different QPS/latency). Plotting both copies makes the curves zigzag, so we
    aggregate the numeric fields by median and keep the first row for the rest.
    """
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for r in rows:
        groups.setdefault((r.get("method", ""), r.get("search_param_value", "")), []).append(r)
    out: list[dict[str, str]] = []
    for key, rs in groups.items():
        if len(rs) == 1 or key[1] == "":
            out.append(rs[0])
            continue
        base = dict(rs[0])
        for col in base:
            vals: list[float] = []
            for r in rs:
                try:
                    vals.append(float(r[col]))
                except (TypeError, ValueError):
                    break
            else:
                if vals:
                    base[col] = f"{statistics.median(vals):.9g}"
        base["n_repeats"] = str(len(rs))
        out.append(base)
    return out


def fnum(row: dict[str, str], key: str) -> float:
    return float(row[key])


def fnum_opt(row: dict[str, str], key: str) -> float:
    """Like fnum but tolerates a missing/empty column (old CSVs)."""
    try:
        return float(row[key])
    except (KeyError, ValueError):
        return 0.0


def hero_style(key: str) -> dict:
    """Hero series get the heaviest, fully saturated rendering."""
    return {
        "linewidth": 2.0 if key == "Ours" else 0.9,
        "markersize": 3.4 if key == "Ours" else 2.4,
        "alpha": 1.0 if key == "Ours" else 0.8,
        "zorder": 5 if key == "Ours" else 2,
        "linestyle": LINESTYLES.get(key, "-"),
    }


def clean_qps_envelope(points: list[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
    """Return measured points unchanged; line order is already efSearch.

    Earlier drafts replaced timing spikes and forced monotonic QPS. Formal
    figures must preserve every observation, even when a single run is noisy.
    """
    return list(points)


def _ef_of(r: dict[str, str]) -> int:
    """Search-eff from either a 'search_param' ('ef=10') or a numeric value."""
    sp = r.get("search_param", "")
    if "=" in sp:
        return int(sp.split("=")[1])
    return int(r["search_param_value"])


def star_marker(ax, x: float, y: float, color: str) -> None:
    ax.plot([x], [y], marker="*", ms=8, color=color, mec="white", mew=0.7,
            zorder=6, clip_on=False)


def representative_marker_indices(n: int, target: int = 9) -> list[int]:
    """Evenly distribute visible markers while retaining the full measured line.

    Dense sweeps remain fully represented by the line.  Showing a marker at
    every setting, however, turns the high-recall saturation region into an
    unreadable solid band at final paper size.
    """
    if n <= target:
        return list(range(n))
    return sorted({int(round(i * (n - 1) / (target - 1))) for i in range(target)})


def format_recall(value: float) -> str:
    """Avoid rounding a measured sub-unity recall to a displayed 1.000."""
    return f"{value:.4f}" if value >= 0.995 else f"{value:.3f}"


def plain_log_formatter():
    """Log-axis tick labels as plain digits (avoids small mathtext superscripts)."""
    return mticker.FuncFormatter(lambda x, _pos: f"{x:g}")


def style_log_y(ax) -> None:
    """Log y-axis with 1/2/5 x 10^k ticks confined to the visible range."""
    ax.set_yscale("log")
    ymin, ymax = ax.get_ylim()
    if ymin <= 0 or ymax <= 0:
        raise ValueError(f"log-y range must be positive, got {(ymin, ymax)}")
    ticks: list[float] = []
    for mantissas in ((1.0, 2.0, 5.0), (1.0, 5.0), (1.0,)):
        cands: list[float] = []
        for k in range(math.floor(math.log10(ymin)) - 1, math.ceil(math.log10(ymax)) + 1):
            for m in mantissas:
                v = m * (10.0**k)
                if ymin <= v <= ymax:
                    cands.append(v)
        if len(cands) <= 6:
            ticks = cands
            break
    ax.set_yticks(ticks)
    ax.yaxis.set_major_formatter(plain_log_formatter())
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())


def panel_label(ax, label: str) -> None:
    ax.text(
        -0.14,
        1.06,
        label,
        transform=ax.transAxes,
        fontsize=8,
        fontweight="bold",
        va="bottom",
        ha="right",
    )


def save_pub(fig, stem: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_DIR / f"{stem}.svg", bbox_inches="tight")
    fig.savefig(OUT_DIR / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(OUT_DIR / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT_DIR / f"{stem}.tiff", dpi=600, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {stem}.{{svg,pdf,png,tiff}}")


def add_figure_legend(fig, handles, labels, ncol: int) -> None:
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=ncol,
        fontsize=6.5,
        frameon=False,
        handlelength=2.4,
        columnspacing=1.4,
        bbox_to_anchor=(0.5, -0.02),
    )


# --------------------------------------------------------------------------
# Figure 1: 01 quantizer fairness -- fixed-candidate Recall@10 vs QPS
# --------------------------------------------------------------------------
def _interp_log_qps(rows: list[dict[str, str]], target: float) -> float | None:
    """Log-linear interpolation of QPS at a target recall from a rerank sweep."""
    pts = sorted((float(r["recall"]), float(r["qps"])) for r in rows)
    if target <= pts[0][0]:
        return pts[0][1]
    if target >= pts[-1][0]:
        return None
    for i in range(len(pts) - 1):
        r0, q0 = pts[i]
        r1, q1 = pts[i + 1]
        if r0 <= target <= r1:
            return math.exp(math.log(q0) + (math.log(q1) - math.log(q0)) * (target - r0) / (r1 - r0))
    return None


def fig01_quantizer_fair() -> None:
    """QPS at a matched recall target (fair, equal-recall comparison)."""
    targets = {"dbpedia": 0.95, "gist": 0.95, "agnews": 0.95}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.6), sharey=True)
    for i, (ax, (label, ds)) in enumerate(zip(axes, DATASETS)):
        tgt = targets[ds]
        qps: dict[str, float] = {}
        mx: dict[str, float] = {}
        for key, disp, file_key in Q01_METHODS:
            path = RESULTS_ROOT / "01_quantizer_fair" / ds / "csv" / f"{file_key}_recall_qps.csv"
            if not path.exists():
                continue
            rows = median_aggregate(
                [r for r in read_csv(path) if int(r["search_param_value"]) >= 16]
            )
            q = _interp_log_qps(rows, tgt)
            if q is not None:
                qps[key] = q
            mx[key] = max(fnum(r, "recall") for r in rows)
        methods = [m for m, _, _ in Q01_METHODS if m in qps or m in mx]

        for key, disp, file_key in Q01_METHODS:
            if key not in mx:
                continue
            if key in qps:
                ax.bar(
                    [key], [qps[key]],
                    color=to_rgba(COLORS[key], 1.0 if key == "Ours" else 0.55),
                    width=0.62, zorder=3,
                    label=disp if key != "SQ" else f"{disp} (max R@10 < target)",
                )
            else:
                ax.bar(
                    [key], [80.0],
                    color=to_rgba(COLORS[key], 0.35),
                    width=0.62, hatch="//", zorder=2,
                    label=f"{disp} (max R@10 < target)",
                )
                ax.text(methods.index(key), 90.0, f"max {mx[key]:.3f}",
                        ha="center", va="bottom", fontsize=5.5, color="#555555")
        if "Ours" in qps:
            ax.text(methods.index("Ours"), qps["Ours"] * 1.15, f"{qps['Ours']:,.0f}",
                    ha="center", va="bottom", fontsize=6.5, color=COLORS["Ours"])
            for other in ("PQ", "SAQ"):
                if other in qps and qps[other] > 0:
                    ax.text(methods.index(other), qps[other] * 1.2, f"{qps[other]:,.0f}",
                            ha="center", va="bottom", fontsize=6)
        ax.set_title(f"{label} · QPS @ R@10={tgt:g}", fontsize=7.5, pad=4)
        style_log_y(ax)
        ax.set_ylim(60, 50000)
        ax.set_xticks(range(len(methods)))
        ax.set_xticklabels(methods, fontsize=6)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
    axes[0].set_ylabel("QPS (matched recall)", fontsize=7)
    for ax in axes:
        ax.set_xlabel("4-bit encoder", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    save_pub(fig, "fig01_quantizer_fair_recall_qps")


# --------------------------------------------------------------------------
# Figure 2: 01 quantization distance error (mean relative error)
# --------------------------------------------------------------------------
def fig02_quantizer_error() -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), sharey=True)
    x = np.arange(len(Q01_METHODS))
    bar_width = 0.36
    for i, (ax, (label, ds)) in enumerate(zip(axes, DATASETS)):
        l2 = np.full(len(Q01_METHODS), np.nan)
        ip = np.full(len(Q01_METHODS), np.nan)
        for j, (_, _, file_key) in enumerate(Q01_METHODS):
            path = RESULTS_ROOT / "01_quantizer_fair" / ds / "csv" / f"{file_key}_accuracy.csv"
            if not path.exists():
                continue
            r = read_csv(path)[0]
            l2[j] = fnum(r, "mean_relative_error")
            ip[j] = fnum_opt(r, "mean_ip_relative_error")
        for j, (key, _, _) in enumerate(Q01_METHODS):
            color = to_rgba(COLORS[key], 1.0 if key == "Ours" else 0.55)
            has_ip = not np.isnan(ip[j]) and ip[j] > 0
            labels_are_close = has_ip and 0.75 <= l2[j] / ip[j] <= 1.33
            if not np.isnan(l2[j]):
                ax.bar(x[j] - bar_width / 2, l2[j], bar_width, color=color, zorder=3)
                ax.text(
                        x[j] - bar_width / 2,
                        l2[j] * (1.9 if labels_are_close else 1.18),
                        f"{l2[j]:.4f}",
                        ha="center", va="bottom",
                        fontsize=5, color=COLORS[key])
            if has_ip:
                ax.bar(x[j] + bar_width / 2, ip[j], bar_width, color=color,
                       hatch="//", edgecolor="white", linewidth=0.4, zorder=3)
                ax.text(x[j] + bar_width / 2, ip[j] * 1.18, f"{ip[j]:.4f}",
                        ha="center", va="bottom", fontsize=5, color=COLORS[key])
        ax.set_title(label, fontsize=7.5, pad=4)
        style_log_y(ax)
        ax.set_ylim(5e-4, 0.6)
        ax.set_xticks(x)
        ax.set_xticklabels([m for m, _, _ in Q01_METHODS], fontsize=6)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
    axes[0].set_ylabel("Mean relative error (L2 / IP)", fontsize=7)
    for ax in axes:
        ax.set_xlabel("4-bit encoder", fontsize=7)
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor="0.65", edgecolor="white"),
        plt.Rectangle((0, 0), 1, 1, facecolor="0.65", edgecolor="white", hatch="//"),
    ]
    add_figure_legend(fig, handles, ["L2 distance error", "Inner-product error"], ncol=2)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    save_pub(fig, "fig02_quantizer_error")


# --------------------------------------------------------------------------
# Figure 3: 01 error-throughput Pareto (QPS @ r=1000 vs mean relative error)
# --------------------------------------------------------------------------
def fig03_quantizer_error_qps() -> None:
    fig, axes = plt.subplots(2, 3, figsize=(7.2, 5.0), sharex=True)
    for i, (label, ds) in enumerate(DATASETS):
        qps_map: dict[str, float] = {}
        l2_map: dict[str, float] = {}
        ip_map: dict[str, float] = {}
        for key, _, file_key in Q01_METHODS:
            acc_path = RESULTS_ROOT / "01_quantizer_fair" / ds / "csv" / f"{file_key}_accuracy.csv"
            rq_path = RESULTS_ROOT / "01_quantizer_fair" / ds / "csv" / f"{file_key}_recall_qps.csv"
            if not acc_path.exists() or not rq_path.exists():
                continue
            acc = read_csv(acc_path)[0]
            rows = median_aggregate(
                [r for r in read_csv(rq_path) if int(r["search_param_value"]) == 1000]
            )
            if not rows:
                continue
            qps_map[key] = fnum(rows[0], "qps")
            l2_map[key] = fnum(acc, "mean_relative_error")
            ip_map[key] = fnum_opt(acc, "mean_ip_relative_error")
        for row_idx, (err_map, disp_label) in enumerate(((l2_map, "L2"), (ip_map, "IP"))):
            ax = axes[row_idx, i]
            for key, disp, _ in Q01_METHODS:
                if key not in err_map or err_map[key] <= 0:
                    continue
                qps = qps_map[key]
                err = err_map[key]
                if key == "Ours":
                    ax.scatter([qps], [err], s=90, marker="*", color=COLORS["Ours"],
                               edgecolor="white", linewidth=0.8, zorder=6,
                               label=disp if row_idx == 0 else None)
                    ax.annotate(disp, (qps, err), textcoords="offset points",
                                xytext=(8, 3), fontsize=6, color=COLORS["Ours"])
                else:
                    ax.scatter([qps], [err], s=40, marker=MARKERS[key],
                               color=to_rgba(COLORS[key], 0.85), zorder=3,
                               label=disp if row_idx == 0 else None)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xlim(90, 3000)
            # Leave room below the best ~1e-3 observations so markers are not
            # clipped by the bottom axis.
            ax.set_ylim(7e-4, 0.6)
            style_log_y(ax)
            ax.set_xticks([200, 500, 1000, 2000])
            ax.xaxis.set_major_formatter(plain_log_formatter())
            ax.xaxis.set_minor_formatter(mticker.NullFormatter())
            ax.tick_params(labelsize=6.5)
            ax.grid(axis="both", color=GRID, lw=0.5, zorder=0)
            if row_idx == 0:
                ax.set_title(label, fontsize=7.5, pad=4)
            panel_label(ax, chr(ord("a") + row_idx * 3 + i))
            if i == 0:
                ax.set_ylabel(f"Mean {disp_label} relative error", fontsize=7)
    for ax in axes[1]:
        ax.set_xlabel("QPS @ rerank=1000", fontsize=7)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save_pub(fig, "fig03_quantizer_error_qps")


# --------------------------------------------------------------------------
# Figure 4: 02 per-method quantized graphs -- Recall@10-QPS on each method's
# own graph (R=64/L=400), from the round2 diskann_fair_raw.csv.
# --------------------------------------------------------------------------
def fig04_diskann_fair_recall_qps() -> None:
    xlims = {"dbpedia": (0.80, 1.0), "gist": (0.65, 1.0), "agnews": (0.88, 1.0)}
    methods = [
        ("Ours", "Ours (DB1 × INT8 query)"),
        ("PQ", "PQ-4bit"),
        ("SQ", "SQ-4bit"),
        ("SAQ", "SAQ (B=4)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), sharey=True)
    for i, (ax, (label, ds)) in enumerate(zip(axes, DATASETS)):
        rows = median_aggregate(
            read_csv(RESULTS_ROOT / "02_diskann_fair" / ds / "csv" / "diskann_fair_raw.csv")
        )
        for key, disp in methods:
            sub = [r for r in rows if r["method"] == key and r.get("status") == "done"]
            if key == "Ours":
                sub = [
                    r for r in sub
                    if r.get("query_coarse_codec", "").strip('"').lower() == "int8"
                ]
            if not sub:
                continue
            pts = clean_qps_envelope(sorted(
                [(fnum(r, "recall"), fnum(r, "qps"), int(r["search_param_value"]))
                 for r in sub],
                key=lambda t: t[2],
            ))
            ax.plot(
                [p[0] for p in pts],
                [p[1] for p in pts],
                color=COLORS[key], marker=MARKERS[key], label=disp,
                **hero_style(key),
            )
            if key == "Ours" and pts:
                p95 = min(pts, key=lambda p: abs(p[0] - 0.95))
                star_marker(ax, p95[0], p95[1], COLORS["Ours"])
        ax.axvline(0.95, color=AXIS, linestyle=":", linewidth=0.8, zorder=0)
        ax.set_title(label, fontsize=7.5, pad=4)
        ax.set_xlim(*xlims[ds])
        style_log_y(ax)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
    axes[0].set_ylabel("QPS", fontsize=7)
    for ax in axes:
        ax.set_xlabel("Recall@10 (own quantized graph, R=64)", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    save_pub(fig, "fig04_diskann_fair_recall_qps")


# --------------------------------------------------------------------------
# Figure 5: 03 end-to-end Recall@10-QPS over the full recall range (Ours M=64)
# --------------------------------------------------------------------------
def _load_q03(ds: str) -> list[dict[str, str]]:
    return read_csv(RESULTS_ROOT / "03_system_fair" / ds / "csv" / "system_fair_median.csv")


def _formal_ours_point(ds: str, search_list_size: int) -> dict[str, str]:
    """Load one measured formal INT8 Ours point from experiment 02."""
    rows = read_csv(
        RESULTS_ROOT / "02_diskann_fair" / ds / "csv" / "diskann_fair_raw.csv"
    )
    matches = [
        row for row in rows
        if row.get("method") == "Ours"
        and row.get("query_coarse_codec", "").strip('"').lower() == "int8"
        and int(row.get("search_param_value", 0) or 0) == search_list_size
    ]
    if not matches:
        raise RuntimeError(
            f"missing formal INT8 Ours point: dataset={ds}, L={search_list_size}"
        )
    return matches[-1]


def _opt_ours_rows(ds: str) -> list[dict[str, str]] | None:
    """Final optimized Ours rows (M64 + cap256 + back-stop2 + refine1 + sidecar)."""
    # Per-dataset canonical run dirs (values match the Feishu doc, 2026-08-19):
    #   agnews  -> query_es/agnews_final_refine1_sidecar  (build 81.7 s, R@10=0.9942)
    #   gist    -> build_opt/gist_opt_refine1             (build 100.9 s, R@10=0.9855)
    #   dbpedia -> query_es/dbpedia_final_refine1_sidecar (build 217.8 s, R@10=0.9935)
    run_rel = {
        "agnews": "results/query_es/agnews_final_refine1_sidecar/agnews/csv/02_diskann_fair/diskann_fair_raw.csv",
        "gist": "results/build_opt/gist_opt_refine1/gist/csv/02_diskann_fair/diskann_fair_raw.csv",
        "dbpedia": "results/query_es/dbpedia_final_refine1_sidecar/dbpedia/csv/02_diskann_fair/diskann_fair_raw.csv",
    }
    for base in (QH_DIR, ROOT / "results"):
        path = base / run_rel.get(
            ds, f"results/query_es/{ds}_final_refine1_sidecar/{ds}/csv/02_diskann_fair/diskann_fair_raw.csv"
        )
        if path.exists():
            rows = [r for r in read_csv(path) if r.get("method") == "Ours"]
            return rows or None
    # Legacy repo-local layout (kept for backward compatibility).
    legacy = (
        ROOT / "results" / "query_es" / f"{ds}_final_refine1_sidecar"
        / "02_diskann_fair" / ds / "csv" / "diskann_fair_raw.csv"
    )
    if legacy.exists():
        rows = [r for r in read_csv(legacy) if r.get("method") == "Ours"]
        return rows or None
    return None


def _opt_ours_build_time_ms(ds: str) -> float | None:
    rows = _opt_ours_rows(ds)
    if not rows:
        return None
    r = rows[0]
    if r.get("graph_build_time_ms"):
        return fnum(r, "graph_build_time_ms")
    return fnum(r, "build_time_ms")


def fig05_endtoend_qps() -> None:
    """End-to-end Pareto curves in three full-width dataset panels.

    Every measured search setting remains in each line.  Only the visible
    markers are thinned, which prevents dense high-recall sweeps from becoming
    a solid vertical band.  Direct ceiling labels make saturation explicit
    instead of leaving an apparently detached endpoint near Recall=1.
    """
    xlims = {"dbpedia": (0.75, 1.0), "gist": (0.75, 1.0), "agnews": (0.85, 1.0)}
    annotation_offsets = {
        "dbpedia": {
            "Ours": (-5, 7), "Glass-NSG": (5, 5),
            "OG-LVQ": (5, 8), "SymphonyQG": (5, 5),
        },
        "gist": {
            "Ours": (-5, 7), "Glass-NSG": (5, 5),
            "OG-LVQ": (5, 8), "SymphonyQG": (-5, 7),
        },
        "agnews": {
            "Ours": (-5, 8), "Glass-NSG": (5, 7),
            "OG-LVQ": (5, 10), "SymphonyQG": (-5, 16),
        },
    }
    short_names = {
        "Ours": "Ours", "Glass-NSG": "Glass",
        "OG-LVQ": "OG-LVQ", "SymphonyQG": "SymphonyQG",
    }
    fig, axes = plt.subplots(3, 1, figsize=(7.2, 5.9), sharex=False)
    for i, (ax, (label, ds)) in enumerate(zip(axes, DATASETS)):
        rows = _load_q03(ds)
        for key, disp in Q03_METHODS:
            sub = [r for r in rows if r["method"] == key]
            if not sub:
                continue
            pts = clean_qps_envelope(sorted(
                [(fnum(r, "recall"), fnum(r, "qps"), _ef_of(r)) for r in sub],
                key=lambda t: t[2],
            ))
            ax.plot(
                [p[0] for p in pts],
                [p[1] for p in pts],
                color=COLORS[key],
                marker=MARKERS[key],
                markevery=representative_marker_indices(len(pts)),
                label=disp,
                **hero_style(key),
            )
            if pts:
                ceiling = max(pts, key=lambda p: p[0])
                endpoint_style = {
                    "s": 31 if key == "Ours" else 22,
                    "marker": MARKERS[key], "color": COLORS[key],
                    "linewidths": 0.65, "zorder": 7, "clip_on": False,
                }
                if MARKERS[key] not in ("x", "+"):
                    endpoint_style["edgecolors"] = "white"
                ax.scatter([ceiling[0]], [ceiling[1]], **endpoint_style)
                dx, dy = annotation_offsets[ds][key]
                ha = "right" if dx < 0 else "left"
                ax.annotate(
                    f"{short_names[key]}  {format_recall(ceiling[0])}",
                    xy=(ceiling[0], ceiling[1]), xytext=(dx, dy),
                    textcoords="offset points", ha=ha, va="center",
                    fontsize=6.1, color=COLORS[key], clip_on=False,
                )
        ax.axvline(0.95, color=AXIS, linestyle=":", linewidth=0.8, zorder=0)
        ax.text(
            0.01, 0.94, label, transform=ax.transAxes,
            ha="left", va="top", fontsize=7.5, fontweight="bold",
        )
        ax.set_xlim(*xlims[ds])
        style_log_y(ax)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
        ax.set_ylabel("QPS", fontsize=7)
    for ax in axes:
        ax.set_xlabel("Recall@10 (end-to-end)", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="lower center", ncol=4, fontsize=6.5,
        frameon=False, handlelength=2.6, columnspacing=1.35,
        bbox_to_anchor=(0.5, 0.018),
    )
    fig.text(
        0.5, 0.068,
        "High-recall view. Curves use all measured settings "
        "(Ours: L_search=10–460; baselines: ef/W=10–580); markers are representative. "
        "Endpoint labels show maximum measured Recall@10.",
        ha="center", va="bottom", fontsize=5.7, color=MUTED,
    )
    fig.tight_layout(rect=(0.02, 0.115, 0.99, 1), h_pad=1.05)
    save_pub(fig, "fig05_endtoend_recall_qps")


# --------------------------------------------------------------------------
# Figure 6: 03 recall ceiling and index footprint
# --------------------------------------------------------------------------
def _fmt_size(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def fig06_endtoend_recall_index() -> None:
    """Recall ceilings and index footprints as aligned dot plots.

    Recall bars with a truncated baseline visually exaggerate small ceiling
    differences.  Position-only dot plots show those values without implying
    that the bars start at zero; the index panel remains logarithmic because
    footprints span two orders of magnitude.
    """
    fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.15), sharex="col", sharey="row")
    method_labels = ["Ours", "Glass-NSG", "OG-LVQ", "SymphonyQG"]
    for col, (label, ds) in enumerate(DATASETS):
        rows = _load_q03(ds)
        best = {m: max((r for r in rows if r["method"] == m), key=lambda r: fnum(r, "recall"))
                for m, _ in Q03_METHODS if any(r["method"] == m for r in rows)}
        methods = [m for m, _ in Q03_METHODS if m in best]
        xpos = np.arange(len(methods))
        colors = [COLORS[m] for m in methods]

        # (a-c) maximum achievable Recall@10
        ax = axes[0, col]
        recs = [fnum(best[m], "recall") for m in methods]
        for x, m, v, color in zip(xpos, methods, recs, colors):
            ax.scatter(
                x, v, s=42 if m == "Ours" else 30, marker=MARKERS[m],
                color=color, linewidths=1.0, zorder=4,
            )
            ax.text(x, v + 0.007, format_recall(v), ha="center", va="bottom",
                    fontsize=6, color=color)
        ax.axhline(0.95, color=AXIS, linestyle=":", linewidth=0.8, zorder=1)
        ax.set_ylim(0.78, 1.025)
        ax.set_xticks(xpos)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        ax.set_title(label, fontsize=7.5, pad=4)
        panel_label(ax, "abc"[col])

        # (d-f) index size at the maximum-recall operating point
        ax = axes[1, col]
        sizes = [fnum(best[m], "index_size_mb") for m in methods]
        ax.set_ylim(150, 30000)
        style_log_y(ax)
        for x, m, v, color in zip(xpos, methods, sizes, colors):
            ax.scatter(
                x, v, s=42 if m == "Ours" else 30, marker=MARKERS[m],
                color=color, linewidths=1.0, zorder=4,
            )
            ax.annotate(
                _fmt_size(v), xy=(x, v), xytext=(0, 7),
                textcoords="offset points", ha="center", va="bottom",
                fontsize=6, color=color,
            )
        ax.set_xticks(xpos)
        ax.set_xticklabels(method_labels, fontsize=6, rotation=24, ha="right",
                           rotation_mode="anchor")
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "def"[col])

    axes[0, 0].set_ylabel("Maximum Recall@10", fontsize=7)
    axes[1, 0].set_ylabel("Index size at max Recall (MB)", fontsize=7)
    fig.text(
        0.5, 0.012, "Ours uses a 1-bit database coarse code and an INT8 query.",
        ha="center", va="bottom", fontsize=5.7, color=MUTED,
    )
    fig.tight_layout(rect=(0.02, 0.055, 1, 1), h_pad=1.25, w_pad=0.9)
    save_pub(fig, "fig06_endtoend_recall_index")


# --------------------------------------------------------------------------
# Figure 10: build cost -- 02 end-to-end construction + 03 system build time
# --------------------------------------------------------------------------
def _fmt_s(v: float) -> str:
    if v >= 3600:
        return f"{v / 3600:.1f} h"
    if v >= 60:
        return f"{v / 60:.1f} min"
    return f"{v:.0f} s"


def fig10_build_time_02_03() -> None:
    """Construction time under a UNIFORM M=64 / beam=400 protocol.

    Ours/PQ/SQ/SAQ come from experiment 02 (round2, R=64/L=400/refine=0);
    SymphonyQG (R64_EF400_t3) and OG-LVQ (R64_W400) from their R64 build
    records; Glass-NSG uses the R64_L400 robustness rebuild. Every method is
    therefore at max-degree 64 with construction beam 400, so the bars are
    directly comparable.
    """
    order = ["Ours", "SAQ", "SymphonyQG", "OG-LVQ", "Glass-NSG", "PQ", "SQ"]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.7), sharey=True)
    for col, (ax, (label, ds)) in enumerate(zip(axes, DATASETS)):
        bt: dict[str, float] = {}
        raw = read_csv(RESULTS_ROOT / "02_diskann_fair" / ds / "csv" / "diskann_fair_raw.csv")
        for r in raw:
            if r.get("status") != "done":
                continue
            if r["method"] in ("PQ", "SQ", "SAQ", "Ours") and r["method"] not in bt:
                bt[r["method"]] = fnum(r, "build_time_ms") / 1000.0
        opt_build = _opt_ours_build_time_ms(ds)
        if opt_build is not None:
            bt["Ours"] = opt_build / 1000.0

        # Prefer the round2 build records (values match the Feishu doc:
        # SymphonyQG R64 111.9 / 139.4 / 382.9 s), then the repo-local
        # results tree, then the source checkout's records.
        build_records = [
            ("SymphonyQG",
             QH_DIR / "results" / "round2" / ds / "indexes" / "03_system_fair"
             / "SymphonyQG" / "SymphonyQG_R64_EF400_t3_build.json",
             RESULTS_ROOT / "03_system_fair" / ds / "indexes" / "SymphonyQG"
             / "SymphonyQG_R64_EF400_t3_build.json"),
            ("OG-LVQ",
             QH_DIR / "results" / "round2" / ds / "indexes" / "03_system_fair"
             / "OG-LVQ" / "OG-LVQ_LVQ4_R64_W400_build.json",
             RESULTS_ROOT / "03_system_fair" / ds / "indexes" / "OG-LVQ"
             / "OG-LVQ_LVQ4_R64_W400_build.json"),
            ("Glass-NSG",
             QH_DIR / "results" / "glass_l400_robustness" / ds
             / "Glass-NSG_R64_L400_build.json",
             ROOT / "results" / "glass_l400_robustness" / ds
             / "Glass-NSG_R64_L400_build.json"),
        ]
        for key, primary, fallback in build_records:
            path = primary if primary.exists() else fallback
            if path.exists():
                bt[key] = float(json.loads(path.read_text()).get("build_time_ms", 0.0)) / 1000.0

        names = [m for m in order if m in bt]
        vals = [bt[m] for m in names]
        colors = [to_rgba(COLORS[m], 1.0 if m == "Ours" else 0.55) for m in names]
        x = np.arange(len(names))
        ax.bar(x, vals, color=colors, width=0.62, zorder=3)
        style_log_y(ax)
        for xi, v in zip(x, vals):
            ax.text(xi, v * 1.14, _fmt_s(v), ha="center", va="bottom", fontsize=5.5)
        ax.set_xticks(x)
        ax.set_xticklabels(
            names, rotation=35, ha="right", rotation_mode="anchor", fontsize=5.5
        )
        ax.tick_params(axis="x", labelsize=6)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        if col == 0:
            ax.set_ylabel("Construction time (s, log)\nM=64 / beam=400", fontsize=6.5)
        ax.set_title(label, fontsize=7.5, pad=4)
        panel_label(ax, "abc"[col])

    handles, labels = [], []
    for m, disp in [("Ours", "Ours (ExRaBitQ-4bit)"), ("SAQ", "SAQ (B=4)"),
                    ("SymphonyQG", "SymphonyQG"), ("OG-LVQ", "OG-LVQ"),
                    ("Glass-NSG", "Glass-NSG"), ("PQ", "PQ-4bit"),
                    ("SQ", "SQ-4bit")]:
        handles.append(plt.Line2D([], [], color=COLORS[m], marker="s", linestyle="",
                                  markersize=4.5))
        labels.append(disp)
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.16, 1, 1))
    save_pub(fig, "fig10_build_time_02_03")


# --------------------------------------------------------------------------
# Figure 7: K ablation -- R@10 and QPS at r=1000 vs codebook size K
# --------------------------------------------------------------------------
def fig07_kmeans_summary() -> None:
    data = {ds: _k_summary(ds) for _, ds in DATASETS}
    swept = [(label, ds) for label, ds in DATASETS if len(data[ds]) > 1]
    if not swept:
        raise RuntimeError("fig07 requires at least one multi-K sweep")
    fig, axes = plt.subplots(
        len(swept), 2, figsize=(7.2, 2.45 * len(swept)), squeeze=False
    )
    for row, (label, ds) in enumerate(swept):
        rows = data[ds]
        ks = sorted(rows)

        ax = axes[row, 0]
        rec = [rows[k]["recall"] for k in ks]
        ax.plot(ks, rec, color=COLORS["Ours"], linewidth=1.5, marker="o",
                markersize=4.5, zorder=3)
        if 1 in rows:
            ax.plot([1], [rows[1]["recall"]], marker="D", mfc="white",
                    mec=COLORS["Ours"], ms=5.5, mew=1.2, zorder=4)
        ax.set_xscale("log", base=2)
        ax.set_ylim(min(rec) - 0.03, max(rec) + 0.015)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        ax.set_ylabel("R@10 @ rerank=1000", fontsize=7)
        ax.set_title(f"{label} · recall", fontsize=7.5, pad=4)

        ax = axes[row, 1]
        qps = [rows[k]["qps"] for k in ks]
        ax.plot(ks, qps, color=MUTED, linewidth=1.5, marker="s",
                markersize=4.5, zorder=3)
        if 1 in rows:
            ax.plot([1], [rows[1]["qps"]], marker="D", mfc="white",
                    mec=MUTED, ms=5.5, mew=1.2, zorder=4)
        ax.set_xscale("log", base=2)
        ax.set_ylim(min(qps) * 0.85, max(qps) * 1.15)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        ax.set_ylabel("QPS @ rerank=1000", fontsize=7)
        ax.set_title(f"{label} · throughput", fontsize=7.5, pad=4)

        if row == len(swept) - 1:
            axes[row, 0].set_xlabel("Codebook size K", fontsize=7)
            axes[row, 1].set_xlabel("Codebook size K", fontsize=7)
        for col in range(2):
            axes[row, col].set_xticks(ks)
            axes[row, col].set_xticklabels([str(k) for k in ks], fontsize=6.5)

        panel_label(axes[row, 0], chr(ord("a") + 2 * row))
        panel_label(axes[row, 1], chr(ord("b") + 2 * row))
    fig.tight_layout(pad=1.0)
    save_pub(fig, "fig07_kmeans_ablation_recall_qps")


# --------------------------------------------------------------------------
# Figure 8: K ablation -- mean relative error and train time vs K
# --------------------------------------------------------------------------
def _k_summary(ds: str) -> dict[int, dict[str, float]]:
    """K -> {recall, qps, mean_rel, train_s} from summary + consolidated CSV."""
    rows: dict[int, dict[str, float]] = {}
    summary = RESULTS_ROOT / "01_quantizer_fair" / ds / "csv" / "faiss_quantizer_summary.csv"
    if summary.exists():
        for r in read_csv(summary):
            method = r.get("method", "")
            if not method.startswith("Ours_RaBitQ_K"):
                continue
            k = int(method.rsplit("_K", 1)[1])
            rows[k] = {
                "recall": float(r["recall"]),
                "qps": float(r["qps"]),
                "mean_rel": float(r["mean_relative_error"]),
                "train_s": float(r["train_time_ms"]) / 1000.0,
            }
    consolidated = ROOT / "ablation" / ds / "kmeans" / f"kmeans_ablation_{ds}.csv"
    if consolidated.exists():
        for r in read_csv(consolidated):
            k = int(float(r["K"]))
            if k in rows:
                continue
            rows[k] = {
                "recall": float(r["fixed_candidate_recall@10"]),
                "qps": float(r["qps(k=1000)"]),
                "mean_rel": float(r["mean_relative_error"]),
                "train_s": float(r["train_time_ms"]) / 1000.0,
            }
    return rows


def fig08_kmeans_error_train() -> None:
    data = {ds: _k_summary(ds) for _, ds in DATASETS}
    swept = [(label, ds) for label, ds in DATASETS if len(data[ds]) > 1]
    if not swept:
        raise RuntimeError("fig08 requires at least one multi-K sweep")
    fig, axes = plt.subplots(
        len(swept), 2, figsize=(7.2, 2.45 * len(swept)), squeeze=False
    )
    for row, (label, ds) in enumerate(swept):
        rows = data[ds]
        ks = sorted(rows)
        err = [rows[k]["mean_rel"] for k in ks]
        train = [rows[k]["train_s"] for k in ks]

        ax = axes[row, 0]
        ax.plot(ks, err, color="#0F4D92", linewidth=1.5, marker="o", markersize=4.5, zorder=3)
        ax.set_xscale("log", base=2)
        if 1 in rows:
            ax.plot([1], [rows[1]["mean_rel"]], marker="D", mfc="white", mec="#0F4D92",
                    ms=5.5, mew=1.2, zorder=4)
        ax.set_ylim(0, max(err) * 1.25)
        ax.set_xticks(ks)
        ax.set_xticklabels([str(k) for k in ks], fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        ax.set_ylabel("Mean relative error", fontsize=7)
        ax.set_title(f"{label} · quantization error", fontsize=7.5, pad=4)

        ax = axes[row, 1]
        ax.plot(ks, train, color=MUTED, linewidth=1.5, marker="s", markersize=4.5, zorder=3)
        ax.set_xscale("log", base=2)
        if 1 in rows:
            ax.plot([1], [rows[1]["train_s"]], marker="D", mfc="white", mec=MUTED,
                    ms=5.5, mew=1.2, zorder=4)
        ax.set_xticks(ks)
        ax.set_xticklabels([str(k) for k in ks], fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        ax.set_ylabel("Train time (s)", fontsize=7)
        ax.set_title(f"{label} · training cost", fontsize=7.5, pad=4)
        if row == len(swept) - 1:
            axes[row, 0].set_xlabel("Codebook size K", fontsize=7)
            axes[row, 1].set_xlabel("Codebook size K", fontsize=7)
    for row in range(len(swept)):
        panel_label(axes[row, 0], chr(ord("a") + 2 * row))
        panel_label(axes[row, 1], chr(ord("b") + 2 * row))
    fig.tight_layout(pad=1.2)
    save_pub(fig, "fig08_kmeans_ablation_error_train")


# --------------------------------------------------------------------------
# Figure 11: 03 decomposition attribution -- per system, three recall
# ceilings: uniform fp32 search on its own graph (ruler 1), fixed-candidate
# payload recall (ruler 2), and end-to-end recall (ruler 3).
# --------------------------------------------------------------------------
SYSTEMS_DECOMP = ["Ours", "Glass-NSG", "OG-LVQ", "SymphonyQG"]


def fig11_decomposition_attribution() -> None:
    path = RESULTS_ROOT / "decomposition_attribution.csv"
    if not path.exists():
        print(f"[fig11] skip: missing {path}")
        return
    rows = list(csv.DictReader(path.open()))
    if not rows:
        print(f"[fig11] skip: empty {path}")
        return

    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.7), sharey=True)
    measurements = [
        ("graph_fp32_recall", "Ruler1 Graph (fp32)"),
        ("payload_recall", "Ruler2 Payload (fixed cand.)"),
        ("e2e_recall", "Ruler3 End-to-end"),
    ]
    bar_colors = ["#0F4D92", "#8C8C8C", "#C78A2E"]
    x = np.arange(len(SYSTEMS_DECOMP))
    bar_width = 0.26

    for i, (label, ds) in enumerate(DATASETS):
        ax = axes[i]
        ds_rows = {r["system"]: r for r in rows if r["dataset"] == ds}
        for j, (key, disp) in enumerate(measurements):
            vals = []
            for sys_name in SYSTEMS_DECOMP:
                r = ds_rows.get(sys_name, {})
                v = r.get(key, "")
                vals.append(float(v) if v not in ("", "nan") else float("nan"))
            for k, sys_name in enumerate(SYSTEMS_DECOMP):
                if not (0 <= k < len(vals)) or not (0 <= vals[k] <= 1):
                    continue
                pos = x[k] + (j - 1) * bar_width
                color = to_rgba(bar_colors[j], 1.0)
                edge = COLORS["Ours"] if sys_name == "Ours" else "white"
                lw = 1.4 if sys_name == "Ours" else 0.5
                ax.bar(pos, vals[k], bar_width, color=color, edgecolor=edge,
                       linewidth=lw, zorder=3, label=disp if k == 0 and i == 0 else None)
        ax.set_title(label, fontsize=7.5, pad=4)
        ax.set_xticks(x)
        ax.set_xticklabels(
            [m for m in SYSTEMS_DECOMP], fontsize=6, rotation=12,
            ha="right", rotation_mode="anchor",
        )
        ax.set_ylim(0, 1.0)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
    axes[0].set_ylabel("Recall ceiling", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    add_figure_legend(fig, handles, labels, ncol=3)
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    save_pub(fig, "fig11_decomposition_attribution")


# --------------------------------------------------------------------------
# Figure 12: AGNews instrumented per-query cost comparison at ef=100.
# All four systems in one figure, three metrics on the same log scale.
# --------------------------------------------------------------------------
def fig12_agnews_instrumented_ef100() -> None:
    systems = ["Ours", "SymphonyQG", "Glass-NSG", "OG-LVQ"]
    # R64 / M=64 instrumented measurements at ef=100. Ours is loaded from the
    # new formal INT8 run; baseline instrumentation remains from the official
    # system adapters because those systems expose different counter sets.
    #   SymphonyQG  = official source instrumentation, R64: 46.3 expanded
    #                 nodes, 3,011 distances (46.3 exact L2 + 2,965 fast-scan),
    #                 142.6 us, recall 0.994 (R32 was 60/1,969/148).
    #   Glass-NSG   = official source instrumentation, R64_L100: 104.7 visited,
    #                 1,876.6 SQ4U distances, 213.1 us, recall 0.9425
    #                 (R32_L50 was 106/1,431/250).
    #   OG-LVQ      = SVS official binding exposes no counts; latency 938 us.
    ours = _formal_ours_point("agnews", 100)
    metrics = [
        ("visited", "Visited / query", [fnum(ours, "visited_nodes"), 46.3, 104.7, None]),
        ("distance", "Distance comps / query", [fnum(ours, "distance_calls"), 3011, 1876.6, None]),
        ("latency", "Latency (µs) / query", [fnum(ours, "latency_mean_us"), 142.6, 213.1, 938]),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.6), sharey=True)
    for col, (ax, (_, title, vals)) in enumerate(zip(axes, metrics)):
        for i, (sys_name, v) in enumerate(zip(systems, vals)):
            if v is None:
                ax.text(
                    i, 12, "N/A", ha="center", va="bottom", fontsize=6,
                    color=MUTED,
                )
                continue
            color = to_rgba(COLORS.get(sys_name, "#888888"),
                            1.0 if sys_name == "Ours" else 0.55)
            edge = COLORS["Ours"] if sys_name == "Ours" else "white"
            ax.bar([i], [v], width=0.62, color=color, edgecolor=edge,
                   linewidth=1.3 if sys_name == "Ours" else 0.5, zorder=3)
            label = f"{v:,.1f}".rstrip("0").rstrip(".")
            ax.text(i, v * 1.18, label, ha="center", va="bottom",
                    fontsize=6, color=COLORS.get(sys_name, "#333333"))
        ax.set_ylim(10, 20000)
        style_log_y(ax)
        ax.set_xticks(range(len(systems)))
        ax.set_xticklabels(
            systems, rotation=12, fontsize=6, ha="right", rotation_mode="anchor"
        )
        ax.set_title(title, fontsize=7.5, pad=4)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        panel_label(ax, "abc"[col])
    axes[0].set_ylabel("per query (log scale)", fontsize=7)
    fig.tight_layout(pad=0.8)
    save_pub(fig, "fig12_agnews_instrumented_ef100")


def main() -> int:
    fig01_quantizer_fair()
    fig02_quantizer_error()
    fig03_quantizer_error_qps()
    fig04_diskann_fair_recall_qps()
    fig05_endtoend_qps()
    fig06_endtoend_recall_index()
    fig07_kmeans_summary()
    fig08_kmeans_error_train()
    fig10_build_time_02_03()
    fig11_decomposition_attribution()
    fig12_agnews_instrumented_ef100()
    return 0


if __name__ == "__main__":
    sys.exit(main())
