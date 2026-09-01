#!/usr/bin/env python3
"""Memory-experiment-style figures for the 05 gist test run.

Style mirrors the in-memory 03 system-fair figures
(``scripts/plot_system_fair.py``): Arial 7 pt, #FCFCFD panels, method colors
(Ours #C2417A, SymphonyQG #0F766E, OG-LVQ #6B8E23, Glass #7A6FA6, DiskANN
#4A6FA5), y-grid #DDE1E6, and SVG/PDF/PNG(300dpi)/TIFF(600dpi) exports.

Inputs (produced by ``run_05_gist_test.py``):
  <out>/rounds/round_w<W>/<layer>_rows.csv   full per-round query sweep
  <out>/summary/summary.csv                  build + selected query stats

Figures:
  test_qps_recall_grid   Recall@10 vs QPS per method, one panel per worker round
  test_qps_vs_workers    QPS at Recall@10~0.95 vs workers (process-count impact)
  test_ours_io_stages    Ours staged I/O (full4/rerank page reads) + io_wait
  test_build_stats       graph-build time and distance evaluations per method
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

os_env = __import__("os")
os_env.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib_config")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
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

GRID = "#DDE1E6"
METHOD_COLORS = {
    "Ours": "#C2417A",
    "Ours-Disk": "#C2417A",
    "SymphonyQG": "#0F766E",
    "SymphonyQG-DiskPort": "#0F766E",
    "OG-LVQ": "#6B8E23",
    "OG-LVQ-DiskPort": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
    "Glass-NSG-DiskPort": "#7A6FA6",
    "DiskANN-PQ-Disk": "#4A6FA5",
    "PQ-DiskANN-Disk": "#B0892E",
    "SQ-DiskANN-Disk": "#3A7D44",
    "SAQ-DiskANN-Disk": "#8E4A9E",
}
METHOD_MARKERS = {
    "Ours": "o",
    "Ours-Disk": "o",
    "SymphonyQG": "x",
    "SymphonyQG-DiskPort": "x",
    "OG-LVQ": "+",
    "OG-LVQ-DiskPort": "+",
    "Glass-NSG": "v",
    "Glass-NSG-DiskPort": "v",
    "DiskANN-PQ-Disk": "D",
    "PQ-DiskANN-Disk": "s",
    "SQ-DiskANN-Disk": "^",
    "SAQ-DiskANN-Disk": "P",
}
METHOD_LINESTYLES = {
    "Ours": "-",
    "Ours-Disk": "-",
    "SymphonyQG": "--",
    "SymphonyQG-DiskPort": "--",
    "OG-LVQ": "-.",
    "OG-LVQ-DiskPort": "-.",
    "Glass-NSG": ":",
    "Glass-NSG-DiskPort": ":",
    "DiskANN-PQ-Disk": "-",
    "PQ-DiskANN-Disk": "--",
    "SQ-DiskANN-Disk": "-.",
    "SAQ-DiskANN-Disk": ":",
}


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _param_num(label) -> float:
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", str(label))
    return float(m.group(0)) if m else 0.0


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as stream:
        return list(csv.DictReader(stream))


def _style_for(method: str, methods: list[str]) -> tuple[str, str, str]:
    color = METHOD_COLORS.get(method)
    if color is None:
        fallback = plt.cm.tab10.colors[methods.index(method) % 10]
        color = "#" + "".join(f"{int(c * 255):02x}" for c in fallback[:3])
    return (
        color,
        METHOD_MARKERS.get(method, "o"),
        METHOD_LINESTYLES.get(method, "-"),
    )


def save_outputs(fig, fig_dir: Path, png_name: str) -> None:
    fig_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(png_name).stem
    fig.savefig(fig_dir / f"{stem}.svg", bbox_inches="tight")
    fig.savefig(fig_dir / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(fig_dir / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(fig_dir / f"{stem}.tiff", dpi=600, bbox_inches="tight")


def _method_label(method: str) -> str:
    return method.replace("-DiskPort", "").replace("-Disk", "")


def plot_qps_recall_grid(
    rounds_root: Path,
    fig_dir: Path,
    layer: str,
    workers: tuple[int, ...],
) -> None:
    """Recall@10 vs QPS per method, one panel per worker round (memory style)."""
    methods_seen: list[str] = []
    data: dict[int, dict[str, list[dict[str, str]]]] = {}
    for w in workers:
        rows = _read_rows(rounds_root / f"round_w{w}" / f"{layer}_rows.csv")
        rows = [r for r in rows if r.get("workers", "") == str(w)]
        by_method: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            method = row.get("method", "")
            if not method or method not in METHOD_COLORS:
                continue
            if row.get("ablation", "") not in ("", "db1+coalescing+reuse"):
                continue
            try:
                recall = float(row.get("recall", "nan"))
                qps = float(row.get("qps", "nan"))
            except ValueError:
                continue
            if not (0 <= recall <= 1 and qps > 0):
                continue
            by_method.setdefault(method, []).append(row)
            if method not in methods_seen:
                methods_seen.append(method)
        data[w] = by_method
    if not methods_seen:
        print(f"[figure] no {layer} rows for recall-QPS grid")
        return

    fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.6), squeeze=False)
    for col, w in enumerate(workers):
        ax = axes[col // 3][col % 3]
        for method in methods_seen:
            color, marker, linestyle = _style_for(method, methods_seen)
            pts = sorted(data.get(w, {}).get(method, []), key=lambda r: _param_num(r["search_param"]))
            x = [float(r["recall"]) for r in pts]
            y = [float(r["qps"]) for r in pts]
            if not x:
                continue
            hero = method in ("Ours", "Ours-Disk")
            ax.plot(
                x,
                y,
                marker=marker,
                markersize=3.8 if hero else 2.8,
                linewidth=2.0 if hero else 1.0,
                linestyle=linestyle,
                alpha=1.0 if hero else 0.8,
                color=color,
                label=_method_label(method) if col == 0 else None,
                zorder=5 if hero else 2,
            )
        ax.set_title(f"{w} workers", fontsize=7.5)
        ax.set_xlim(0.55, 1.0)
        ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
        if col == 0:
            ax.set_ylabel("QPS")
        if col >= 3:
            ax.set_xlabel("Recall@10")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False)
    fig.suptitle("Recall@10 vs QPS (test run)", fontsize=8)
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    save_outputs(fig, fig_dir, f"test_qps_recall_grid_{layer}.png")
    plt.close(fig)


def _at_recall(rows: list[dict[str, str]], target: float = 0.95) -> dict[str, str] | None:
    best = None
    best_delta = float("inf")
    for row in rows:
        recall = _num(row.get("recall"))
        if not (0 <= recall <= 1):
            continue
        delta = abs(recall - target)
        if delta < best_delta:
            best = row
            best_delta = delta
    return best


def plot_qps_vs_workers(
    rounds_root: Path,
    fig_dir: Path,
    layer: str,
    workers: tuple[int, ...],
) -> None:
    """QPS at Recall@10~0.95 versus workers (process-count impact)."""
    methods_seen: list[str] = []
    points: dict[str, list[tuple[int, float]]] = {}
    for w in workers:
        rows = _read_rows(rounds_root / f"round_w{w}" / f"{layer}_rows.csv")
        rows = [r for r in rows if r.get("workers", "") == str(w)]
        by_method: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            method = row.get("method", "")
            if not method or method not in METHOD_COLORS:
                continue
            if row.get("ablation", "") not in ("", "db1+coalescing+reuse"):
                continue
            by_method.setdefault(method, []).append(row)
        for method, method_rows in by_method.items():
            chosen = _at_recall(method_rows)
            if chosen is None:
                continue
            points.setdefault(method, []).append((w, float(chosen["qps"])))
            if method not in methods_seen:
                methods_seen.append(method)
    if not methods_seen:
        print(f"[figure] no {layer} rows for QPS-vs-workers")
        return

    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    worker_order = sorted(workers)
    for method in methods_seen:
        color, marker, linestyle = _style_for(method, methods_seen)
        pts = sorted(points.get(method, []))
        if not pts:
            continue
        x = [worker_order.index(p[0]) for p in pts]
        y = [p[1] for p in pts]
        hero = method in ("Ours", "Ours-Disk")
        ax.plot(
            x,
            y,
            marker=marker,
            markersize=4.2 if hero else 3.2,
            linewidth=2.0 if hero else 1.0,
            linestyle=linestyle,
            alpha=1.0 if hero else 0.8,
            color=color,
            label=_method_label(method),
            zorder=5 if hero else 2,
        )
    ax.set_xticks(range(len(worker_order)), [str(w) for w in worker_order])
    ax.set_xlabel("Workers (process-concurrent queries)")
    ax.set_ylabel("QPS at Recall@10≈0.95")
    ax.set_title("Process-count impact (test run)")
    ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
    ax.legend(loc="lower right")
    fig.tight_layout()
    save_outputs(fig, fig_dir, f"test_qps_vs_workers_{layer}.png")
    plt.close(fig)


def plot_ours_io_stages(
    rounds_root: Path,
    fig_dir: Path,
    workers: tuple[int, ...],
) -> None:
    """Ours staged I/O (full4/rerank page reads) and io_wait vs workers."""
    positions = list(range(len(workers)))
    full4 = []
    rerank = []
    io_wait = []
    io_total = []
    for w in workers:
        rows = _read_rows(rounds_root / f"round_w{w}" / "05c_rows.csv")
        rows = [r for r in rows if r.get("workers", "") == str(w)]
        ours = [r for r in rows if r.get("method") == "Ours-Disk"]
        chosen = _at_recall(ours)
        if chosen is None:
            full4.append(0.0)
            rerank.append(0.0)
            io_wait.append(0.0)
            io_total.append(0.0)
            continue
        full4.append(_num(chosen.get("full4_page_reads")))
        rerank.append(_num(chosen.get("rerank_page_reads")))
        io_wait.append(_num(chosen.get("io_wait_us")) / 1000.0)
        io_total.append(_num(chosen.get("io_requests_per_query")))
    if not any(full4):
        print("[figure] no Ours staged-I/O rows")
        return

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
    ax = axes[0]
    width = 0.35
    ax.bar(
        [v - width / 2 for v in positions], full4, width,
        color=METHOD_COLORS["Ours-Disk"], label="Full4 page reads",
    )
    ax.bar(
        [v + width / 2 for v in positions], rerank, width,
        color="#B0892E", label="Rerank page reads",
    )
    ax.set_xticks(positions, [str(w) for w in workers])
    ax.set_xlabel("Workers")
    ax.set_ylabel("Pages per query @ R@10≈0.95")
    ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
    ax.legend(loc="upper left")

    ax = axes[1]
    ax.plot(
        positions, io_wait, marker="o", linewidth=2.0,
        color=METHOD_COLORS["Ours-Disk"], label="I/O wait (ms)",
    )
    ax.plot(
        positions, io_total, marker="s", linewidth=1.0,
        color="#4A6FA5", label="I/O requests/query",
    )
    ax.set_xticks(positions, [str(w) for w in workers])
    ax.set_xlabel("Workers")
    ax.set_ylabel("I/O per query @ R@10≈0.95")
    ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
    ax.legend(loc="upper left")
    fig.tight_layout()
    save_outputs(fig, fig_dir, "test_ours_io_stages.png")
    plt.close(fig)


def plot_build_stats(
    summary_csv: Path,
    fig_dir: Path,
) -> None:
    """Graph-build time and distance evaluations per method (test build phase)."""
    rows = _read_rows(summary_csv)
    build = [r for r in rows if r.get("part") in ("shared_graph", "ours_graph")]
    if not build:
        print("[figure] no graph-build rows in summary")
        return
    labels = []
    times = []
    distances = []
    for r in build:
        labels.append(f"{r.get('part', '')}\n{r.get('method', '')}")
        times.append(_num(r.get("graph_build_time_ms")) / 1000.0)
        try:
            distances.append(float(r.get("graph_build_distance_evaluations")))
        except (TypeError, ValueError):
            distances.append(0.0)
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    x = list(range(len(labels)))
    ax.bar(x, times, 0.45, color=METHOD_COLORS["Ours-Disk"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=6)
    ax.set_ylabel("Graph build time (s)")
    ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
    ax2 = ax.twinx()
    ax2.plot(
        x, distances, marker="o", linewidth=1.2,
        color="#4A6FA5", label="Distance evaluations",
    )
    ax2.set_ylabel("Graph-build distance evaluations")
    ax2.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    fig.tight_layout()
    save_outputs(fig, fig_dir, "test_build_stats.png")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--workers-list", default="32,16,8,4,2,1")
    ap.add_argument("--layers", default="05b,05c")
    args = ap.parse_args(argv)
    workers = tuple(int(w) for w in args.workers_list.split(",") if w.strip())
    rounds_root = args.out_root / "rounds"
    fig_dir = args.out_root / "figures"
    for layer in (x.strip() for x in args.layers.split(",") if x.strip()):
        plot_qps_recall_grid(rounds_root, fig_dir, layer, workers)
        plot_qps_vs_workers(rounds_root, fig_dir, layer, workers)
    plot_ours_io_stages(rounds_root, fig_dir, workers)
    plot_build_stats(args.out_root / "summary" / "summary.csv", fig_dir)
    print(f"figures written to {fig_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
