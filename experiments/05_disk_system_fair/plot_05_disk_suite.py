"""Nature-style figures for complete, contract-validated 05 formal runs.

This script reads only ``runs/<run-id>/.../aggregate/formal_test_rows.csv``.
Legacy smoke JSONL, memory-only 03 rows and incomplete five-system data are
rejected by construction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import types
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib_qgraph05")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

if "diskfair" not in sys.modules:
    _pkg = types.ModuleType("diskfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules["diskfair"] = _pkg

from diskfair.native_contract import (  # noqa: E402
    FORMAL_REPEATS,
    LAYER_METHODS,
    ContractError,
    atomic_write_csv,
    sha256_file,
    validate_layer_completeness,
)

LAYER_DIRS = {
    "05a": "05A_disk_quantizer_io",
    "05b": "05B_diskann_shared_graph",
    "05c": "05C_disk_system_fair",
}
DATASET_LABELS = {"agnews": "AGNews", "gist": "GIST", "dbpedia": "DBpedia"}
COLORS = {
    "PQ_4bit": "#0072B2",
    "SQ_4bit": "#009E73",
    "SAQ_B4": "#E69F00",
    "Ours_RaBitQ_K1": "#C2417A",
    "PQ-DiskANN-Disk": "#0072B2",
    "SQ-DiskANN-Disk": "#009E73",
    "SAQ-DiskANN-Disk": "#E69F00",
    "Ours-Disk": "#C2417A",
    "Ours-Disk": "#C2417A",
    "SymphonyQG-DiskPort": "#0F766E",
    "OG-LVQ-DiskPort": "#6B8E23",
    "Glass-NSG-DiskPort": "#7A6FA6",
    "DiskANN-PQ-Disk": "#0072B2",
}
MARKERS = ("o", "s", "D", "^", "v")
LINESTYLES = ("-", "--", "-.", ":", (0, (3, 1, 1, 1)))
GRID = "#DDE1E6"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
        "font.size": 7,
        "axes.labelsize": 7,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 6.2,
        "axes.spines.right": False,
        "axes.spines.top": False,
        "axes.linewidth": 0.7,
        "legend.frameon": False,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "savefig.transparent": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
)

NUMERIC_FIELDS = {
    "repeat_id", "workers", "search_width", "beam_width", "recall", "qps",
    "latency_mean_us", "latency_p50_us", "latency_p95_us", "latency_p99_us",
    "fixed_candidate_recall_at_10", "mean_relative_error", "p95_relative_error",
    "pairwise_flip_rate", "index_size_mb", "resident_bytes", "cache_bytes",
    "cache_nodes", "peak_rss_bytes", "io_requests_per_query",
    "sectors_4k_per_query", "bytes_read_per_query", "io_wait_us",
    "distance_compute_us", "query_prep_us", "queue_compute_us", "rerank_us",
    "visited_nodes", "distance_evaluations", "db1_checks", "db1_survivors",
    "full4_candidates", "full4_page_reads", "rerank_candidates",
    "rerank_page_reads", "query_count",
}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for field in NUMERIC_FIELDS:
            value = row.get(field, "")
            if value not in (None, ""):
                row[field] = float(value)
    return rows


def _workers_for(layer: str, dataset: str) -> tuple[int, ...]:
    if dataset == "gist" and layer in ("05b", "05c"):
        return (1, 4, 8, 16, 32)
    return (1, 16)


def _load_complete(run_root: Path, layer: str, dataset: str) -> list[dict[str, Any]]:
    base = run_root / LAYER_DIRS[layer] / dataset / "aggregate"
    csv_path = base / "formal_test_rows.csv"
    manifest_path = csv_path.with_suffix(".manifest.json")
    if not csv_path.exists() or not manifest_path.exists():
        raise ContractError(f"missing formal aggregate or manifest: {csv_path}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("methods") != list(LAYER_METHODS[layer]):
        raise ContractError(f"{manifest_path}: method set is not the formal {layer} set")
    for source in manifest.get("source_artifacts", []):
        path = Path(source["path"])
        if not path.exists() or sha256_file(path) != source["sha256"]:
            raise ContractError(f"source artifact changed or disappeared: {path}")
    rows = _read_rows(csv_path)
    validate_layer_completeness(
        rows,
        layer=layer,
        dataset=dataset,
        repeats=FORMAL_REPEATS,
        workers=_workers_for(layer, dataset),
    )
    if any(str(row.get("formal_ready", "")).lower() != "true" for row in rows):
        raise ContractError(f"{csv_path}: contains non-formal rows")
    return rows


def _validate_run_manifests(run_root: Path) -> None:
    preflight_path = run_root / "manifests" / "preflight.json"
    fio_path = run_root / "manifests" / "fio_preflight.json"
    if not preflight_path.exists() or not fio_path.exists():
        raise ContractError("formal preflight/fio manifests are missing")
    preflight = json.loads(preflight_path.read_text())
    fio = json.loads(fio_path.read_text())
    if preflight.get("formal_ready") is not True:
        raise ContractError("run preflight is not formal-ready")
    if fio.get("status") != "passed" or fio.get("direct") is not True or fio.get("ioengine") != "libaio":
        raise ContractError("fio manifest did not pass direct libaio validation")
    if preflight.get("fio_manifest_sha256") != sha256_file(fio_path):
        raise ContractError("fio manifest hash does not match preflight")


def _selected_rows(run_root: Path, layer: str, dataset: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lock_path = run_root / LAYER_DIRS[layer] / dataset / "manifests" / "tuning.lock.json"
    if not lock_path.exists():
        raise ContractError(f"missing tuning lock: {lock_path}")
    selected = json.loads(lock_path.read_text()).get("selected", {})
    result = []
    for row in rows:
        key = f"{row['method']}::{row['storage_mode']}"
        lock = selected.get(key)
        if lock is None:
            raise ContractError(f"tuning lock has no entry for {key}")
        if str(row["config_id"]) == str(lock["config_id"]):
            result.append(row)
    return result


def _median_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    keys = (
        "method", "storage_mode", "cache_mode", "search_dram_budget_gib",
        "workers", "config_id", "search_param", "search_width", "beam_width",
        "ablation",
    )
    for row in rows:
        groups[tuple(row.get(key, "") for key in keys)].append(row)
    result = []
    for _, group in groups.items():
        repeat_ids = {int(r["repeat_id"]) for r in group}
        if repeat_ids != set(range(FORMAL_REPEATS)):
            raise ContractError(f"operating point lacks five repeats: {group[0]}")
        for field in ("qps", "latency_p95_us"):
            values = [float(r[field]) for r in group]
            mean = statistics.fmean(values)
            cv = statistics.pstdev(values) / mean if mean else math.inf
            if cv > 0.05:
                raise ContractError(
                    f"repeat variation >5% for {group[0]['method']} {group[0]['search_param']} "
                    f"{field}: CV={cv:.3f}; investigate and rerun"
                )
        row = dict(group[0])
        for field in NUMERIC_FIELDS:
            values = [float(r[field]) for r in group if r.get(field, "") not in (None, "")]
            if values:
                row[field] = float(statistics.median(values))
        row["repeat_id"] = "median_of_5"
        result.append(row)
    return result


def _pareto(rows: list[dict[str, Any]], yfield: str, maximize: bool) -> list[dict[str, Any]]:
    by_recall: dict[float, dict[str, Any]] = {}
    for row in rows:
        recall = round(float(row["recall"]), 10)
        current = by_recall.get(recall)
        if current is None or ((float(row[yfield]) > float(current[yfield])) if maximize else (float(row[yfield]) < float(current[yfield]))):
            by_recall[recall] = row
    ordered = sorted(by_recall.values(), key=lambda row: float(row["recall"]), reverse=True)
    frontier = []
    best = -math.inf if maximize else math.inf
    for row in ordered:
        value = float(row[yfield])
        better = value > best if maximize else value < best
        if better:
            frontier.append(row)
            best = value
    return list(reversed(frontier))


def _at_recall(rows: list[dict[str, Any]], field: str, target: float = 0.95) -> float | None:
    points = sorted((float(r["recall"]), float(r[field])) for r in rows)
    for recall, value in points:
        if abs(recall - target) < 1e-12:
            return value
    for (r0, y0), (r1, y1) in zip(points, points[1:]):
        if r0 < target < r1:
            weight = (target - r0) / (r1 - r0)
            return y0 + weight * (y1 - y0)
    return None


def _save(fig, fig_dir: Path, stem: str) -> None:
    fig_dir.mkdir(parents=True, exist_ok=True)
    for ext, kwargs in (
        ("svg", {}),
        ("pdf", {}),
        ("png", {"dpi": 300}),
        ("tiff", {"dpi": 600}),
    ):
        fig.savefig(fig_dir / f"{stem}.{ext}", bbox_inches="tight", **kwargs)
    plt.close(fig)


def _style(method: str, index: int) -> dict[str, Any]:
    ours = method.startswith("Ours")
    return {
        "color": COLORS.get(method, f"C{index}"),
        "marker": MARKERS[index % len(MARKERS)],
        "linestyle": LINESTYLES[index % len(LINESTYLES)],
        "linewidth": 1.8 if ours else 1.1,
        "markersize": 3.6 if ours else 2.8,
        "zorder": 5 if ours else 2,
    }


def _curve_figure(
    rows: list[dict[str, Any]],
    methods: tuple[str, ...],
    *,
    yfield: str,
    ylabel: str,
    maximize: bool,
    logy: bool = False,
    xlim: tuple[float, float] | None = None,
) -> Any:
    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    for index, method in enumerate(methods):
        method_rows = [r for r in rows if r["method"] == method]
        points = _pareto(method_rows, yfield, maximize)
        if not points:
            continue
        ax.plot(
            [float(r["recall"]) for r in points],
            [float(r[yfield]) for r in points],
            label=method,
            **_style(method, index),
        )
    ax.set_xlabel("Recall@10")
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _: f"{value:g}"))
    if xlim:
        ax.set_xlim(*xlim)
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    ax.legend(loc="best", handlelength=2.1)
    fig.tight_layout()
    return fig


def _plot_05a(run_root: Path, dataset: str, rows: list[dict[str, Any]]) -> None:
    fig_dir = run_root / LAYER_DIRS["05a"] / dataset / "figures"
    latency_rows = [r for r in rows if int(r["workers"]) == 1]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.65), sharey=True)
    for axis, mode in zip(axes, ("resident", "payload_on_ssd")):
        for index, method in enumerate(LAYER_METHODS["05a"]):
            sub = [r for r in latency_rows if r["method"] == method and r["storage_mode"] == mode]
            points = _pareto(sub, "qps", True)
            axis.plot(
                [float(r["fixed_candidate_recall_at_10"]) for r in points],
                [float(r["qps"]) for r in points],
                label=method,
                **_style(method, index),
            )
        axis.set_xlabel("Fixed-candidate Recall@10")
        axis.set_title("Resident codes" if mode == "resident" else "Payload on SSD")
        axis.set_yscale("log")
        axis.grid(axis="y", color=GRID, linewidth=0.55)
    axes[0].set_ylabel("QPS")
    axes[1].legend(loc="best", handlelength=2.0)
    fig.tight_layout()
    _save(fig, fig_dir, "4bit_recall_qps")

    # Numerical error is storage-invariant; resident rows are the canonical copy.
    error_rows = []
    for method in LAYER_METHODS["05a"]:
        candidates = [r for r in latency_rows if r["method"] == method and r["storage_mode"] == "resident"]
        error_rows.append(max(candidates, key=lambda r: float(r["search_width"])))
    x = range(len(error_rows))
    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    ax.bar([v - 0.18 for v in x], [float(r["mean_relative_error"]) for r in error_rows], 0.36, label="Mean")
    ax.bar([v + 0.18 for v in x], [float(r["p95_relative_error"]) for r in error_rows], 0.36, label="P95", alpha=0.65)
    ax.set_xticks(list(x), [r["method"] for r in error_rows], rotation=22, ha="right")
    ax.set_ylabel("Relative distance error")
    ax.set_yscale("log")
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    ax.legend()
    fig.tight_layout()
    _save(fig, fig_dir, "4bit_quantization_error")


def _plot_05b(run_root: Path, dataset: str, rows: list[dict[str, Any]]) -> None:
    fig_dir = run_root / LAYER_DIRS["05b"] / dataset / "figures"
    full_variant = "db1+coalescing+reuse"
    primary = [
        r for r in rows
        if r.get("ablation", "") in ("", full_variant)
        and float(r["search_dram_budget_gib"]) == 2.0
        and r.get("cache_mode") == "standard"
    ]
    qps_rows = [r for r in primary if int(r["workers"]) == 16]
    latency_rows = [r for r in primary if int(r["workers"]) == 1]
    _save(
        _curve_figure(qps_rows, LAYER_METHODS["05b"], yfield="qps", ylabel="QPS (16 workers)", maximize=True, logy=True),
        fig_dir,
        "diskann_vs_rabitq_recall_qps_logy",
    )
    _save(
        _curve_figure(latency_rows, LAYER_METHODS["05b"], yfield="latency_p95_us", ylabel="P95 latency (µs)", maximize=False, logy=True),
        fig_dir,
        "diskann_vs_rabitq_recall_p95latency_logy",
    )

    labels, sectors, colors = [], [], []
    for method in LAYER_METHODS["05b"]:
        sub = [r for r in latency_rows if r["method"] == method]
        value = _at_recall(sub, "sectors_4k_per_query", 0.95)
        if value is None:
            continue
        labels.append(method)
        sectors.append(value)
        colors.append(COLORS[method])
    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    ax.bar(range(len(labels)), sectors, color=colors, width=0.68)
    ax.set_xticks(range(len(labels)), labels, rotation=22, ha="right")
    ax.set_ylabel("4-KiB sectors/query at R@10=0.95")
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    fig.tight_layout()
    _save(fig, fig_dir, "diskann_io_at_95recall")

    ours_rows = [
        r for r in rows
        if r["method"] == "Ours-Disk"
        and int(r["workers"]) == 1
        and float(r["search_dram_budget_gib"]) == 2.0
        and r.get("cache_mode") == "standard"
    ]
    ablations = (
        "full4-resident/no-gate",
        "db1-resident/full4-on-ssd",
        "db1+coalescing",
        "db1+coalescing+reuse",
    )
    ablation_values = []
    for ablation in ablations:
        sub = [r for r in ours_rows if r.get("ablation") == ablation]
        value = _at_recall(sub, "latency_p95_us", 0.95)
        ablation_values.append(value)
    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    available = [(name, value) for name, value in zip(ablations, ablation_values) if value is not None]
    ax.plot(
        range(len(available)),
        [value for _, value in available],
        color=COLORS["Ours-Disk"],
        marker="o",
        linewidth=1.5,
    )
    ax.set_xticks(range(len(available)), [name for name, _ in available], rotation=24, ha="right")
    ax.set_ylabel("P95 latency (µs) at R@10=0.95")
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    fig.tight_layout()
    _save(fig, fig_dir, "ours_disk_ablation_at_95recall")

    if dataset == "gist":
        _plot_gist_sensitivity(fig_dir, rows, LAYER_METHODS["05b"])


def _plot_05c(run_root: Path, dataset: str, rows: list[dict[str, Any]]) -> None:
    fig_dir = run_root / LAYER_DIRS["05c"] / dataset / "figures"
    primary = [
        r for r in rows
        if float(r["search_dram_budget_gib"]) == 2.0
        and r.get("cache_mode") == "standard"
    ]
    qps_rows = [r for r in primary if int(r["workers"]) == 16]
    latency_rows = [r for r in primary if int(r["workers"]) == 1]
    _save(
        _curve_figure(qps_rows, LAYER_METHODS["05c"], yfield="qps", ylabel="QPS (16 workers)", maximize=True),
        fig_dir,
        "system_qps_recall",
    )
    _save(
        _curve_figure(latency_rows, LAYER_METHODS["05c"], yfield="latency_p95_us", ylabel="P95 latency (µs)", maximize=False),
        fig_dir,
        "system_p95latency_recall",
    )
    _save(
        _curve_figure(qps_rows, LAYER_METHODS["05c"], yfield="qps", ylabel="QPS (16 workers)", maximize=True, logy=True, xlim=(0.8, 1.0)),
        fig_dir,
        "system_qps_recall_logy_highrecall",
    )
    _save(
        _curve_figure(latency_rows, LAYER_METHODS["05c"], yfield="latency_p95_us", ylabel="P95 latency (µs)", maximize=False, logy=True, xlim=(0.8, 1.0)),
        fig_dir,
        "system_p95latency_recall_logy_highrecall",
    )

    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    labels, qps, memory, colors = [], [], [], []
    for method in LAYER_METHODS["05c"]:
        sub = _pareto([r for r in qps_rows if r["method"] == method], "qps", True)
        value = _at_recall(sub, "qps", 0.95)
        if value is None:
            continue
        labels.append(method)
        qps.append(value)
        memory.append(float(sub[0]["resident_bytes"]) / (1 << 30))
        colors.append(COLORS[method])
    ax.scatter(memory, qps, c=colors, s=28)
    for x, y, label in zip(memory, qps, labels):
        ax.annotate(label, (x, y), xytext=(3, 3), textcoords="offset points", fontsize=5.7)
    ax.set_xlabel("Resident index memory (GiB)")
    ax.set_ylabel("QPS at Recall@10 = 0.95")
    ax.set_yscale("log")
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    fig.tight_layout()
    _save(fig, fig_dir, "system_qps_memory_at_95recall")
    if dataset == "gist":
        _plot_gist_sensitivity(fig_dir, rows, LAYER_METHODS["05c"])


def _plot_gist_sensitivity(
    fig_dir: Path,
    rows: list[dict[str, Any]],
    methods: tuple[str, ...],
) -> None:
    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    for index, method in enumerate(methods):
        points = []
        for budget in (1.0, 2.0, 4.0):
            sub = [
                r for r in rows
                if r["method"] == method
                and int(r["workers"]) == 1
                and float(r["search_dram_budget_gib"]) == budget
                and r.get("cache_mode") == "standard"
                and r.get("ablation", "") in ("", "db1+coalescing+reuse")
            ]
            value = _at_recall(sub, "latency_p95_us", 0.95)
            if value is not None:
                points.append((budget, value))
        if points:
            ax.plot(
                [x for x, _ in points],
                [y for _, y in points],
                label=method,
                **_style(method, index),
            )
    ax.set_xlabel("Search DRAM budget (GiB)")
    ax.set_ylabel("P95 latency (µs) at R@10=0.95")
    ax.set_xticks((1, 2, 4))
    ax.set_yscale("log")
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    ax.legend(loc="best")
    fig.tight_layout()
    _save(fig, fig_dir, "gist_dram_budget_sensitivity")

    fig, ax = plt.subplots(figsize=(3.5, 2.55))
    for index, method in enumerate(methods):
        points = []
        for workers in (1, 4, 8, 16, 32):
            sub = [
                r for r in rows
                if r["method"] == method
                and int(r["workers"]) == workers
                and float(r["search_dram_budget_gib"]) == 2.0
                and r.get("cache_mode") == "standard"
                and r.get("ablation", "") in ("", "db1+coalescing+reuse")
            ]
            value = _at_recall(sub, "qps", 0.95)
            if value is not None:
                points.append((workers, value))
        if points:
            ax.plot(
                [x for x, _ in points],
                [y for _, y in points],
                label=method,
                **_style(method, index),
            )
    ax.set_xlabel("Query workers")
    ax.set_ylabel("QPS at R@10=0.95")
    ax.set_xticks((1, 4, 8, 16, 32))
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.grid(axis="y", color=GRID, linewidth=0.55)
    ax.legend(loc="best")
    fig.tight_layout()
    _save(fig, fig_dir, "gist_worker_scaling")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--layers", default="05a,05b,05c")
    parser.add_argument("--datasets", default="agnews,gist,dbpedia")
    args = parser.parse_args(argv)
    try:
        layers = tuple(x.strip() for x in args.layers.split(",") if x.strip())
        datasets = tuple(x.strip() for x in args.datasets.split(",") if x.strip())
        _validate_run_manifests(args.run_root.resolve())
        for layer in layers:
            for dataset in datasets:
                rows = _load_complete(args.run_root.resolve(), layer, dataset)
                rows = _selected_rows(args.run_root.resolve(), layer, dataset, rows)
                rows = _median_rows(rows)
                aggregate = args.run_root / LAYER_DIRS[layer] / dataset / "aggregate" / "formal_test_median.csv"
                atomic_write_csv(aggregate, rows)
                if layer == "05a":
                    _plot_05a(args.run_root, dataset, rows)
                elif layer == "05b":
                    _plot_05b(args.run_root, dataset, rows)
                elif layer == "05c":
                    _plot_05c(args.run_root, dataset, rows)
        return 0
    except (ContractError, KeyError, ValueError) as exc:
        print(f"ERROR: refusing to plot invalid/incomplete 05 data: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
