#!/usr/bin/env python3
"""Plot the 02 diskann-fair recall-QPS figures from the canonical raw CSV.

Reads results/<dataset>/csv/02_diskann_fair/diskann_fair_raw.csv (the
aggregated per-search-list-size rows written by the 02 runner) and writes:

  - results/<dataset>/csv/02_diskann_fair/dbpedia_diskann_vs_rabitq_recall_qps.csv
  - results/<dataset>/figures/02_diskann_fair/dbpedia_diskann_vs_rabitq_recall_qps_linear.(png|pdf)
  - results/<dataset>/figures/02_diskann_fair/dbpedia_diskann_vs_rabitq_recall_qps_logy.(png|pdf)
  - results/<dataset>/figures/02_diskann_fair/dbpedia_diskann_fair_index_size.(png|pdf)
  - results/<dataset>/figures/02_diskann_fair/dbpedia_diskann_fair_build_time.(png|pdf)
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


# Method order for legend and color assignment.
METHOD_ORDER = ["PQ", "SQ", "SAQ", "Ours"]  # Ours M=32 (degree-matched with R=32 shared graph)
PLANNED = {
    "dbpedia_diskann_vs_rabitq_recall_qps_logy",
    "dbpedia_diskann_vs_rabitq_with_system_recall_qps_logy",
}
LABELS = {
    "Ours": "Ours (ExRaBitQ-4bit)",
    "PQ": "DiskANN-PQ-4bit",
    "SQ": "DiskANN-SQ-4bit",
    "SAQ": "DiskANN-SAQ-4bit",
}
COLORS = {
    "Ours": "#9467bd",  # tab10 purple, matches the previous RaBitQ/Vamana curve
    "PQ": "#1f77b4",    # tab10 blue
    "SQ": "#2ca02c",    # tab10 green
    "SAQ": "#ff7f0e",   # tab10 orange
}
MARKERS = {
    "Ours": "^",
    "PQ": "o",
    "SQ": "s",
    "SAQ": "D",
}

# 03 system-fair families (overlaid on the 02 figure only when requested).
SYSTEM_METHOD_ORDER = ["SymphonyQG", "OG-LVQ", "Glass-NSG", "NGT-QG"]
SYSTEM_LABELS = {
    "SymphonyQG": "Sys: SymphonyQG",
    "OG-LVQ": "Sys: OG-LVQ",
    "Glass-NSG": "Sys: Glass-NSG",
    "NGT-QG": "Sys: NGT-QG",
}
SYSTEM_COLORS = {
    "SymphonyQG": "#e377c2",
    "OG-LVQ": "#8c564b",
    "Glass-NSG": "#17becf",
    "NGT-QG": "#7f7f7f",
}
SYSTEM_MARKERS = {
    "SymphonyQG": "x",
    "OG-LVQ": "+",
    "Glass-NSG": "v",
    "NGT-QG": "P",
}


def fmt_duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.0f} s"


def read_raw(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def as_float(row: dict[str, str], key: str) -> float:
    return float(row[key])


def save_fig(fig, fig_dir: Path, stem: str, dpi: int) -> None:
    if stem not in PLANNED:
        plt.close(fig)
        return
    for ext in ("png", "pdf"):
        out = fig_dir / f"{stem}.{ext}"
        fig.savefig(out, dpi=dpi)
        print(f"wrote {out}")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="dbpedia")
    ap.add_argument("--results-root", default="results/disk_environment")
    ap.add_argument("--dpi", type=int, default=220)
    ap.add_argument(
        "--include-system-fair",
        action="store_true",
        help="overlay 03 system-fair recall-QPS curves on the 02 figures",
    )
    ap.add_argument(
        "--normalize-system-threads",
        action="store_true",
        help="divide 03 QPS by its reported thread count (linear-scaling lower bound)",
    )
    args = ap.parse_args()

    base = Path(args.results_root) / "02_diskann_fair" / args.dataset
    csv_dir = base / "csv"
    fig_dir = base / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    raw_path = csv_dir / "diskann_fair_raw.csv"
    if not raw_path.exists():
        raise SystemExit(f"missing raw CSV: {raw_path}")

    # The raw CSV may contain both Ours R=32 (02 shared-graph) and R=64 (03
    # end-to-end) sweeps. The 02 comparison must use the degree-matched R=32
    # payload only; otherwise M=64 rows would leak into the shared-graph CSV.
    rows = [
        r
        for r in read_raw(raw_path)
        if r.get("status") == "done"
        and not (r["method"] == "Ours" and r.get("max_degree", "") != "32")
    ]
    methods = [m for m in METHOD_ORDER if any(r["method"] == m for r in rows)]
    if not methods:
        raise SystemExit(f"no done rows in {raw_path}")

    # ---- optional 03 system-fair overlay -----------------------------------
    sys_rows: list[dict[str, str]] = []
    if args.include_system_fair:
        sys_raw = Path(args.results_root) / "03_system_fair" / args.dataset / "csv" / "system_fair_raw.csv"
        if not sys_raw.exists():
            raise SystemExit(f"missing system-fair raw CSV: {sys_raw}")
        sys_rows = read_raw(sys_raw)
        sys_methods = [
            m
            for m in SYSTEM_METHOD_ORDER
            if any(r["method"] == m for r in sys_rows)
        ]
        if not sys_methods:
            raise SystemExit(f"no system-fair rows in {sys_raw}")

    # ---- write the merged plot CSV ----------------------------------------
    out_csv = csv_dir / "dbpedia_diskann_vs_rabitq_recall_qps.csv"
    out_rows: list[dict[str, str]] = []
    for r in rows:
        out_rows.append(
            {
                "dataset": r.get("dataset", args.dataset),
                "method": LABELS.get(r["method"], r["method"]),
                "search_param_name": r.get("search_param_name", "efSearch"),
                "search_param_value": r.get("search_param_value", ""),
                "recall_at_10": r["recall"],
                "qps": r["qps"],
                "latency_mean_us": r.get("latency_mean_us", ""),
                "latency_p95_us": r.get("latency_p95_us", ""),
                "source_log": r.get("log_path", ""),
            }
        )
    fieldnames = [
        "dataset",
        "method",
        "search_param_name",
        "search_param_value",
        "recall_at_10",
        "qps",
        "latency_mean_us",
        "latency_p95_us",
        "source_log",
    ]
    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"wrote {out_csv} rows={len(out_rows)}")

    if args.include_system_fair:
        combined_csv = csv_dir / "dbpedia_02_03_combined_recall_qps.csv"
        combined_rows: list[dict[str, str]] = []
        for r in out_rows:
            combined_rows.append(
                {
                    "family": "02_diskann_fair",
                    "dataset": r["dataset"],
                    "method": r["method"],
                    "search_param_name": r["search_param_name"],
                    "search_param_value": r["search_param_value"],
                    "recall_at_10": r["recall_at_10"],
                    "qps": r["qps"],
                    "config_id": "",
                }
            )
        for r in sys_rows:
            combined_rows.append(
                {
                    "family": "03_system_fair",
                    "dataset": r.get("dataset", args.dataset),
                    "method": r["method"],
                    "search_param_name": r.get("search_param", "ef"),
                    "search_param_value": r.get("search_param", ""),
                    "recall_at_10": r["recall"],
                    "qps": r["qps"],
                    "config_id": r.get("config_id", ""),
                }
            )
        with combined_csv.open("w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "family",
                    "dataset",
                    "method",
                    "search_param_name",
                    "search_param_value",
                    "recall_at_10",
                    "qps",
                    "config_id",
                ],
            )
            writer.writeheader()
            writer.writerows(combined_rows)
        print(f"wrote {combined_csv} rows={len(combined_rows)}")

    # ---- figures -----------------------------------------------------------
    title_suffix = " + 03 system-fair" if args.include_system_fair else ""
    if args.normalize_system_threads:
        title_suffix += " (03 QPS/threads)"
    title = (
        f"{args.dataset} 02_diskann_fair 4-bit DiskANN vs RaBitQ "
        f"recall-QPS{title_suffix}"
    )
    for logy in (False, True):
        fig, ax = plt.subplots(figsize=(8.4, 5.4))
        for m in methods:
            pts = sorted(
                (r for r in rows if r["method"] == m),
                key=lambda r: as_float(r, "recall"),
            )
            if not pts:
                continue
            ax.plot(
                [as_float(r, "recall") for r in pts],
                [as_float(r, "qps") for r in pts],
                marker=MARKERS[m],
                color=COLORS[m],
                linewidth=2.2 if m == "Ours" else 1.8,
                markersize=4.5 if m == "Ours" else 4.0,
                label=LABELS[m],
            )
        if args.include_system_fair:
            for m in sys_methods:
                pts = sorted(
                    (r for r in sys_rows if r["method"] == m),
                    key=lambda r: as_float(r, "recall"),
                )
                if not pts:
                    continue
                thread_div = 1.0
                if args.normalize_system_threads:
                    thread_div = max(1.0, as_float(pts[0], "threads"))
                ax.plot(
                    [as_float(r, "recall") for r in pts],
                    [as_float(r, "qps") / thread_div for r in pts],
                    marker=SYSTEM_MARKERS[m],
                    color=SYSTEM_COLORS[m],
                    linestyle="--",
                    linewidth=1.6,
                    markersize=3.6,
                    label=(
                        f"{SYSTEM_LABELS[m]} (÷{int(thread_div)})"
                        if args.normalize_system_threads
                        else SYSTEM_LABELS[m]
                    ),
                )
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel("Recall@10")
        ax.set_ylabel("QPS")
        ax.set_title(title)
        ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.45)
        ax.legend()
        fig.tight_layout()
        stem = (
            "dbpedia_diskann_vs_rabitq_with_system_recall_qps"
            + ("_threadnorm" if args.normalize_system_threads else "")
            + ("_logy" if logy else "_linear")
        ) if args.include_system_fair else (
            "dbpedia_diskann_vs_rabitq_recall_qps"
            + ("_logy" if logy else "_linear")
        )
        save_fig(fig, fig_dir, stem, args.dpi)

    # ---- memory (index size) comparison -------------------------------------
    summaries = [next(r for r in rows if r["method"] == m) for m in methods]
    mib = 1024.0 * 1024.0
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    x = list(range(len(methods)))
    payload_mib = [as_float(r, "index_bytes") / mib for r in summaries]
    residual_mib = [as_float(r, "residual_bytes") / mib for r in summaries]
    fp32_mib = [as_float(r, "fp32_base_bytes") / mib for r in summaries]
    aux_mib = [as_float(r, "auxiliary_bytes") / mib for r in summaries]
    widths = 0.62
    b1 = ax.bar(
        x,
        payload_mib,
        widths,
        label="Quantized payload",
        color=[COLORS[m] for m in methods],
        alpha=0.9,
    )
    b2 = ax.bar(
        x,
        residual_mib,
        widths,
        bottom=payload_mib,
        label="Residual",
        color=[COLORS[m] for m in methods],
        alpha=0.55,
    )
    b3 = ax.bar(
        x,
        fp32_mib,
        widths,
        bottom=[p + r for p, r in zip(payload_mib, residual_mib)],
        label="FP32 base vectors",
        color="#b0b0b0",
        alpha=0.55,
    )
    ax.bar(
        x,
        aux_mib,
        widths,
        bottom=[p + r + f for p, r, f in zip(payload_mib, residual_mib, fp32_mib)],
        color=[COLORS[m] for m in methods],
        alpha=0.25,
    )
    totals = [p + r + f + a for p, r, f, a in zip(payload_mib, residual_mib, fp32_mib, aux_mib)]
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[m] for m in methods], rotation=12)
    ax.set_ylabel("Index size (MB)")
    ax.set_title(f"{args.dataset} 02_diskann_fair 4-bit index size")
    ax.grid(True, axis="y", linestyle="--", linewidth=0.5, alpha=0.45)
    ax.set_ylim(top=max(totals) * 1.08)
    ax.legend(loc="upper left")
    for i, total in enumerate(totals):
        ax.annotate(
            f"{total:,.0f}",
            (i, total),
            textcoords="offset points",
            xytext=(0, 3),
            ha="center",
            fontsize=9,
        )
    fig.tight_layout()
    save_fig(fig, fig_dir, "dbpedia_diskann_fair_index_size", args.dpi)

    # ---- build time comparison ------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    graph_s = [as_float(r, "graph_build_time_ms") / 1000.0 for r in summaries]
    payload_s = []
    for r in summaries:
        total_s = as_float(r, "build_time_ms") / 1000.0
        # Ours' build_time includes its own graph build; baselines load the
        # shared fp32 graph, so their build_time excludes graph construction.
        if r["method"] == "Ours":
            payload_s.append(total_s - as_float(r, "graph_build_time_ms") / 1000.0)
        else:
            payload_s.append(total_s)
    b1 = ax.bar(
        x,
        graph_s,
        widths,
        label="Graph build",
        color=[COLORS[m] for m in methods],
        alpha=0.85,
    )
    b2 = ax.bar(
        x,
        payload_s,
        widths,
        bottom=graph_s,
        label="Payload build (train + encode)",
        color=[COLORS[m] for m in methods],
        alpha=0.45,
    )
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[m] for m in methods], rotation=12)
    ax.set_ylabel("Build time (s)")
    ax.set_title(f"{args.dataset} 02_diskann_fair 4-bit build time")
    ax.grid(True, axis="y", which="both", linestyle="--", linewidth=0.5, alpha=0.45)
    ax.legend(loc="upper left")
    for i, (g, p) in enumerate(zip(graph_s, payload_s)):
        ax.annotate(
            fmt_duration(g + p),
            (i, g + p),
            textcoords="offset points",
            xytext=(0, 3),
            ha="center",
            fontsize=9,
        )
    fig.tight_layout()
    save_fig(fig, fig_dir, "dbpedia_diskann_fair_build_time", args.dpi)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
