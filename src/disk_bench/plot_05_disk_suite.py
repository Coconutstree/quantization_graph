"""Nature-style figures for complete, contract-validated 05 formal runs.

This script reads only ``<experiment>/<dataset>/<run-id>/tables/formal_test_rows.csv``.
Legacy smoke JSONL, memory-only 03 rows and invalid single-run measurements are
rejected by construction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
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

from diskfair.layout import (LAYER_DIRS, LAYOUT_VERSION, normalize_layers, validate_run_id,
                            run_metadata_root, dataset_run_root, memory_experiment,
                            run_experiment, experiment_directory)

from diskfair.results_paths import resolve_result_path

PUBLISH_LAYER_DIRS = LAYER_DIRS

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
    "AiSAQ-Disk": "#009E73",
    "Starling-Disk": "#CC79A7",
}
MARKERS = ("o", "s", "D", "^", "v")
LINESTYLES = ("-", "--", "-.", ":", (0, (3, 1, 1, 1)))
GRID = "#DDE1E6"
AXIS = "#7A7F87"
MUTED = "#62666D"

DISPLAY_LABELS = {
    "PQ_4bit": "PQ",
    "SQ_4bit": "SQ",
    "SAQ_B4": "SAQ",
    "Ours_RaBitQ_K1": "Ours",
    "PQ-DiskANN-Disk": "PQ",
    "SQ-DiskANN-Disk": "SQ",
    "SAQ-DiskANN-Disk": "SAQ",
    "Ours-Disk": "Ours",
    "SymphonyQG-DiskPort": "SymphonyQG (our disk adaptation)",
    "OG-LVQ-DiskPort": "OG-LVQ",
    "Glass-NSG-DiskPort": "Glass-NSG",
    "DiskANN-PQ-Disk": "DiskANN-PQ",
    "AiSAQ-Disk": "AiSAQ",
    "Starling-Disk": "Starling",
}

PRIMARY_FIGURE_STEMS = {
    "05a": "disk05a_quantizer_fair_summary",
    "05b": "disk05b_shared_graph_recall_qps",
    "05c": "disk05c_system_recall_qps",
}

FIGURE_EXTENSIONS = (".svg", ".pdf", ".png", ".tiff")

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
    "pairwise_flip_rate", "code_bytes_per_vector", "effective_bits_per_dim",
    "read_amplification", "index_size_mb", "resident_bytes", "cache_bytes",
    "cache_nodes", "peak_rss_bytes", "io_requests_per_query",
    "sectors_4k_per_query", "bytes_read_per_query", "io_wait_us",
    "distance_compute_us", "query_prep_us", "queue_compute_us", "rerank_us",
    "visited_nodes", "distance_evaluations", "db1_checks", "db1_survivors",
    "full4_candidates", "full4_page_reads", "rerank_candidates",
    "rerank_page_reads", "query_count", "qps_iqr", "qps_cv",
    "latency_p95_us_iqr", "latency_p95_us_cv",
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
    return (32,)


def _memory_condition_label(rows):
    budgets = sorted({float(r['search_dram_budget_gib']) for r in rows
                      if r.get('storage_mode') != 'resident'})
    if len(budgets) == 1:
        return f"RAM budget {budgets[0]:g} GiB; measured RSS (no OS cap)"
    return "planned RAM budgets; measured RSS (no OS cap)"


def _load_complete(run_root: Path, layer: str, dataset: str) -> list[dict[str, Any]]:
    from diskfair.protocol import PROTOCOL_ID
    from diskfair.native_contract import SPECS_BY_KEY, validate_artifact, flatten_artifact
    base = dataset_run_root(run_root, layer, dataset) / "tables"
    csv_path = base / "formal_test_rows.csv"
    manifest_path = csv_path.with_suffix(".manifest.json")
    if not csv_path.exists() or not manifest_path.exists():
        raise ContractError(f"missing aggregate or manifest: {csv_path}")
    manifest = json.loads(manifest_path.read_text())
    experiment = experiment_directory(layer, run_root)
    if manifest.get("experiment", LAYER_DIRS[layer]) != experiment:
        raise ContractError("aggregate belongs to a different public experiment")
    if manifest.get("protocol_id") != PROTOCOL_ID or manifest.get("formal_ready") is not True:
        raise ContractError("legacy/diagnostic results cannot enter new formal plots")
    if manifest.get("raw_csv_sha256") != sha256_file(csv_path):
        raise ContractError("aggregate CSV hash mismatch")
    sources = manifest.get("source_artifacts", [])
    if not sources:
        raise ContractError("source artifact evidence is missing")
    artifact_paths = set()
    source_rows = []
    for source in sources:
        path = resolve_result_path(source["path"])
        if not path.exists() or sha256_file(path) != source["sha256"]:
            raise ContractError(f"source artifact changed or disappeared: {path}")
        artifact = json.loads(path.read_text())
        if artifact.get("experiment", LAYER_DIRS[layer]) != experiment:
            raise ContractError("source artifact belongs to a different public experiment")
        validate_artifact(path, spec=SPECS_BY_KEY[f"{layer}:{artifact['method']}"],
                          expected=dict(artifact, dataset=dataset, layer=layer, phase="test", protocol_id=PROTOCOL_ID),
                          disk_root=Path(json.loads((run_root / 'protocol.json').read_text())["disk_root"]))
        artifact_paths.add(str(path))
        source_rows.extend(flatten_artifact(path, artifact))
    rows = _read_rows(csv_path)
    if any(row.get("experiment", LAYER_DIRS[layer]) != experiment for row in rows):
        raise ContractError("CSV belongs to a different public experiment")
    if {str(resolve_result_path(r.get("artifact_path", ""))) for r in rows} != artifact_paths:
        raise ContractError("CSV and artifact manifest disagree")
    _validate_csv_sources(rows, source_rows)
    if manifest.get("system03_protocol"):
        from diskfair.system03 import check_rows
        check_rows(rows, tuple(manifest['methods']))
    validate_layer_completeness(
        rows, layer=layer, dataset=dataset, repeats=FORMAL_REPEATS,
        workers=tuple(manifest["workers"]), budget_gib=manifest["search_dram_budget_gib"],
        cache_mode=manifest["cache_mode"], methods=manifest["methods"],
        baseline_budgets=manifest.get("baseline_native_budgets", {}),
        storage_modes=manifest["storage_modes"],
    )
    _single_rows(rows)  # Validate every point before any tuning/method filtering.
    return rows


def _validate_csv_sources(rows, sources):
    """A hashed CSV must still contain the actual admitted measurements."""
    from diskfair.native_contract import SUMMARY_FIELDS
    def key(row):
        return (str(resolve_result_path(row['artifact_path'])), str(row.get('config_id', '')),
                float(row.get('search_width') or 0), float(row.get('beam_width') or 0),
                str(row.get('ablation', '')))
    expected = {key(row): row for row in sources}
    if len(expected) != len(sources) or len(rows) != len(sources):
        raise ContractError('CSV/source row count or source uniqueness mismatch')
    for row in rows:
        original = expected.pop(key(row), None)
        if original is None:
            raise ContractError('CSV contains an unknown or duplicate operating point')
        for field in SUMMARY_FIELDS:
            if field == 'artifact_path':
                continue
            value, actual = original.get(field), row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    same = float(actual) == float(value)
                except (TypeError, ValueError):
                    same = False
            else:
                same = str('' if actual is None else actual) == str('' if value is None else value)
            if not same:
                raise ContractError(f'CSV differs from admitted source: {field}')


def _validate_run_manifests(run_root: Path) -> None:
    from diskfair.protocol import PROTOCOL_ID
    config_path = run_root / "protocol.json"
    if not config_path.exists() or json.loads(config_path.read_text()).get("protocol_id") != PROTOCOL_ID:
        raise ContractError("run protocol manifest missing or legacy")
    preflight_path = run_root / "preflight.json"
    fio_path = run_root / "fio_preflight.json"
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
    lock_path = dataset_run_root(run_root, layer, dataset) / "manifests" / "tuning.lock.json"
    if any(row.get('system03_protocol') for row in rows):
        lock_path = lock_path.with_name('fixed_parameters.lock.json')
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


def _single_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from diskfair.protocol import aggregate_repeats
    return aggregate_repeats(rows)


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
    if not points:
        return None
    for recall, value in points:
        if abs(recall - target) < 1e-12:
            return value
    for (r0, y0), (r1, y1) in zip(points, points[1:]):
        if r0 < target < r1:
            weight = (target - r0) / (r1 - r0)
            return y0 + weight * (y1 - y0)
    return None


def _at_x(
    rows: list[dict[str, Any]],
    *,
    xfield: str,
    yfield: str,
    target: float = 0.95,
    logy: bool = False,
) -> float | None:
    points = sorted(
        (float(r[xfield]), float(r[yfield]))
        for r in rows
        if r.get(xfield, "") not in (None, "") and r.get(yfield, "") not in (None, "")
    )
    if not points:
        return None
    if target <= points[0][0]:
        return points[0][1]
    if target >= points[-1][0]:
        return points[-1][1] if abs(target - points[-1][0]) < 1e-12 else None
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= target <= x1:
            weight = (target - x0) / (x1 - x0)
            if logy and y0 > 0 and y1 > 0:
                return math.exp(math.log(y0) + (math.log(y1) - math.log(y0)) * weight)
            return y0 + weight * (y1 - y0)
    return None


def _style_log_y(ax: Any) -> None:
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _: f"{value:g}"))
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())


def _panel_label(ax: Any, label: str) -> None:
    ax.text(
        -0.18,
        1.04,
        label,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=8,
        fontweight="bold",
    )


def _display_label(method: str) -> str:
    return DISPLAY_LABELS.get(method, method)


def _add_figure_legend(fig: Any, handles: list[Any], labels: list[str], ncol: int) -> None:
    if handles:
        fig.legend(
            handles,
            labels,
            loc="lower center",
            ncol=ncol,
            frameon=False,
            handlelength=2.2,
            columnspacing=1.2,
        )


def _clean_figures(fig_dir: Path) -> None:
    fig_dir.mkdir(parents=True, exist_ok=True)
    for path in fig_dir.iterdir():
        if path.is_file() and path.suffix.lower() in FIGURE_EXTENSIONS:
            path.unlink()


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


def _save_to_dataset_dirs(
    fig: Any,
    run_root: Path,
    layer: str,
    datasets: tuple[str, ...],
    stem: str,
    *,
    published_layout: bool = False,
) -> None:
    layer_dir = PUBLISH_LAYER_DIRS[layer] if published_layout else LAYER_DIRS[layer]
    for dataset in datasets:
        fig_dir = dataset_run_root(run_root, layer, dataset) / "figures"
        _clean_figures(fig_dir)
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


def _plot_05a_summary(
    run_root: Path,
    datasets: tuple[str, ...],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    *,
    published_layout: bool = False,
    workers: int = 32,
) -> None:
    methods = tuple(m for m in LAYER_METHODS["05a"] if any(
        any(row["method"] == m for row in rows_by_dataset.get(dataset, []))
        for dataset in datasets
    ))
    if not methods:
        return

    fig, axes = plt.subplots(2, len(datasets), figsize=(7.2, 4.15), squeeze=False)
    x = list(range(len(methods)))
    for col, dataset in enumerate(datasets):
        label = DATASET_LABELS.get(dataset, dataset)
        rows = rows_by_dataset[dataset]
        one_worker = [r for r in rows if int(r["workers"]) == workers]

        ax = axes[0, col]
        qps_values = []
        colors = []
        for index, method in enumerate(methods):
            sub = [
                r for r in one_worker
                if r["method"] == method and r["storage_mode"] == "payload_on_ssd"
            ]
            qps_values.append(_at_x(
                sub,
                xfield="fixed_candidate_recall_at_10",
                yfield="qps",
                target=0.95,
                logy=True,
            ))
            colors.append(COLORS.get(method, f"C{index}"))
        for xi, method, value, color in zip(x, methods, qps_values, colors):
            if value is None:
                ax.text(xi, 1.0, "N/A", ha="center", va="bottom", fontsize=6, color=MUTED)
                continue
            alpha = 1.0 if method.startswith("Ours") else 0.55
            ax.bar(xi, value, color=color, alpha=alpha, width=0.62, zorder=3)
            ax.text(xi, value * 1.15, f"{value:,.0f}", ha="center", va="bottom", fontsize=5.8)
        positive_qps = [v for v in qps_values if v is not None and v > 0]
        if positive_qps:
            ax.set_ylim(min(positive_qps) * 0.65, max(positive_qps) * 2.0)
        _style_log_y(ax)
        ax.set_title(f"{label} · payload on disk", fontsize=7.5, pad=4)
        ax.set_xticks(x)
        ax.set_xticklabels([_display_label(m) for m in methods], fontsize=6)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, linewidth=0.55, zorder=0)
        _panel_label(ax, chr(ord("a") + col))

        ax = axes[1, col]
        error_rows = []
        for method in methods:
            candidates = [
                r for r in one_worker
                if r["method"] == method and r["storage_mode"] == "resident"
            ]
            if candidates:
                error_rows.append(max(candidates, key=lambda r: float(r["search_width"])))
            else:
                error_rows.append(None)
        width = 0.34
        for xi, method, row, color in zip(x, methods, error_rows, colors):
            if row is None:
                continue
            alpha = 1.0 if method.startswith("Ours") else 0.55
            ax.bar(
                xi - width / 2,
                float(row["mean_relative_error"]),
                width,
                color=color,
                alpha=alpha,
                zorder=3,
                label="Mean" if xi == 0 and col == 0 else None,
            )
            ax.bar(
                xi + width / 2,
                float(row["p95_relative_error"]),
                width,
                color=color,
                alpha=alpha,
                hatch="//",
                edgecolor="white",
                linewidth=0.4,
                zorder=3,
                label="P95" if xi == 0 and col == 0 else None,
            )
        _style_log_y(ax)
        ax.set_xticks(x)
        ax.set_xticklabels([_display_label(m) for m in methods], fontsize=6)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, linewidth=0.55, zorder=0)
        _panel_label(ax, chr(ord("a") + len(datasets) + col))

    axes[0, 0].set_ylabel("QPS @ fixed-candidate\nR@10=0.95", fontsize=7)
    axes[1, 0].set_ylabel("Relative distance error", fontsize=7)
    for ax in axes[1, :]:
        ax.set_xlabel("4-bit encoder", fontsize=7)
    handles, labels = axes[1, 0].get_legend_handles_labels()
    _add_figure_legend(fig, handles, labels, ncol=2)
    fig.text(
        0.5,
        0.055,
        "Top: compressed payload fetched through the 4-KiB disk-page layout. Bottom: storage-invariant quantization error.",
        ha="center",
        va="bottom",
        fontsize=5.7,
        color=MUTED,
    )
    fig.tight_layout(rect=(0.02, 0.10, 1, 1), h_pad=1.0, w_pad=0.9)
    _save_to_dataset_dirs(
        fig,
        run_root,
        "05a",
        datasets,
        PRIMARY_FIGURE_STEMS["05a"],
        published_layout=published_layout,
    )


def _primary_05b_rows(
    rows: list[dict[str, Any]], workers: int = 32
) -> list[dict[str, Any]]:
    full_variant = "db1+coalescing+reuse"
    ours_ablations = {full_variant}
    if os.environ.get("QG05_FAST") == "1":
        ours_ablations.update(
            item for item in os.environ.get("QG05_OURS_ABLATIONS", "").split(",") if item
        )
    primary = [
        r for r in rows
        if int(r["workers"]) == workers
    ]
    baselines = [r for r in primary if r["method"] != "Ours-Disk" and r.get("ablation", "") == ""]
    ours = [r for r in primary if r["method"] == "Ours-Disk" and r.get("ablation", "") in ours_ablations]
    if not ours:
        ours = [r for r in primary if r["method"] == "Ours-Disk"]
    return baselines + ours


def _plot_05b_summary(
    run_root: Path,
    datasets: tuple[str, ...],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    *,
    published_layout: bool = False,
    workers: int = 32,
) -> None:
    _plot_recall_qps_grid(
        run_root,
        "05b",
        datasets,
        {dataset: _primary_05b_rows(rows, workers) for dataset, rows in rows_by_dataset.items()},
        methods=LAYER_METHODS["05b"],
        stem=PRIMARY_FIGURE_STEMS["05b"],
        xlabel="Recall@10 (shared graph, 4-KiB disk pages)",
        ylabel=f"QPS ({workers} workers)",
        logy=True,
        published_layout=published_layout,
    )


def _primary_05c_rows(
    rows: list[dict[str, Any]], workers: int = 32
) -> list[dict[str, Any]]:
    return [r for r in rows if int(r["workers"]) == workers]


def _plot_05c_summary(
    run_root: Path,
    datasets: tuple[str, ...],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    *,
    published_layout: bool = False,
    workers: int = 32,
) -> None:
    _plot_recall_qps_grid(
        run_root,
        "05c",
        datasets,
        {dataset: _primary_05c_rows(rows, workers) for dataset, rows in rows_by_dataset.items()},
        methods=(tuple(m for m in ("Ours-Disk", "DiskANN-PQ-Disk", "Starling-Disk", "AiSAQ-Disk")
                       if any(r['method'] == m for rows in rows_by_dataset.values() for r in rows))
                 if any(r.get('system03_protocol') for rows in rows_by_dataset.values() for r in rows)
                 else LAYER_METHODS["05c"]),
        stem=PRIMARY_FIGURE_STEMS["05c"],
        xlabel="Recall@10 (end-to-end disk search)",
        ylabel=f"QPS ({workers} workers)",
        logy=True,
        published_layout=published_layout,
    )


def _plot_recall_qps_grid(
    run_root: Path,
    layer: str,
    datasets: tuple[str, ...],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    *,
    methods: tuple[str, ...],
    stem: str,
    xlabel: str,
    ylabel: str,
    logy: bool,
    published_layout: bool = False,
) -> None:
    fig, axes = plt.subplots(
        len(datasets),
        1,
        figsize=(7.2, max(2.6, 1.95 * len(datasets))),
        sharex=False,
        squeeze=False,
    )
    legend_handles: list[Any] = []
    legend_labels: list[str] = []
    for row_index, dataset in enumerate(datasets):
        ax = axes[row_index, 0]
        dataset_rows = rows_by_dataset.get(dataset, [])
        if not dataset_rows:
            raise ContractError(f"no admitted rows to plot for {layer}/{dataset}")
        plotted_recalls: list[float] = []
        for method_index, method in enumerate(methods):
            sub = [r for r in dataset_rows if r["method"] == method and float(r["recall"]) > 0]
            points = _pareto(sub, "qps", True)
            if not points:
                continue
            plotted_recalls.extend(float(r["recall"]) for r in points)
            line = ax.plot(
                [float(r["recall"]) for r in points],
                [float(r["qps"]) for r in points],
                label=_display_label(method),
                **_style(method, method_index),
            )[0]
            if row_index == 0:
                legend_handles.append(line)
                legend_labels.append(_display_label(method))
            near95 = min(points, key=lambda r: abs(float(r["recall"]) - 0.95))
            if method.startswith("Ours"):
                ax.plot(
                    [float(near95["recall"])],
                    [float(near95["qps"])],
                    marker="*",
                    markersize=7.5,
                    color=COLORS.get(method, "C0"),
                    markeredgecolor="white",
                    markeredgewidth=0.6,
                    zorder=7,
                    clip_on=False,
                )
        ax.axvline(0.95, color=AXIS, linestyle=":", linewidth=0.8, zorder=0)
        if plotted_recalls:
            ax.set_xlim(max(0.0, min(plotted_recalls) - 0.02), min(1.0, max(plotted_recalls) + 0.01))
        ax.text(
            0.01,
            0.94,
            (f"{DATASET_LABELS.get(dataset, dataset)} | "
             f"{_memory_condition_label(dataset_rows)} | "
             f"cache={dataset_rows[0]['cache_mode']} | single run"),
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=7.5,
            fontweight="bold",
        )
        if logy:
            _style_log_y(ax)
        ax.set_ylabel(ylabel, fontsize=7)
        ax.set_xlabel(xlabel, fontsize=7)
        ax.tick_params(labelsize=6.5)
        ax.grid(axis="y", color=GRID, linewidth=0.55, zorder=0)
        _panel_label(ax, chr(ord("a") + row_index))
    _add_figure_legend(fig, legend_handles, legend_labels, ncol=min(5, max(1, len(legend_handles))))
    fig.tight_layout(rect=(0.02, 0.10, 0.99, 1), h_pad=1.05)
    _save_to_dataset_dirs(
        fig,
        run_root,
        layer,
        datasets,
        stem,
        published_layout=published_layout,
    )


def _load_public_rows(public_root: Path, layer: str, dataset: str) -> list[dict[str, Any]]:
    raise ContractError("published CSV alone is insufficient; plot from --run-root with immutable evidence")


def _plot_layer_summary(
    run_root: Path,
    layer: str,
    datasets: tuple[str, ...],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
    *,
    published_layout: bool = False,
    workers: int = 32,
) -> None:
    if layer == "05a":
        _plot_05a_summary(
            run_root, datasets, rows_by_dataset,
            published_layout=published_layout, workers=workers,
        )
    elif layer == "05b":
        _plot_05b_summary(
            run_root, datasets, rows_by_dataset,
            published_layout=published_layout, workers=workers,
        )
    elif layer == "05c":
        _plot_05c_summary(
            run_root, datasets, rows_by_dataset,
            published_layout=published_layout, workers=workers,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, help="<results>/manifests/<run-id>")
    parser.add_argument("--results-root", type=Path, default=Path(__file__).resolve().parents[2] / "results")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--public-root",
        type=Path,
        help="retired; use --results-root and --run-id",
    )
    parser.add_argument("--layers", default="01,02,03")
    parser.add_argument("--datasets", default="agnews,gist,dbpedia")
    parser.add_argument("--methods", default="", help="optional comma-separated method filter")
    parser.add_argument(
        "--workers",
        type=int,
        default=32,
        help="worker count to select for the primary figures (default 32)",
    )
    args = parser.parse_args(argv)
    try:
        if args.public_root:
            raise ContractError("published copies are retired; use --results-root and --run-id")
        if args.run_id:
            if args.run_root:
                raise ContractError("choose --run-root or --results-root/--run-id")
            args.run_root = run_metadata_root(args.results_root.resolve(), args.run_id)
        layers = normalize_layers(args.layers)
        datasets = tuple(x.strip() for x in args.datasets.split(",") if x.strip())
        methods = tuple(x.strip() for x in args.methods.split(",") if x.strip())
        if bool(args.run_root) == bool(args.public_root):
            raise ContractError("provide --run-id (with optional --results-root) or --run-root")
        if args.public_root and methods:
            raise ContractError("--methods is only supported with --run-root")
        published_layout = args.public_root is not None
        root = (args.public_root or args.run_root).resolve()
        if memory_experiment(args.layers) != run_experiment(root):
            raise ContractError("public experiment does not match this run; use --layers 05 for memory budgets")
        if not published_layout:
            _validate_run_manifests(root)
        for layer in layers:
            rows_by_dataset = {}
            for dataset in datasets:
                if published_layout:
                    rows = _load_public_rows(root, layer, dataset)
                else:
                    rows = _load_complete(root, layer, dataset)
                if methods:
                    rows = [row for row in rows if row["method"] in methods]
                # 05A evaluates the full fixed-candidate sweep as its result;
                # only 05B/05C select a beam config from the tuning lock.
                if not published_layout:
                    rows = rows if layer == "05a" else _selected_rows(root, layer, dataset, rows)
                    rows = _single_rows(rows)
                    # Keep formal_test_rows.csv intact; tuning selection is a plotting view.
                # Different methods may use different native memory parameters.
                # Still reject mixing two configurations of the same method.
                conditions = {}
                for row in rows:
                    conditions.setdefault(row["method"], set()).add(
                        (row["search_dram_budget_gib"], row["cache_mode"]))
                if any(len(values) != 1 for values in conditions.values()):
                    raise ContractError("plot requires one native budget/cache configuration per method")
                rows_by_dataset[dataset] = rows
            _plot_layer_summary(
                root,
                layer,
                datasets,
                rows_by_dataset,
                published_layout=published_layout,
                workers=args.workers,
            )
        return 0
    except (ContractError, KeyError, ValueError) as exc:
        print(f"ERROR: refusing to plot invalid/incomplete 05 data: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
