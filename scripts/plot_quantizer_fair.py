#!/usr/bin/env python3
"""Generate the 01 quantizer-fair figures from the per-method CSVs.

Figures written to results/<dataset>/figures/01_quantizer_fair/:
  - 4bit_recall_qps.(png|pdf)        Recall@10 vs compressed-distance QPS
  - 4bit_per_distance_cost.(png|pdf) Per compressed-distance-call cost
  - 4bit_quantization_error.(png|pdf) Mean/P95 relative distance error
  - 4bit_index_size.(png|pdf)        Index size in MB
  - 4bit_train_encode_time.(png|pdf) Train and encode wall time
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


OURS_PREFIX = "Ours_RaBitQ_K"
PLANNED = {"4bit_recall_qps", "4bit_quantization_error"}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def method_from_csv(path: Path, suffix: str) -> str:
    """Infer method name from a file like <METHOD>_<suffix>.csv."""
    return path.name[: -len(suffix) - len(".csv")].rstrip("_")


def load_methods(csv_dir: Path, suffix: str) -> list[str]:
    methods = []
    for p in sorted(csv_dir.glob(f"*_{suffix}.csv")):
        m = method_from_csv(p, suffix)
        if m not in methods:
            methods.append(m)
    for m in METHOD_ORDER:
        if m not in methods:
            methods.append(m)
    return methods


def fnum(row: dict[str, str], key: str) -> float:
    return float(row[key])


def style_of(method: str, i: int) -> dict:
    markers = ["o", "s", "D", "^"]
    return {"marker": markers[i % len(markers)], "linewidth": 1.6, "markersize": 4.0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="dbpedia")
    ap.add_argument("--results-root", default="results")
    ap.add_argument("--ours-k", type=int, default=1,
                    help="Ours codebook size K to plot (default 1)")
    args = ap.parse_args()

    METHOD_ORDER = ["PQ_4bit", "SQ_4bit", "SAQ_B4", f"{OURS_PREFIX}{args.ours_k}"]
    base = Path(args.results_root) / args.dataset
    csv_dir = base / "csv" / "01_quantizer_fair"
    fig_dir = base / "figures" / "01_quantizer_fair"
    fig_dir.mkdir(parents=True, exist_ok=True)

    recall_methods = load_methods(csv_dir, "recall_qps")
    acc_methods = load_methods(csv_dir, "accuracy")
    methods = [m for m in METHOD_ORDER if m in recall_methods or m in acc_methods]

    if not methods:
        raise SystemExit(f"no method CSVs found under {csv_dir}")

    colors = plt.cm.tab10.colors
    styles = {m: {"color": colors[i]} for i, m in enumerate(methods)}

    def save(fig, name: str) -> None:
        if name not in PLANNED:
            plt.close(fig)
            return
        for ext in ("png", "pdf"):
            fig.savefig(fig_dir / f"{name}.{ext}", dpi=220)
        plt.close(fig)
        print(f"wrote {fig_dir / name}.{{png,pdf}}")

    # ---- 1. Recall-QPS curve (fixed-candidate rerank sweep) -----------------
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    for i, m in enumerate(methods):
        rows = read_csv_rows(csv_dir / f"{m}_recall_qps.csv")
        rows.sort(key=lambda r: fnum(r, "recall"))
        ax.plot(
            [fnum(r, "recall") for r in rows],
            [fnum(r, "qps") for r in rows],
            label=m,
            **styles[m],
            **style_of(m, i),
        )
    ax.set_xlabel("Recall@10 (fixed candidates)")
    ax.set_ylabel("QPS (compressed-distance kernel)")
    ax.set_yscale("log")
    ax.set_title(f"{args.dataset} 01_quantizer_fair 4-bit recall-QPS")
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend()
    fig.tight_layout()
    save(fig, "4bit_recall_qps")

    # ---- 1b. Per-distance-call cost (fixed-candidate sweep) -----------------
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    medians = {}
    for i, m in enumerate(methods):
        rows = read_csv_rows(csv_dir / f"{m}_recall_qps.csv")
        pts = [
            (fnum(r, "search_param_value"), fnum(r, "latency_mean_us") / fnum(r, "search_param_value"))
            for r in rows
            if fnum(r, "search_param_value") > 0 and fnum(r, "latency_mean_us") > 0
        ]
        if not pts:
            continue
        pts.sort()
        import statistics

        med = statistics.median(c for r, c in pts if r >= 30)
        medians[m] = med
        ax.plot(
            [r for r, _ in pts],
            [c for _, c in pts],
            label=f"{m} (median {med:.2f} us)",
            **styles[m],
            **style_of(m, i),
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Candidate prefix size (rerank_candidates)")
    ax.set_ylabel("Latency per distance call (us)")
    ax.set_title(f"{args.dataset} 01_quantizer_fair 4-bit per-distance cost")
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend(fontsize=9)
    fig.tight_layout()
    save(fig, "4bit_per_distance_cost")

    # ---- 2. Quantization error ----------------------------------------------
    acc_rows = [read_csv_rows(csv_dir / f"{m}_accuracy.csv")[0] for m in acc_methods]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    x = list(range(len(acc_rows)))
    width = 0.38
    mean_vals = [fnum(r, "mean_relative_error") for r in acc_rows]
    p95_vals = [fnum(r, "p95_relative_error") for r in acc_rows]
    b1 = ax.bar(
        [i - width / 2 for i in x],
        mean_vals,
        width,
        label="Mean relative error",
        color=[styles[m]["color"] for m in acc_methods],
        alpha=0.85,
    )
    b2 = ax.bar(
        [i + width / 2 for i in x],
        p95_vals,
        width,
        label="P95 relative error",
        color=[styles[m]["color"] for m in acc_methods],
        alpha=0.45,
    )
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(acc_methods, rotation=12)
    ax.set_ylabel("Relative distance error")
    ax.set_title(f"{args.dataset} 01_quantizer_fair 4-bit quantization error")
    ax.grid(True, axis="y", which="both", linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend()
    for bars, vals in ((b1, mean_vals), (b2, p95_vals)):
        for bar, v in zip(bars, vals):
            ax.annotate(
                f"{v:.4f}",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                textcoords="offset points",
                xytext=(0, 2),
                ha="center",
                fontsize=7,
            )
    fig.tight_layout()
    save(fig, "4bit_quantization_error")

    # ---- 3. Index size --------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    sizes = [fnum(r, "index_size_mb") for r in acc_rows]
    ax.bar(
        x,
        sizes,
        0.62,
        color=[styles[m]["color"] for m in acc_methods],
        alpha=0.85,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(acc_methods, rotation=12)
    ax.set_ylabel("Index size (MB)")
    ax.set_title(f"{args.dataset} 01_quantizer_fair 4-bit index size")
    ax.grid(True, axis="y", linestyle="--", linewidth=0.5, alpha=0.45)
    for bar, v in zip(ax.patches, sizes):
        ax.annotate(
            f"{v:,.0f}",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            textcoords="offset points",
            xytext=(0, 2),
            ha="center",
            fontsize=8,
        )
    fig.tight_layout()
    save(fig, "4bit_index_size")

    # ---- 4. Train / encode time ------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    train_vals = [fnum(r, "train_time_ms") for r in acc_rows]
    encode_vals = [fnum(r, "encode_time_ms") for r in acc_rows]
    b1 = ax.bar(
        [i - width / 2 for i in x],
        [max(v, 1.0) for v in train_vals],
        width,
        label="Train time (ms)",
        color=[styles[m]["color"] for m in acc_methods],
        alpha=0.85,
    )
    b2 = ax.bar(
        [i + width / 2 for i in x],
        [max(v, 1.0) for v in encode_vals],
        width,
        label="Encode time (ms)",
        color=[styles[m]["color"] for m in acc_methods],
        alpha=0.45,
    )
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(acc_methods, rotation=12)
    ax.set_ylabel("Time (ms, log scale)")
    ax.set_title(f"{args.dataset} 01_quantizer_fair 4-bit train/encode time")
    ax.grid(True, axis="y", which="both", linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend()
    for bars, vals in ((b1, train_vals), (b2, encode_vals)):
        for bar, v in zip(bars, vals):
            label = "n/a" if v <= 0 else f"{v:,.0f}"
            ax.annotate(
                label,
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                textcoords="offset points",
                xytext=(0, 2),
                ha="center",
                fontsize=7,
            )
    fig.tight_layout()
    save(fig, "4bit_train_encode_time")

    print(f"figures written to {fig_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
