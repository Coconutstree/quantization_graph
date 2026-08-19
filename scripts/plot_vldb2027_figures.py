#!/usr/bin/env python3
"""VLDB 2027 draft figures for the ExRaBitQ experiments.

Reads the canonical per-suite CSVs under results/<dataset>/csv/ and the
codebook-size (K) ablation summary, and renders seven publication figures as a
3-panel-per-dataset quantitative grid (DBpedia-1M / GIST-1M / AGNews):

  fig01_quantizer_fair_recall_qps        -- 01 QPS at matched recall bars
  fig02_quantizer_error                  -- 01 mean relative distance error
  fig03_quantizer_error_qps              -- 01 error-throughput Pareto
  fig04_diskann_fair_recall_qps          -- 02 shared-graph payload curves
  fig05_endtoend_recall_qps              -- 03 end-to-end Recall@10-QPS (M=64)
  fig06_endtoend_recall_index            -- 03 recall ceiling + index size
  fig07_kmeans_ablation_recall_qps       -- K ablation R@10/QPS vs K
  fig08_kmeans_ablation_error_train      -- K ablation mean error + train time
  fig09_memory_02_03                     -- 02 payload index + 03 peak RSS
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

# Optional external experiment-data home. The source checkout that owns the
# optimized-Ours query runs (query_es/), the Glass R64/L400 robustness rebuild
# (glass_l400_robustness/) and the round2 build records used by fig10.
# Defaults to the sibling quantized_hnsw checkout when present; otherwise the
# repo-local results/ tree is used (and the affected figures degrade to
# whatever data is available).
_QH_DIR = Path(os.environ.get("QH_DIR", ROOT.parent / "quantized_hnsw"))
QH_DIR = _QH_DIR if _QH_DIR.exists() else RESULTS_ROOT

DATASETS = [
    ("DBpedia-1M", "dbpedia"),
    ("GIST-1M", "gist"),
    ("AGNews", "agnews"),
]

# Unified method families (kept identical across every figure).
# Ours is the hero series: vivid red-orange, heaviest weight, sole saturated
# warm series; every competitor is muted so the hero stays the most salient.
COLORS = {
    "Ours": "#FF4D00",        # hero: vivid red-orange (eye-catching)
    "PQ": "#8C8C8C",          # neutral grey
    "SQ": "#BDBDBD",          # light grey
    "SAQ": "#C78A2E",         # muted amber
    "SymphonyQG": "#A64D45",  # muted brick
    "OG-LVQ": "#7A6FA6",      # muted violet
    "Glass-NSG": "#5B8CA6",   # slate blue
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

Q01_METHODS = [
    ("Ours", "Ours (ExRaBitQ-4bit, K=1)", "Ours_RaBitQ_K1"),
    ("PQ", "PQ-4bit", "PQ_4bit"),
    ("SQ", "SQ-4bit", "SQ_4bit"),
    ("SAQ", "SAQ (B=4)", "SAQ_B4"),
]

Q03_METHODS = [
    ("Ours", "Ours (ExRaBitQ-4bit)"),
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
    }


def star_marker(ax, x: float, y: float, color: str) -> None:
    ax.plot([x], [y], marker="*", ms=8, color=color, mec="white", mew=0.7,
            zorder=6, clip_on=False)


def plain_log_formatter():
    """Log-axis tick labels as plain digits (avoids small mathtext superscripts)."""
    return mticker.FuncFormatter(lambda x, _pos: f"{x:g}")


def style_log_y(ax) -> None:
    """Log y-axis with 1/2/5 x 10^k ticks confined to the visible range."""
    ax.set_yscale("log")
    ymin, ymax = ax.get_ylim()
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
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
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
            if not np.isnan(l2[j]):
                ax.bar(x[j] - bar_width / 2, l2[j], bar_width, color=color, zorder=3)
                ax.text(x[j] - bar_width / 2, l2[j] * 1.18, f"{l2[j]:.4f}",
                        ha="center", va="bottom", fontsize=5, color=COLORS[key])
            if not np.isnan(ip[j]) and ip[j] > 0:
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
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
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
            ax.set_ylim(1e-3, 0.6)
            style_log_y(ax)
            ax.set_xticks([200, 500, 1000, 2000])
            ax.xaxis.set_major_formatter(plain_log_formatter())
            ax.xaxis.set_minor_formatter(mticker.NullFormatter())
            ax.tick_params(labelsize=6.5)
            ax.grid(axis="both", color="#E8E8E8", lw=0.5, zorder=0)
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
        ("Ours", "Ours (ExRaBitQ-4bit)"),
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
            if not sub:
                continue
            sub.sort(key=lambda r: fnum(r, "recall"))
            ax.plot(
                [fnum(r, "recall") for r in sub],
                [fnum(r, "qps") for r in sub],
                color=COLORS[key], marker=MARKERS[key], label=disp,
                **hero_style(key),
            )
            if key == "Ours" and sub:
                p95 = min(sub, key=lambda r: abs(fnum(r, "recall") - 0.95))
                star_marker(ax, fnum(p95, "recall"), fnum(p95, "qps"), COLORS["Ours"])
        ax.axvline(0.95, color="#BFBFBF", linestyle=":", linewidth=0.8, zorder=0)
        ax.set_title(label, fontsize=7.5, pad=4)
        ax.set_xlim(*xlims[ds])
        style_log_y(ax)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
    axes[0].set_ylabel("QPS", fontsize=7)
    for ax in axes:
        ax.set_xlabel("Recall@10 (own quantized graph, R=64)", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    save_pub(fig, "fig04_diskann_fair_recall_qps")


# --------------------------------------------------------------------------
# Figure 4: 03 end-to-end Recall@10-QPS over the full recall range (Ours M=64)
# --------------------------------------------------------------------------
def _load_q03(ds: str) -> list[dict[str, str]]:
    return read_csv(RESULTS_ROOT / "03_system_fair" / ds / "csv" / "system_fair_median.csv")


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
    # High-recall zoom so the 0.95 target line and ceiling differences are
    # readable; QPS stays log-scaled (orders of magnitude apart).
    xlims = {"dbpedia": (0.75, 1.0), "gist": (0.75, 1.0), "agnews": (0.85, 1.0)}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), sharey=True)
    for i, (ax, (label, ds)) in enumerate(zip(axes, DATASETS)):
        rows = _load_q03(ds)
        opt_rows = _opt_ours_rows(ds)
        for key, disp in Q03_METHODS:
            sub = [r for r in rows if r["method"] == key]
            if not sub:
                continue
            if key == "Ours":
                # round2 Ours as a light dashed reference.
                sub.sort(key=lambda r: fnum(r, "recall"))
                ax.plot(
                    [fnum(r, "recall") for r in sub],
                    [fnum(r, "qps") for r in sub],
                    color="#BBBBBB",
                    linestyle="--",
                    linewidth=0.8,
                    marker="o",
                    markersize=1.6,
                    zorder=2,
                )
                if opt_rows is not None:
                    sub = opt_rows
            sub.sort(key=lambda r: fnum(r, "recall"))
            ax.plot(
                [fnum(r, "recall") for r in sub],
                [fnum(r, "qps") for r in sub],
                color=COLORS[key],
                marker=MARKERS[key],
                label=disp,
                **hero_style(key),
            )
            if key == "Ours" and sub:
                end = sub[-1]
                star_marker(ax, fnum(end, "recall"), fnum(end, "qps"), COLORS["Ours"])
        ax.axvline(0.95, color="#BFBFBF", linestyle=":", linewidth=0.8, zorder=0)
        ax.set_title(label, fontsize=7.5, pad=4)
        ax.set_xlim(*xlims[ds])
        style_log_y(ax)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        panel_label(ax, "abc"[i])
    axes[0].set_ylabel("QPS", fontsize=7)
    for ax in axes:
        ax.set_xlabel("Recall@10 (end-to-end)", fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    save_pub(fig, "fig05_endtoend_recall_qps")


# --------------------------------------------------------------------------
# Figure 4: 03 recall ceiling and index footprint
# --------------------------------------------------------------------------
def _fmt_size(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def fig06_endtoend_recall_index() -> None:
    fig, axes = plt.subplots(3, 2, figsize=(7.0, 5.4))
    for row, (label, ds) in enumerate(DATASETS):
        rows = _load_q03(ds)
        best = {m: max((r for r in rows if r["method"] == m), key=lambda r: fnum(r, "recall"))
                for m, _ in Q03_METHODS if any(r["method"] == m for r in rows)}
        opt_rows = _opt_ours_rows(ds)
        if opt_rows:
            best["Ours"] = max(opt_rows, key=lambda r: fnum(r, "recall"))

        # (a/d) maximum achievable Recall@10
        ax = axes[row, 0]
        methods = list(best)
        recs = [fnum(best[m], "recall") for m in methods]
        colors = [to_rgba(COLORS[m], 1.0 if m == "Ours" else 0.55) for m in methods]
        bars = ax.bar(methods, recs, color=colors, width=0.62, zorder=3)
        for b, v in zip(bars, recs):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.008, f"{v:.3f}",
                    ha="center", va="bottom", fontsize=6)
        ax.axhline(0.95, color="#BFBFBF", linestyle=":", linewidth=0.8, zorder=1)
        ax.set_ylim(0.3, 1.06)
        ax.tick_params(axis="x", labelsize=6, rotation=22)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("Max achievable R@10", fontsize=7)
        ax.set_title(label, fontsize=7.5, pad=4)

        # (b/e) index size at the maximum-recall operating point
        ax = axes[row, 1]
        sizes = [fnum(best[m], "index_size_mb") for m in methods]
        bars = ax.bar(methods, sizes, color=colors, width=0.62, zorder=3)
        style_log_y(ax)
        for b, v in zip(bars, sizes):
            ax.text(b.get_x() + b.get_width() / 2, v * 1.12, _fmt_size(v),
                    ha="center", va="bottom", fontsize=6)
        ax.tick_params(axis="x", labelsize=6, rotation=22)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("Index size at max recall", fontsize=7)
        panel_label(axes[row, 0], "abc"[row])
        panel_label(axes[row, 1], "def"[row])
    handles, labels = [], []
    for m, disp in Q03_METHODS:
        if m in best:
            handles.append(plt.Line2D([], [], color=COLORS[m], marker=MARKERS[m],
                                      linestyle="", markersize=4))
            labels.append(disp)
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.14, 1, 1))
    save_pub(fig, "fig06_endtoend_recall_index")


# --------------------------------------------------------------------------
# Figure 6: memory summary -- 02 payload index size + 03 system peak RSS
# --------------------------------------------------------------------------
def fig09_memory_summary() -> None:
    fig, axes = plt.subplots(3, 2, figsize=(7.0, 5.4))
    for row, (label, ds) in enumerate(DATASETS):
        # 02: per-method 4-bit payload index size under the current protocol
        # (each method builds its own graph with R=M=64 / L=400; see fig04).
        raw = median_aggregate(
            read_csv(RESULTS_ROOT / "02_diskann_fair" / ds / "csv" / "diskann_fair_raw.csv")
        )
        idx: dict[str, float] = {}
        for r in raw:
            if r.get("status") != "done":
                continue
            if r["method"] not in idx:
                idx[r["method"]] = fnum(r, "index_size_mb")
        m02 = [m for m in ("PQ", "SQ", "SAQ", "Ours") if m in idx]

        ax = axes[row, 0]
        colors02 = [to_rgba(COLORS[m], 1.0 if m == "Ours" else 0.55) for m in m02]
        vals02 = [idx[m] for m in m02]
        bars = ax.bar(m02, vals02, color=colors02, width=0.62, zorder=3)
        style_log_y(ax)
        for b, v in zip(bars, vals02):
            ax.text(b.get_x() + b.get_width() / 2, v * 1.12, _fmt_size(v),
                    ha="center", va="bottom", fontsize=6)
        ax.tick_params(axis="x", labelsize=6)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("02 payload index size (R=64/M=64)", fontsize=7)
        ax.set_title(label, fontsize=7.5, pad=4)

        # 03: end-to-end system peak RSS.
        rows = _load_q03(ds)
        rss: dict[str, float] = {}
        for r in rows:
            if r["method"] not in rss:
                rss[r["method"]] = fnum(r, "peak_rss_mb")
        m03 = [m for m, _ in Q03_METHODS if m in rss]

        ax = axes[row, 1]
        colors03 = [to_rgba(COLORS[m], 1.0 if m == "Ours" else 0.55) for m in m03]
        vals03 = [rss[m] / 1024.0 for m in m03]
        bars = ax.bar(m03, vals03, color=colors03, width=0.62, zorder=3)
        style_log_y(ax)
        for b, v in zip(bars, vals03):
            ax.text(b.get_x() + b.get_width() / 2, v * 1.12, f"{v:.1f} GB",
                    ha="center", va="bottom", fontsize=6)
        ax.tick_params(axis="x", labelsize=6)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("03 peak RSS (GB, M=64 full system)", fontsize=7)
        ax.set_title(label, fontsize=7.5, pad=4)

        panel_label(axes[row, 0], "abc"[row])
        panel_label(axes[row, 1], "def"[row])

    handles, labels = [], []
    for m, disp in [("PQ", "PQ-4bit"), ("SQ", "SQ-4bit"), ("SAQ", "SAQ (B=4)"),
                    ("Glass-NSG", "Glass-NSG"), ("OG-LVQ", "OG-LVQ"),
                    ("Ours", "Ours (ExRaBitQ-4bit)"), ("SymphonyQG", "SymphonyQG")]:
        handles.append(plt.Line2D([], [], color=COLORS[m], marker="s", linestyle="",
                                  markersize=4.5))
        labels.append(disp)
    add_figure_legend(fig, handles, labels, ncol=4)
    fig.tight_layout(rect=(0, 0.16, 1, 1))
    save_pub(fig, "fig09_memory_02_03")


# --------------------------------------------------------------------------
# Figure 7: build cost -- 02 end-to-end construction + 03 system build time
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
        ax.set_xticklabels(names, rotation=35, ha="right", fontsize=5.5)
        ax.tick_params(axis="x", labelsize=6)
        ax.tick_params(axis="y", labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
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
# Figure 4: K ablation -- R@10 and QPS at r=1000 vs codebook size K
# --------------------------------------------------------------------------
def fig07_kmeans_summary() -> None:
    data = {ds: _k_summary(ds) for _, ds in DATASETS}
    fig, axes = plt.subplots(3, 2, figsize=(7.0, 5.4), sharex="col")
    for row, (label, ds) in enumerate(DATASETS):
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
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("R@10 @ rerank=1000", fontsize=7)
        ax.set_title(label, fontsize=7.5, pad=4)

        ax = axes[row, 1]
        qps = [rows[k]["qps"] for k in ks]
        ax.plot(ks, qps, color="#767676", linewidth=1.5, marker="s",
                markersize=4.5, zorder=3)
        if 1 in rows:
            ax.plot([1], [rows[1]["qps"]], marker="D", mfc="white",
                    mec="#767676", ms=5.5, mew=1.2, zorder=4)
        ax.set_xscale("log", base=2)
        ax.set_ylim(min(qps) * 0.85, max(qps) * 1.15)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("QPS @ rerank=1000", fontsize=7)

        if row == 2:
            axes[row, 0].set_xlabel("Codebook size K", fontsize=7)
            axes[row, 1].set_xlabel("Codebook size K", fontsize=7)
            for col in range(2):
                axes[row, col].set_xticks(ks)
                axes[row, col].set_xticklabels([str(k) for k in ks], fontsize=6.5)

        panel_label(axes[row, 0], "abc"[row])
        panel_label(axes[row, 1], "def"[row])
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
    fig, axes = plt.subplots(3, 2, figsize=(7.0, 5.3), sharex="col")
    for row, (label, ds) in enumerate(DATASETS):
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
        if row == 2:
            ax.set_xticks(ks)
            ax.set_xticklabels([str(k) for k in ks], fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("Mean relative error", fontsize=7)
        ax.set_title(label, fontsize=7.5, pad=4)

        ax = axes[row, 1]
        ax.plot(ks, train, color="#767676", linewidth=1.5, marker="s", markersize=4.5, zorder=3)
        ax.set_xscale("log", base=2)
        if 1 in rows:
            ax.plot([1], [rows[1]["train_s"]], marker="D", mfc="white", mec="#767676",
                    ms=5.5, mew=1.2, zorder=4)
        if row == 2:
            ax.set_xticks(ks)
            ax.set_xticklabels([str(k) for k in ks], fontsize=6.5)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
        if row == 0:
            ax.set_ylabel("Train time (s)", fontsize=7)
        if row == 2:
            axes[row, 0].set_xlabel("Codebook size K", fontsize=7)
            axes[row, 1].set_xlabel("Codebook size K", fontsize=7)
    for row in range(3):
        panel_label(axes[row, 0], "abc"[row])
        panel_label(axes[row, 1], "def"[row])
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
                edge = "#FF4D00" if sys_name == "Ours" else "white"
                lw = 1.4 if sys_name == "Ours" else 0.5
                ax.bar(pos, vals[k], bar_width, color=color, edgecolor=edge,
                       linewidth=lw, zorder=3, label=disp if k == 0 and i == 0 else None)
        ax.set_title(label, fontsize=7.5, pad=4)
        ax.set_xticks(x)
        ax.set_xticklabels([m for m in SYSTEMS_DECOMP], fontsize=6, rotation=12)
        ax.set_ylim(0, 1.0)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
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
    # R64 / M=64 instrumented measurements at ef=100 (2026-08-19, Feishu doc):
    #   Ours        = final config (M64 / L_build=400 / cap256 + back-stop2 +
    #                 refine1 + sidecar), from query_es/agnews_final_refine1_
    #                 sidecar (visited=2989.34, dist=727.62, latency=1176.44us,
    #                 recall=0.99275).
    #   SymphonyQG  = official source instrumentation, R64: 46.3 expanded
    #                 nodes, 3,011 distances (46.3 exact L2 + 2,965 fast-scan),
    #                 142.6 us, recall 0.994 (R32 was 60/1,969/148).
    #   Glass-NSG   = official source instrumentation, R64_L100: 104.7 visited,
    #                 1,876.6 SQ4U distances, 213.1 us, recall 0.9425
    #                 (R32_L50 was 106/1,431/250).
    #   OG-LVQ      = SVS official binding exposes no counts; latency 938 us.
    metrics = [
        ("visited", "Visited / query", [2989, 46.3, 104.7, None]),
        ("distance", "Distance comps / query", [728, 3011, 1876.6, None]),
        ("latency", "Latency (us) / query", [1176, 142.6, 213.1, 938]),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.6), sharey=True)
    for col, (ax, (_, title, vals)) in enumerate(zip(axes, metrics)):
        for i, (sys_name, v) in enumerate(zip(systems, vals)):
            if v is None:
                continue
            color = to_rgba(COLORS.get(sys_name, "#888888"),
                            1.0 if sys_name == "Ours" else 0.55)
            edge = "#FF4D00" if sys_name == "Ours" else "white"
            ax.bar([i], [v], width=0.62, color=color, edgecolor=edge,
                   linewidth=1.3 if sys_name == "Ours" else 0.5, zorder=3)
            label = f"{v:,.1f}".rstrip("0").rstrip(".")
            ax.text(i, v * 1.18, label, ha="center", va="bottom",
                    fontsize=6, color=COLORS.get(sys_name, "#333333"))
        ax.set_yscale("log")
        ax.set_ylim(10, 20000)
        ax.set_xticks(range(len(systems)))
        ax.set_xticklabels(systems, rotation=12, fontsize=6)
        ax.set_title(title, fontsize=7.5, pad=4)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color="#E8E8E8", lw=0.5, zorder=0)
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
    fig09_memory_summary()
    fig10_build_time_02_03()
    fig11_decomposition_attribution()
    fig12_agnews_instrumented_ef100()
    return 0


if __name__ == "__main__":
    sys.exit(main())
