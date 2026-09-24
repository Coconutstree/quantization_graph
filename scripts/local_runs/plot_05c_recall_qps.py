"""Paper-layout Recall-QPS figures from this run's CSV; acceptance remains pending."""
import argparse
import csv
import json
import math
from pathlib import Path
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

STYLES = {
    "Ours-Disk": ("Ours", "#c23e7d", "o", "-"),
    "Glass-NSG-DiskPort": ("Glass-NSG", "#8878b2", "v", ":"),
    "OG-LVQ-DiskPort": ("OG-LVQ", "#829d36", "+", "-."),
    "SymphonyQG-DiskPort": ("SymphonyQG", "#288f8d", "x", "--"),
    "DiskANN-PQ-Disk": ("DiskANN-PQ", "#477bb4", "s", "--"),
}


def render(rows, datasets, out, name, lower, partial=False):
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
                         "font.size": 7, "svg.fonttype": "none", "pdf.fonttype": 42,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#7c8087", "xtick.color": "#656970",
                         "ytick.color": "#656970", "axes.linewidth": 0.8})
    fig, axes = plt.subplots(len(datasets), 1, figsize=(7.205, 2.30 * len(datasets) + 0.45), squeeze=False)
    for i, dataset in enumerate(datasets):
        ax = axes[i, 0]
        for method, (label, color, marker, linestyle) in STYLES.items():
            points = sorted((r for r in rows if r["dataset"] == dataset and r["method"] == method),
                            key=lambda r: int(r["search_width"]))
            if not points:
                continue
            locality = (out.parent / dataset / "Ours-Disk/layout_revision.json").exists()
            if method == "Ours-Disk" and locality:
                # Requested widths below top-k execute as width 10; do not plot as distinct settings.
                points = [r for r in points if int(r["search_width"]) >= 10]
            x = [float(r["recall"]) for r in points]
            y = [float(r["qps"]) for r in points]
            assert all(math.isfinite(v) and v > 0 for v in y), "Log QPS requires positive measurements"
            assert all(0 <= v <= 1 for v in x)
            ax.plot(x, y, color=color, marker=marker, linestyle=linestyle,
                    linewidth=1.6 if method == "Ours-Disk" else 1.05,
                    markersize=3.2, markevery=max(1, len(points) // 9), label=label)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.set(xlim=(lower, 1.002), xlabel="Recall@10 (end-to-end)", ylabel="QPS")
        if lower == 0.90:
            ax.set_xticks([0.90, 0.92, 0.94, 0.96, 0.98, 1.00])
            ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.2f}"))
        ax.grid(axis="y", color="#dfe1e5", linewidth=0.5)
        ax.axvline(0.95, color="#92969c", linestyle=":", linewidth=0.8)
        ax.text(0.015, 0.95, {"agnews": "AGNews", "gist": "GIST-1M", "dbpedia": "DBpedia", "sift10m": "SIFT10M"}[dataset] + (" (Ours: locality layout)" if locality else ""),
                transform=ax.transAxes, va="top", weight="bold", fontsize=8)
        ax.text(-0.08, 1.025, chr(97 + i), transform=ax.transAxes, weight="bold", fontsize=8)
        ax.tick_params(labelsize=7)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False,
               fontsize=7, bbox_to_anchor=(0.53, 0.008), handlelength=2.2, columnspacing=1)
    present = {r["method"] for r in rows if r["dataset"] in datasets}
    missing = ", ".join(label for method, (label, *_) in STYLES.items() if method not in present)
    caption = (f"Incomplete: missing {missing}; w32; C0; acceptance pending" if partial
               else "Single test run; w32; C0; acceptance pending")
    fig.text(0.53, 0.115 / (len(datasets) + 0.3), caption,
             ha="center", fontsize=7, color="#656970")
    fig.subplots_adjust(left=0.10, right=0.98, top=0.91 if len(datasets) == 1 else 0.97,
                        bottom=0.29 if len(datasets) == 1 else 0.16, hspace=0.65)
    fig.savefig(out / f".{name}.tmp.svg")
    fig.savefig(out / f".{name}.tmp.pdf")
    fig.savefig(out / f".{name}.tmp.png", dpi=600)
    for ext in ("svg", "pdf", "png"):
        (out / f".{name}.tmp.{ext}").replace(out / f"{name}.{ext}")
    plt.close(fig)


def export(root):
    if (root / "EXPORTS_SUPERSEDED_BY.txt").exists():
        return
    import fcntl
    with (root / "exports/.recall_qps.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _export_locked(root)


def _export_locked(root):
    out = root / "exports"
    source = out / "pending_test_rows.csv"
    if not source.exists():
        return
    with source.open() as stream:
        rows = list(csv.DictReader(stream))
    run = json.loads((root / "run.json").read_text())
    complete = [d for d in run["datasets"] if all(
        sorted(int(r["search_width"]) for r in rows if r["dataset"] == d and r["method"] == m)
        == [w for w in run["widths"] if m not in ("Ours-Disk", "DiskANN-PQ-Disk") or w >= 10] for m in STYLES)]
    for dataset in run["datasets"]:
        if dataset not in complete and any(r["dataset"] == dataset for r in rows):
            render(rows, [dataset], out, f"{dataset}_recall_qps_partial", 0, partial=True)
    if not complete:
        return
    for dataset in complete:
        for ext in ("svg", "pdf", "png"):
            (out / f"{dataset}_recall_qps_partial.{ext}").unlink(missing_ok=True)
        render(rows, [dataset], out, f"{dataset}_recall_qps", 0.90)
        render(rows, [dataset], out, f"{dataset}_recall_qps_full", 0)
    render(rows, complete, out, "fig05_disk_recall_qps", 0.90)
    (out / "recall_qps_figure_notes.md").write_text(
        "# Recall-QPS figure contract\n\n"
        "Question: compare measured end-to-end throughput at matched recall, without superiority claims.\n"
        "Python; stacked dataset panels; 183 mm width; editable SVG/PDF and 600 dpi PNG.\n"
        "Style adapted from the user-supplied fig05; original paper figure remains unchanged.\n"
        "Mapping: dataset -> panel; method -> curve; recall -> Recall@10; qps -> queries/s; search_width -> point order.\n"
        "Lines connect ALL measured settings in width order, without interpolation, smoothing or envelope filtering.\n"
        "Markers are representative only. User-requested main view clips recall <0.90; *_full plots retain the full range.\n"
        "Only the axis window changes; all source rows, QPS values and width-order connections are preserved.\n"
        "AGNews DiskANN widths 540/580 require clean timing remeasurement after migration. "
        "If its result.json contains timing_repair_manifest, that manifest records the replacement measurements and parity check.\n"
        "Source: pending_test_rows.csv. Only datasets with all five complete methods are plotted.\n"
        "DiskANN L<10 is unsupported, not imputed. One repeat, no uncertainty intervals or statistical tests.\n"
        "These are manuscript-layout drafts, NOT accepted formal measurements; memory/parity acceptance is pending.\n"
        f"Completed datasets: {', '.join(complete)}.\n")
    counts = {d: {m: {"source_points": sum(r["dataset"] == d and r["method"] == m for r in rows),
                      "points_in_zoom": sum(r["dataset"] == d and r["method"] == m and float(r["recall"]) >= 0.90 for r in rows)}
                   for m in STYLES} for d in complete}
    (out / "recall_qps_zoom_counts.json").write_text(json.dumps(counts, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    stamp = None
    while True:
        source = args.root / "exports/pending_test_rows.csv"
        current = source.stat().st_mtime_ns if source.exists() else None
        if current != stamp:
            export(args.root)
            stamp = current
        if not args.watch:
            break
        queue = json.loads((args.root / "queue.json").read_text())
        if queue and queue[-1]["status"] == "failed":
            break
        if len(queue) == 4 and all(r["status"] == "diagnostic_complete" for r in queue):
            export(args.root)
            break
        time.sleep(60)
