"""Formal orchestrator for 05A/05B/05C.

Only native ports that preserve the 01/02/03 implementation are executable
through this module. The old NumPy reference runners are deliberately not
imported: they are format prototypes, not a valid source of paper data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if "diskfair" not in sys.modules:
    import types

    _pkg = types.ModuleType("diskfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules["diskfair"] = _pkg

from diskfair.common import (  # noqa: E402
    DATA_ROOT,
    RESULTS_ROOT,
    SEED,
    dataset_paths,
    hardware_info,
    prepare_query_splits,
    run_preflight,
)
from diskfair.native_contract import (  # noqa: E402
    FORMAL_REPEATS,
    FORMAL_WARMUP_QUERIES,
    LAYER_METHODS,
    ContractError,
    MethodSpec,
    atomic_write_csv,
    atomic_write_json,
    flatten_artifact,
    load_port_registry,
    sha256_file,
    specs_for,
    validate_artifact,
    validate_layer_completeness,
    validate_registry,
)
from diskfair.fio_preflight import run_fio  # noqa: E402

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
LAYER_DIRS = {
    "05a": "05A_disk_quantizer_io",
    "05b": "05B_diskann_shared_graph",
    "05c": "05C_disk_system_fair",
}
PUBLISH_LAYER_DIRS = {
    "05a": "01_quantizer_fair",
    "05b": "02_diskann_fair",
    "05c": "03_system_fair",
}
_VERIFIED_INPUT_MANIFESTS: dict[Path, str] = {}
FAST_MODE = os.environ.get("QG05_FAST") == "1"


def _csv_list(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _int_list(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(dict.fromkeys(int(x) for x in _csv_list(value)))
    except ValueError as exc:
        raise ContractError(f"expected comma-separated integers, got {value!r}") from exc
    if not parsed or any(x <= 0 for x in parsed):
        raise ContractError("worker counts must be positive")
    return parsed


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-+" else "-" for c in value)


def _default_run_id() -> str:
    return time.strftime("formal_%Y%m%dT%H%M%S")


def _resolve_executable(argv0: str) -> str:
    path = Path(argv0)
    resolved = str(path) if path.is_absolute() else shutil.which(argv0)
    if not resolved or not Path(resolved).exists():
        raise ContractError(f"native port executable not found: {argv0}")
    if not os.access(resolved, os.X_OK):
        raise ContractError(f"native port is not executable: {resolved}")
    return resolved


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _formal_preflight(
    disk_root: Path,
    out_root: Path,
    run_id: str,
    disk_profile: str,
) -> dict[str, Any]:
    report = run_preflight(disk_root)
    report["hardware"] = hardware_info()
    report["disk_profile"] = disk_profile
    report["storage_medium"] = (
        "non_rotational_nvme_or_ssd"
        if disk_profile == "nvme"
        else "rotational_hdd_or_hardware_raid"
    )
    rotational_ok = (
        report.get("rotational") is False
        if disk_profile == "nvme"
        else report.get("rotational") is True
    )
    report["formal_ready"] = bool(
        report.get("odirect") is True
        and rotational_ok
        and report.get("sufficient_space_200gib") is True
    )
    report["required_io_backend"] = "O_DIRECT + native asynchronous I/O"
    report["run_id"] = run_id
    manifest_dir = out_root / "runs" / run_id / "manifests"
    atomic_write_json(manifest_dir / "preflight.json", report)
    if not report["formal_ready"]:
        expected = "ROTA=0 NVMe/SSD" if disk_profile == "nvme" else "ROTA=1 HDD/RAID"
        raise ContractError(
            f"formal {disk_profile} preflight failed: require {expected}, working O_DIRECT and "
            f">=200 GiB free under {disk_root}; see the run preflight manifest"
        )
    fio_path = manifest_dir / "fio_preflight.json"
    if fio_path.exists():
        fio_report = json.loads(fio_path.read_text())
        if fio_report.get("disk_root") != str(disk_root.resolve()) or fio_report.get("status") != "passed":
            raise ContractError(f"stale or invalid fio manifest: {fio_path}")
    else:
        try:
            fio_report = run_fio(disk_root, fio_path)
        except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ContractError(str(exc)) from exc
    report["fio_manifest"] = str(fio_path)
    report["fio_manifest_sha256"] = sha256_file(fio_path)
    atomic_write_json(manifest_dir / "preflight.json", report)
    return report


def _shared_graph(dataset: str) -> Path:
    return (
        REPO_ROOT
        / "results"
        / dataset
        / "indexes"
        / "02_diskann_fair"
        / "shared_graph"
        / "diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    )


def _ours_graph(dataset: str) -> Path:
    """The exact M=64 ExRaBitQ-symmetric graph used by experiments 02 and 03."""
    return (
        REPO_ROOT
        / "results"
        / dataset
        / "indexes"
        / "02_diskann_fair"
        / "Ours"
        / f"{dataset}_Ours_R64_Lbuild400.graph.bin"
    )


@dataclass(frozen=True)
class WorkItem:
    spec: MethodSpec
    storage_mode: str
    repeat_id: int
    workers: int
    budget_gib: float
    cache_mode: str


def _work_items(
    specs: list[MethodSpec],
    phase: str,
    workers: tuple[int, ...],
    repeats: int,
    dataset: str,
) -> list[WorkItem]:
    layer = specs[0].layer
    primary_cache = "c0" if layer == "05a" else "standard"
    if phase != "test":
        return [
            WorkItem(spec, mode, 0, 1, 2.0, primary_cache)
            for spec in specs
            for mode in spec.storage_modes
        ]
    result: list[WorkItem] = []
    for repeat_id in range(repeats):
        for wi, worker in enumerate(workers):
            shift = (repeat_id + wi) % len(specs)
            ordered = specs[shift:] + specs[:shift]
            result.extend(
                WorkItem(spec, mode, repeat_id, worker, 2.0, primary_cache)
                for spec in ordered
                for mode in spec.storage_modes
            )
    if dataset == "gist" and layer in ("05b", "05c") and not FAST_MODE:
        for budget_gib, cache_mode in ((1.0, "standard"), (4.0, "standard"), (2.0, "c0")):
            for repeat_id in range(repeats):
                shift = repeat_id % len(specs)
                ordered = specs[shift:] + specs[:shift]
                result.extend(
                    WorkItem(spec, mode, repeat_id, 1, budget_gib, cache_mode)
                    for spec in ordered
                    for mode in spec.storage_modes
                )
    return result


def _artifact_path(
    run_root: Path,
    layer: str,
    dataset: str,
    phase: str,
    item: WorkItem,
) -> Path:
    stem = (
        f"{_safe(item.spec.method)}__{_safe(item.storage_mode)}"
        f"__B{item.budget_gib:g}__{item.cache_mode}"
        f"__w{item.workers}__r{item.repeat_id}.json"
    )
    return run_root / LAYER_DIRS[layer] / dataset / "artifacts" / phase / stem


def _publish_root(out_root: Path) -> Path:
    if out_root.name == ".formal_runs" and out_root.parent.name == "disk_environment":
        return out_root.parent
    return Path(os.environ.get("QG05_PUBLISH_ROOT", str(REPO_ROOT / "results" / "disk_environment"))).resolve()


def _publish_disk_environment(run_root: Path, out_root: Path, run_id: str, layer: str, dataset: str) -> None:
    """Publish 05A/05B/05C outputs as disk_environment/01/02/03.

    The strict runner keeps immutable artifacts under .formal_runs/runs/<run-id>.
    This public copy mirrors the memory experiments' numbered layout so figures,
    CSVs and logs are easy to compare with results/memory_environment/01/02/03.
    """
    source = run_root / LAYER_DIRS[layer] / dataset
    if not source.exists():
        return
    destination = _publish_root(out_root) / PUBLISH_LAYER_DIRS[layer] / dataset
    for name in ("csv", "logs", "figures", "manifests", "artifacts", "aggregate"):
        (destination / name).mkdir(parents=True, exist_ok=True)

    aggregate = source / "aggregate"
    for csv_path in aggregate.glob("*.csv"):
        shutil.copy2(csv_path, destination / "csv" / csv_path.name)
        shutil.copy2(csv_path, destination / "csv" / f"{csv_path.stem}_{run_id}{csv_path.suffix}")
    for manifest_path in aggregate.glob("*.json"):
        shutil.copy2(manifest_path, destination / "manifests" / manifest_path.name)
        shutil.copy2(manifest_path, destination / "manifests" / f"{manifest_path.stem}_{run_id}{manifest_path.suffix}")
    for manifest_path in (source / "manifests").glob("*"):
        if manifest_path.is_file():
            shutil.copy2(manifest_path, destination / "manifests" / manifest_path.name)
    for figure in (source / "figures").glob("*"):
        if figure.is_file():
            shutil.copy2(figure, destination / "figures" / figure.name)
    for log_path in (source / "artifacts").glob("**/*.terminal.log"):
        if log_path.is_file():
            target = destination / "logs" / f"{log_path.parent.name}__{log_path.name}"
            shutil.copy2(log_path, target)


def _fvec_count(path: Path) -> int:
    with path.open("rb") as stream:
        raw = stream.read(4)
    if len(raw) != 4:
        raise ContractError(f"empty fvecs file: {path}")
    dimension = struct.unpack("<i", raw)[0]
    record_bytes = 4 * (dimension + 1)
    size = path.stat().st_size
    if dimension <= 0 or size % record_bytes:
        raise ContractError(f"malformed fvecs file: {path}")
    return size // record_bytes


def _query_order_file(artifact_path: Path, query_path: Path, seed: int) -> tuple[Path, str]:
    phase = artifact_path.parent.name
    query_hash = sha256_file(query_path)[:12]
    order_path = (
        artifact_path.parents[2]
        / "manifests"
        / f"query_order_{phase}_{query_hash}_seed{seed}.u32"
    )
    if not order_path.exists():
        order_path.parent.mkdir(parents=True, exist_ok=True)
        order = list(range(_fvec_count(query_path)))
        random.Random(seed).shuffle(order)
        with order_path.open("wb") as stream:
            for query_id in order:
                stream.write(struct.pack("<I", query_id))
    return order_path, sha256_file(order_path)


def _invoke_port(
    *,
    port: dict[str, Any],
    item: WorkItem,
    phase: str,
    dataset: str,
    run_id: str,
    data_root: Path,
    disk_root: Path,
    query_path: Path,
    gt_path: Path,
    split_sha256: str,
    artifact_path: Path,
    search_dram_budget_gib: float,
    seed: int,
    tuning_lock: Path | None,
    input_manifest: Path,
    input_manifest_sha256: str,
    candidate_row_offset: int,
) -> dict[str, Any]:
    command = [str(x) for x in port["command"]]
    command[0] = _resolve_executable(command[0])
    binary_sha256 = sha256_file(Path(command[0]))
    if binary_sha256 != port["binary_sha256"]:
        raise ContractError(
            f"native binary changed for {item.spec.key}: got {binary_sha256}, "
            f"registry pins {port['binary_sha256']}"
        )
    index_dir = (
        disk_root
        / "05_disk_system_fair"
        / LAYER_DIRS[item.spec.layer]
        / dataset
        / _safe(item.spec.method)
        / ("shared_payload" if item.spec.layer == "05a" else _safe(item.storage_mode))
    )
    index_dir.mkdir(parents=True, exist_ok=True)
    trace_path = artifact_path.with_suffix(".queries.jsonl")
    log_path = artifact_path.with_suffix(".terminal.log")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    order_seed = seed + item.repeat_id
    query_order_path, query_order_sha256 = _query_order_file(
        artifact_path, query_path, order_seed
    )
    warmup_queries = FORMAL_WARMUP_QUERIES if phase in ("validate", "validation", "test") else 0
    args = command + [
        "--contract-version", "2",
        "--phase", phase,
        "--layer", item.spec.layer,
        "--dataset", dataset,
        "--method", item.spec.method,
        "--storage-mode", item.storage_mode,
        "--data-root", str(data_root.resolve()),
        "--work-root", str((REPO_ROOT / "work").resolve()),
        "--saq-data-root", str((REPO_ROOT / "baselines" / "saq" / "data").resolve()),
        "--source-results-root", str((REPO_ROOT / "results").resolve()),
        "--input-manifest", str(input_manifest.resolve()),
        "--input-manifest-sha256", input_manifest_sha256,
        "--disk-index-dir", str(index_dir.resolve()),
        "--query", str(query_path.resolve()),
        "--groundtruth", str(gt_path.resolve()),
        "--query-split-sha256", split_sha256,
        "--query-order", str(query_order_path.resolve()),
        "--query-order-sha256", query_order_sha256,
        "--query-order-seed", str(order_seed),
        "--result-json", str(artifact_path.resolve()),
        "--query-trace", str(trace_path.resolve()),
        "--run-id", run_id,
        "--repeat-id", str(item.repeat_id),
        "--workers", str(item.workers),
        "--warmup-queries", str(warmup_queries),
        "--search-dram-budget-gib", str(item.budget_gib),
        "--cache-mode", item.cache_mode,
        "--seed", str(seed),
        "--page-size", "4096",
        "--max-inflight-io", "128",
        "--direct-io", "required",
        "--native-aio", "required",
        "--implementation-fingerprint", str(port["implementation_fingerprint"]),
        "--native-binary-sha256", binary_sha256,
        "--git-commit", _git_commit(),
        "--candidate-row-offset", str(candidate_row_offset),
    ]
    if item.spec.layer == "05b" or (
        item.spec.layer == "05c" and item.spec.method == "Ours-Disk"
    ):
        ours = item.spec.method == "Ours-Disk"
        graph = _ours_graph(dataset) if ours else _shared_graph(dataset)
        if not graph.exists():
            raise ContractError(
                f"missing formal 02 {'Ours native' if ours else 'shared baseline'} graph "
                f"for {dataset}: {graph}"
            )
        if ours:
            args += ["--ours-graph", str(graph.resolve()), "--ours-graph-sha256", sha256_file(graph)]
        else:
            args += ["--shared-graph", str(graph.resolve()), "--shared-graph-sha256", sha256_file(graph)]
    if item.spec.layer == "05b" and item.spec.method == "Ours-Disk":
        args += [
            "--ablations",
            os.environ.get(
                "QG05_OURS_ABLATIONS",
                "full4-resident/no-gate,db1-resident/full4-on-ssd,db1+coalescing,db1+coalescing+reuse",
            ),
        ]
    if tuning_lock is not None:
        args += ["--tuning-lock", str(tuning_lock.resolve())]

    with log_path.open("w") as log:
        log.write("argv=" + json.dumps(args) + "\n")
        log.flush()
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, text=True)
    if proc.returncode:
        raise ContractError(
            f"native port failed ({proc.returncode}) for {item.spec.key}; terminal log: {log_path}"
        )
    expected = {
        "dataset": dataset,
        "phase": phase,
        "run_id": run_id,
        "repeat_id": item.repeat_id,
        "workers": item.workers,
        "storage_mode": item.storage_mode,
        "cache_mode": item.cache_mode,
        "implementation_fingerprint": port["implementation_fingerprint"],
        "native_binary_sha256": binary_sha256,
        "query_split_sha256": split_sha256,
        "input_manifest_sha256": input_manifest_sha256,
        "query_order_sha256": query_order_sha256,
        "query_order_seed": order_seed,
        "warmup_queries": warmup_queries,
        "search_dram_budget_gib": item.budget_gib,
    }
    return validate_artifact(
        artifact_path,
        spec=item.spec,
        expected=expected,
        disk_root=disk_root,
    )


def _reuse_export_artifact(
    path: Path,
    *,
    item: WorkItem,
    port: dict[str, Any],
    dataset: str,
    run_id: str,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        artifact = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if (
        artifact.get("status") != "done"
        or artifact.get("formal_ready") is not True
        or artifact.get("layer") != item.spec.layer
        or artifact.get("dataset") != dataset
        or artifact.get("method") != item.spec.method
        or artifact.get("storage_mode") != item.storage_mode
        or artifact.get("phase") != "export"
        or artifact.get("run_id") != run_id
        or artifact.get("repeat_id") != item.repeat_id
        or artifact.get("workers") != item.workers
        or artifact.get("cache_mode") != item.cache_mode
        or artifact.get("implementation_fingerprint") != port.get("implementation_fingerprint")
        or not artifact.get("query_order_sha256")
        or artifact.get("query_order_seed") is None
        or artifact.get("search_dram_budget_gib") is None
    ):
        return None
    if (
        artifact.get("native_binary_sha256") != port.get("binary_sha256")
        and item.spec.method == "Ours-Disk"
    ):
        return None
    return artifact


def _reuse_measured_artifact(
    path: Path,
    *,
    item: WorkItem,
    port: dict[str, Any],
    phase: str,
    dataset: str,
    run_id: str,
    query_path: Path,
    split_sha256: str,
    input_manifest_sha256: str,
    disk_root: Path,
    seed: int,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        command = [str(x) for x in port["command"]]
        command[0] = _resolve_executable(command[0])
        binary_sha256 = sha256_file(Path(command[0]))
        order_seed = seed + item.repeat_id
        _, query_order_sha256 = _query_order_file(path, query_path, order_seed)
        warmup_queries = FORMAL_WARMUP_QUERIES if phase in ("validate", "validation", "test") else 0
        expected = {
            "dataset": dataset,
            "phase": phase,
            "run_id": run_id,
            "repeat_id": item.repeat_id,
            "workers": item.workers,
            "storage_mode": item.storage_mode,
            "cache_mode": item.cache_mode,
            "implementation_fingerprint": port["implementation_fingerprint"],
            "native_binary_sha256": binary_sha256,
            "query_split_sha256": split_sha256,
            "input_manifest_sha256": input_manifest_sha256,
            "query_order_sha256": query_order_sha256,
            "query_order_seed": order_seed,
            "warmup_queries": warmup_queries,
            "search_dram_budget_gib": item.budget_gib,
        }
        return validate_artifact(path, spec=item.spec, expected=expected, disk_root=disk_root)
    except (OSError, json.JSONDecodeError, ContractError, KeyError, ValueError):
        return None


def _build_tuning_lock(
    run_root: Path,
    layer: str,
    dataset: str,
    artifacts: list[tuple[Path, dict[str, Any]]],
) -> Path:
    selected: dict[str, Any] = {}
    for path, artifact in artifacts:
        rows = artifact.get("summary_rows", [])
        key = f"{artifact['method']}::{artifact['storage_mode']}"
        configs: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            configs.setdefault(str(row["config_id"]), []).append(row)
        choices = []
        for config_id, config_rows in configs.items():
            max_recall = max(float(r["recall"]) for r in config_rows)
            reached = [r for r in config_rows if float(r["recall"]) >= 0.95]
            score = max(float(r["qps"]) for r in reached) if reached else -1.0
            choices.append((bool(reached), score, max_recall, config_id))
        choices.sort(reverse=True)
        chosen = choices[0]
        selected[key] = {
            "config_id": chosen[3],
            "target_recall": 0.95,
            "target_reached": chosen[0],
            "max_measured_recall": chosen[2],
            "validation_artifact": str(path),
            "validation_artifact_sha256": sha256_file(path),
            "note": "No extrapolation; target_reached=false means the target is N/A.",
        }
    lock = {
        "schema_version": 2,
        "layer": layer,
        "dataset": dataset,
        "phase": "validation",
        "selection_target": 0.95,
        "selected": selected,
    }
    path = run_root / LAYER_DIRS[layer] / dataset / "manifests" / "tuning.lock.json"
    atomic_write_json(path, lock)
    return path


FAST_CONFIG_IDS = {
    ("05c", "Ours-Disk"): "beam1",
    ("05c", "SymphonyQG-DiskPort"): "QG_R64_EF400_t3",
    ("05c", "OG-LVQ-DiskPort"): "LVQ4_R64_W400",
    ("05c", "Glass-NSG-DiskPort"): "NSG_R64_L100_SQ4U",
    ("05c", "DiskANN-PQ-Disk"): "DiskANN_PQ_R64_L400",
}


def _fast_config_id(layer: str, method: str) -> str:
    return FAST_CONFIG_IDS.get((layer, method), "beam1")


def _write_fast_tuning_lock(
    run_root: Path,
    layer: str,
    dataset: str,
    specs: list[MethodSpec],
) -> Path:
    selected = {}
    for spec in specs:
        for mode in spec.storage_modes:
            selected[f"{spec.method}::{mode}"] = {
                "config_id": _fast_config_id(layer, spec.method),
                "target_recall": None,
                "target_reached": None,
                "max_measured_recall": None,
                "validation_artifact": "",
                "validation_artifact_sha256": "",
                "note": "QG05_FAST=1: fixed configuration inherited from the in-memory run; tune sweep skipped.",
            }
    lock = {
        "schema_version": 2,
        "layer": layer,
        "dataset": dataset,
        "phase": "validation",
        "selection_target": None,
        "selected": selected,
        "fast_mode": True,
    }
    path = run_root / LAYER_DIRS[layer] / dataset / "manifests" / "tuning.lock.json"
    atomic_write_json(path, lock)
    return path


def _pareto_front(rows: list[dict[str, Any]], yfield: str = "qps") -> list[dict[str, Any]]:
    """Recall-cost Pareto envelope over measured rows.

    Mirrors ``plot_05_disk_suite._pareto``: for each recall keep the best y,
    then retain only the monotone envelope (recall and y both maximized).
    """
    by_recall: dict[float, dict[str, Any]] = {}
    for row in rows:
        recall = round(float(row["recall"]), 10)
        current = by_recall.get(recall)
        if current is None or float(row[yfield]) > float(current[yfield]):
            by_recall[recall] = row
    ordered = sorted(
        by_recall.values(), key=lambda row: float(row["recall"]), reverse=True
    )
    frontier: list[dict[str, Any]] = []
    best = -math.inf
    for row in ordered:
        value = float(row[yfield])
        if value > best:
            frontier.append(row)
            best = value
    frontier.reverse()
    return frontier


def _median_test_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Median over the five formal repeats for every operating point.

    Mirrors ``plot_05_disk_suite._median_rows``, including the >5% repeat
    variation contract check.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    keys = (
        "method", "storage_mode", "cache_mode", "search_dram_budget_gib",
        "workers", "config_id", "search_param", "search_width", "beam_width",
        "ablation",
    )
    for row in rows:
        groups.setdefault(tuple(row.get(key, "") for key in keys), []).append(row)
    numeric_fields = {
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
    result: list[dict[str, Any]] = []
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
                    f"repeat variation >5% for {group[0]['method']} "
                    f"{group[0]['search_param']} {field}: CV={cv:.3f}; "
                    "investigate and rerun"
                )
        row = dict(group[0])
        for field in numeric_fields:
            values = [
                float(r[field]) for r in group if r.get(field, "") not in (None, "")
            ]
            if values:
                row[field] = float(statistics.median(values))
        row["repeat_id"] = "median_of_5"
        result.append(row)
    return result


def _write_tuning_frontier(
    run_root: Path,
    layer: str,
    dataset: str,
    artifacts: list[tuple[Path, dict[str, Any]]],
) -> Path:
    """Write the full validation recall-QPS Pareto frontier per method/ablation.

    DISK_SYSTEM_EXPERIMENT_PLAN.md section 7 requires the complete validation
    recall-cost Pareto front, not only the single Recall@10=0.95 point frozen
    into the tuning lock.
    """
    rows: list[dict[str, Any]] = []
    for path, artifact in artifacts:
        rows.extend(flatten_artifact(path, artifact))
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["method"]), str(row.get("ablation") or ""))
        groups.setdefault(key, []).append(row)
    frontier: list[dict[str, Any]] = []
    for group in groups.values():
        frontier.extend(_pareto_front(group))
    frontier.sort(
        key=lambda row: (
            str(row["method"]),
            str(row.get("ablation") or ""),
            float(row["recall"]),
        )
    )
    path = run_root / LAYER_DIRS[layer] / dataset / "manifests" / "tuning_frontier.csv"
    atomic_write_csv(path, frontier)
    lock_path = run_root / LAYER_DIRS[layer] / dataset / "manifests" / "tuning.lock.json"
    lock = json.loads(lock_path.read_text())
    lock["frontier_csv"] = str(path)
    lock["frontier_csv_sha256"] = sha256_file(path)
    atomic_write_json(lock_path, lock)
    print(
        f"[{layer}/{dataset}] tuning frontier: {len(frontier)} Pareto points -> {path}",
        flush=True,
    )
    return path


def hashlib_pair(query_path: Path, gt_path: Path) -> str:
    h = hashlib.sha256()
    for path in (query_path, gt_path):
        h.update(path.name.encode())
        h.update(bytes.fromhex(sha256_file(path)))
    return h.hexdigest()


def _dataset_input_manifest(
    run_root: Path, layer: str, dataset: str, data_root: Path
) -> tuple[Path, str]:
    path = run_root / "manifests" / "inputs" / f"{layer}_{dataset}.json"
    if path in _VERIFIED_INPUT_MANIFESTS:
        return path, _VERIFIED_INPUT_MANIFESTS[path]
    inputs = dataset_paths(dataset, data_root)
    if layer == "05a":
        saq_root = REPO_ROOT / "baselines" / "saq" / "data" / dataset
        inputs = {
            **inputs,
            "saq_query_pca": saq_root / f"{dataset}_query_pca.fvecs",
            "saq_index": saq_root / "ivf4096_b4_caq_adj_seg_pca.index",
        }
    if path.exists():
        document = json.loads(path.read_text())
        for name, source in inputs.items():
            recorded = document.get("files", {}).get(name, {})
            if recorded.get("path") != str(source.resolve()) or not source.exists():
                raise ContractError(f"stale dataset input manifest: {path}")
            if recorded.get("bytes") != source.stat().st_size or recorded.get("sha256") != sha256_file(source):
                raise ContractError(f"dataset input changed since manifest creation: {source}")
        digest = sha256_file(path)
        _VERIFIED_INPUT_MANIFESTS[path] = digest
        return path, digest
    files = {}
    for name, source in inputs.items():
        if not source.exists():
            raise ContractError(f"missing dataset input: {source}")
        files[name] = {
            "path": str(source.resolve()),
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
        }
    atomic_write_json(
        path,
        {"schema_version": 1, "dataset": dataset, "files": files},
    )
    digest = sha256_file(path)
    _VERIFIED_INPUT_MANIFESTS[path] = digest
    return path, digest


def _run_layer_dataset(
    *,
    args: argparse.Namespace,
    ports: dict[str, dict[str, Any]],
    run_root: Path,
    layer: str,
    dataset: str,
) -> None:
    specs = specs_for(layer)
    if getattr(args, "methods", ()):
        wanted = set(args.methods)
        specs = [spec for spec in specs if spec.method in wanted]
        missing = wanted - {spec.method for spec in specs}
        if missing:
            raise ContractError(
                f"unknown method(s) for {layer}: {', '.join(sorted(missing))}"
            )
        if not specs:
            raise ContractError(f"no methods selected for {layer}")
    input_manifest, input_manifest_sha256 = _dataset_input_manifest(
        run_root, layer, dataset, args.data_root
    )
    splits = prepare_query_splits(dataset, args.data_root, run_root / "query_splits", args.val_queries)
    native_phase = "validation" if args.phase == "tune" else ("test" if args.phase == "run" else args.phase)
    # 05A mirrors memory experiment 01: evaluate the FULL query set
    # (agnews/gist=1000, dbpedia=5000) with candidate_row_offset=0 so the
    # fixed-candidate recall/QPS are directly comparable to results/01.
    full_test_05a = layer == "05a" and native_phase == "test"
    if full_test_05a:
        ds_paths = dataset_paths(dataset, args.data_root)
        query_path, gt_path = ds_paths["query"], ds_paths["gt"]
    elif native_phase in ("validation", "validate"):
        query_path, gt_path = splits["validation_query"], splits["validation_gt"]
    elif native_phase == "test":
        query_path, gt_path = splits["test_query"], splits["test_gt"]
    else:
        query_path, gt_path = splits["validation_query"], splits["validation_gt"]
    split_sha256 = hashlib_pair(query_path, gt_path)
    candidate_row_offset = (
        0
        if full_test_05a
        else (_fvec_count(splits["validation_query"]) if native_phase == "test" else 0)
    )

    workers = args.workers
    if dataset == "gist" and layer in ("05b", "05c") and native_phase == "test" and not FAST_MODE:
        workers = tuple(dict.fromkeys((*workers, 1, 4, 8, 16, 32)))
    items = _work_items(specs, native_phase, workers, args.repeats, dataset)
    tuning_lock = run_root / LAYER_DIRS[layer] / dataset / "manifests" / "tuning.lock.json"
    if native_phase == "test" and FAST_MODE and not tuning_lock.exists():
        tuning_lock = _write_fast_tuning_lock(run_root, layer, dataset, specs)
    if native_phase == "test" and not tuning_lock.exists():
        raise ContractError(
            f"missing validation lock {tuning_lock}; run --phase tune with the same --run-id first"
        )
    artifacts: list[tuple[Path, dict[str, Any]]] = []
    for index, item in enumerate(items, 1):
        path = _artifact_path(run_root, layer, dataset, native_phase, item)
        if native_phase == "export":
            reused = _reuse_export_artifact(
                path,
                item=item,
                port=ports[item.spec.key],
                dataset=dataset,
                run_id=args.run_id,
            )
            if reused is not None:
                print(
                    f"[{layer}/{dataset}] {index}/{len(items)} export "
                    f"{item.spec.method} {item.storage_mode} reused existing artifact",
                    flush=True,
                )
                artifacts.append((path, reused))
                continue
        elif native_phase in ("validation", "test"):
            reused = _reuse_measured_artifact(
                path,
                item=item,
                port=ports[item.spec.key],
                phase=native_phase,
                dataset=dataset,
                run_id=args.run_id,
                query_path=query_path,
                split_sha256=split_sha256,
                input_manifest_sha256=input_manifest_sha256,
                disk_root=args.disk_root,
                seed=args.seed,
            )
            if reused is not None:
                print(
                    f"[{layer}/{dataset}] {index}/{len(items)} {native_phase} "
                    f"{item.spec.method} {item.storage_mode} reused existing artifact",
                    flush=True,
                )
                artifacts.append((path, reused))
                continue
        print(
            f"[{layer}/{dataset}] {index}/{len(items)} {native_phase} "
            f"{item.spec.method} {item.storage_mode} B={item.budget_gib:g} "
            f"cache={item.cache_mode} workers={item.workers} repeat={item.repeat_id}",
            flush=True,
        )
        artifact = _invoke_port(
            port=ports[item.spec.key],
            item=item,
            phase=native_phase,
            dataset=dataset,
            run_id=args.run_id,
            data_root=args.data_root,
            disk_root=args.disk_root,
            query_path=query_path,
            gt_path=gt_path,
            split_sha256=split_sha256,
            artifact_path=path,
            search_dram_budget_gib=args.search_dram_budget_gib,
            seed=args.seed,
            tuning_lock=tuning_lock if native_phase == "test" else None,
            input_manifest=input_manifest,
            input_manifest_sha256=input_manifest_sha256,
            candidate_row_offset=candidate_row_offset,
        )
        artifacts.append((path, artifact))

    if native_phase == "validation":
        _build_tuning_lock(run_root, layer, dataset, artifacts)
        _write_tuning_frontier(run_root, layer, dataset, artifacts)
    if native_phase == "test":
        rows = [row for path, artifact in artifacts for row in flatten_artifact(path, artifact)]
        if not FAST_MODE:
            validate_layer_completeness(
                rows,
                layer=layer,
                dataset=dataset,
                repeats=args.repeats,
                workers=workers,
            )
        aggregate = run_root / LAYER_DIRS[layer] / dataset / "aggregate" / "formal_test_rows.csv"
        atomic_write_csv(aggregate, rows)
        median_rows = rows if FAST_MODE else _median_test_rows(rows)
        frontier_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in median_rows:
            key = (
                str(row["method"]),
                str(row.get("ablation") or ""),
                str(row["workers"]),
            )
            frontier_groups.setdefault(key, []).append(row)
        frontier_rows: list[dict[str, Any]] = []
        for group in frontier_groups.values():
            frontier_rows.extend(_pareto_front(group))
        frontier_rows.sort(
            key=lambda row: (
                str(row["method"]),
                str(row.get("ablation") or ""),
                int(row["workers"]),
                float(row["recall"]),
            )
        )
        frontier_path = aggregate.with_name("formal_test_frontier.csv")
        atomic_write_csv(frontier_path, frontier_rows)
        atomic_write_json(
            aggregate.with_suffix(".manifest.json"),
            {
                "schema_version": 2,
                "run_id": args.run_id,
                "layer": layer,
                "dataset": dataset,
                "rows": len(rows),
                "methods": [spec.method for spec in specs],
                "workers": list(workers),
                "repeats": args.repeats,
                "frontier_csv": str(frontier_path),
                "frontier_csv_sha256": sha256_file(frontier_path),
                "source_artifacts": [
                    {"path": str(path), "sha256": sha256_file(path)} for path, _ in artifacts
                ],
            },
        )
        _publish_disk_environment(run_root, args.out_root, args.run_id, layer, dataset)


def make_parser(default_layer: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strict 05 disk-system fair runner (native 01/02/03 kernels only)"
    )
    parser.add_argument(
        "--layers",
        "--layer",
        dest="layers",
        default=default_layer or "05a,05b,05c",
        help="comma-separated layer list; --layer is accepted for compatibility",
    )
    parser.add_argument(
        "--phase",
        choices=("doctor", "export", "validate", "tune", "run", "plot"),
        required=True,
    )
    parser.add_argument("--datasets", "--dataset", dest="datasets", default="agnews,gist,dbpedia")
    parser.add_argument("--disk-root", type=Path)
    parser.add_argument(
        "--disk-profile",
        choices=("nvme", "hdd_raid"),
        default="nvme",
        help="physical disk profile for preflight; both profiles require O_DIRECT/native AIO",
    )
    parser.add_argument("--ports", type=Path, default=PACKAGE_DIR / "ports.local.json")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--out-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--search-dram-budget-gib", type=float, default=2.0)
    parser.add_argument("--storage-modes", default="resident,payload_on_ssd")
    parser.add_argument("--workers", default="1,32")
    parser.add_argument("--methods", default="", help="optional comma-separated method filter")
    parser.add_argument("--repeats", type=int, default=FORMAL_REPEATS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--val-queries", type=int, default=1000)
    return parser


def run(argv: list[str] | None = None, default_layer: str | None = None) -> int:
    parser = make_parser(default_layer)
    args = parser.parse_args(argv)
    try:
        args.layers = _csv_list(args.layers)
        args.datasets = _csv_list(args.datasets)
        args.workers = _int_list(args.workers)
        args.storage_modes = _csv_list(args.storage_modes)
        args.methods = _csv_list(args.methods)
        args.data_root = args.data_root.resolve()
        args.out_root = args.out_root.resolve()
        args.run_id = args.run_id or _default_run_id()
        if args.layers == ("all",):
            args.layers = tuple(LAYER_DIRS)
        if not args.layers or any(layer not in LAYER_DIRS for layer in args.layers):
            raise ContractError(f"layers must be a subset of {tuple(LAYER_DIRS)}")
        if args.phase == "doctor":
            from diskfair.doctor import run_doctor

            return run_doctor(
                repo_root=REPO_ROOT,
                layers=args.layers,
                datasets=args.datasets,
                data_root=args.data_root,
                ports_path=args.ports.resolve(),
                disk_root=args.disk_root,
                disk_profile=args.disk_profile,
            )
        if args.repeats != FORMAL_REPEATS and args.phase == "run" and not FAST_MODE:
            raise ContractError(f"formal test requires exactly {FORMAL_REPEATS} repeats")
        if args.search_dram_budget_gib != 2.0:
            raise ContractError(
                "the formal primary budget is fixed at 2 GiB; GIST 1/4-GiB and C=0 "
                "sensitivity profiles are scheduled automatically"
            )
        if "05a" in args.layers and args.storage_modes != ("resident", "payload_on_ssd"):
            raise ContractError(
                "formal 05A requires paired --storage-modes resident,payload_on_ssd"
            )
        if args.seed != SEED:
            raise ContractError(f"formal suite seed is fixed at {SEED}")
        if args.phase == "plot":
            command = [
                sys.executable,
                str(PACKAGE_DIR / "plot_05_disk_suite.py"),
                "--run-root", str(args.out_root / "runs" / args.run_id),
                "--layers", ",".join(args.layers),
                "--datasets", ",".join(args.datasets),
            ]
            if args.methods:
                command.extend(["--methods", ",".join(args.methods)])
            status = subprocess.call(command)
            if status == 0:
                run_root = args.out_root / "runs" / args.run_id
                for layer in args.layers:
                    for dataset in args.datasets:
                        _publish_disk_environment(run_root, args.out_root, args.run_id, layer, dataset)
            return status
        if args.disk_root is None:
            raise ContractError("--disk-root is mandatory for every non-plot formal phase")
        args.disk_root = args.disk_root.resolve()
        ports = load_port_registry(args.ports.resolve())
        validate_registry(ports, args.layers)
        _formal_preflight(args.disk_root, args.out_root, args.run_id, args.disk_profile)
        run_root = args.out_root / "runs" / args.run_id
        atomic_write_json(
            run_root / "manifests" / f"invocation_{args.phase}.json",
            {
                "schema_version": 2,
                "argv": sys.argv if argv is None else argv,
                "run_id": args.run_id,
                "layers": list(args.layers),
                "datasets": list(args.datasets),
                "workers": list(args.workers),
                "repeats": args.repeats,
                "seed": args.seed,
                "search_dram_budget_gib": args.search_dram_budget_gib,
                "disk_profile": args.disk_profile,
                "ports_registry": str(args.ports.resolve()),
                "ports_registry_sha256": sha256_file(args.ports.resolve()),
            },
        )
        for layer in args.layers:
            for dataset in args.datasets:
                _run_layer_dataset(
                    args=args,
                    ports=ports,
                    run_root=run_root,
                    layer=layer,
                    dataset=dataset,
                )
        print(f"formal phase complete: run_id={args.run_id}")
        return 0
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
