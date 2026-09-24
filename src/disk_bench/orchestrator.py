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
    DEFAULT_DISK_ROOT,
    RESULTS_ROOT,
    SEED,
    dataset_paths,
    hardware_info,
    lsblk_rotational,
    prepare_query_splits,
    resolve_executable,
    resolve_repo_path,
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
from diskfair.protocol import (PROTOCOL_ID, DEFAULT_BUDGET_GIB, SYSTEM_PRIMARY_BUDGET_GIB, BUDGET_GRID_GIB,
                               aggregate_repeats, attach_resource_evidence,
                               method_budget_gib, requires_memory_limit)
from diskfair.memory_runner import run_measured_isolated as run_measured, check_environment, MemoryEnvironmentError
from diskfair.storage_precondition import prepare_search, PROTOCOL as STORAGE_PRECONDITION_PROTOCOL
from diskfair.fio_preflight import run_fio  # noqa: E402
from diskfair.dataset_policy import (  # noqa: E402
    metric_compatibility_error,
    policy_for,
    resident_budget_errors,
)

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
from diskfair.layout import (LAYER_DIRS, LAYOUT_VERSION, normalize_layers, validate_run_id,
                            run_metadata_root, dataset_run_root, MEMORY_EXPERIMENT,
                            memory_experiment, experiment_directory)

PUBLISH_LAYER_DIRS = LAYER_DIRS

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
    try:
        resolved = resolve_executable(argv0, REPO_ROOT)
    except (FileNotFoundError, PermissionError):
        raise ContractError(f"native port executable not found: {argv0}")
    return resolved


def _auto_disk_profile(disk_root: Path) -> str:
    disk_root.mkdir(parents=True, exist_ok=True)
    rotational = lsblk_rotational(disk_root)
    if rotational is True:
        return "hdd_raid"
    device = disk_root.stat().st_dev
    resolved = Path(f"/sys/dev/block/{os.major(device)}:{os.minor(device)}").resolve()
    if rotational is False and any(part.startswith("nvme") for part in resolved.parts):
        return "nvme"
    raise ContractError("cannot verify NVMe/HDD transport automatically; specify and audit --disk-profile")


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
    manifest_dir = run_metadata_root(out_root, run_id)
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
    filename = "diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    candidates = (
        REPO_ROOT / "artifacts" / "graphs" / dataset / "shared_graph" / filename,
        REPO_ROOT / "artifacts" / "indexes" / "legacy_aliases" / dataset / "indexes" / "02_diskann_fair" / "shared_graph" / filename,
    )
    return next((path for path in candidates if path.exists()), candidates[0])


def _ours_graph(dataset: str) -> Path:
    """The exact M=64 ExRaBitQ-symmetric graph used by experiments 02 and 03."""
    candidates = (
        REPO_ROOT
        / "artifacts" / "graphs"
        / dataset
        / "Ours"
        / f"{dataset}_Ours_R64_Lbuild400.graph.bin",
        REPO_ROOT
        / "artifacts" / "indexes" / "legacy_aliases" / dataset / "indexes"
        / "02_diskann_fair"
        / "Ours"
        / f"{dataset}_Ours_R64_Lbuild400.graph.bin",
        REPO_ROOT
        / "results"
        / "02_diskann_fair"
        / dataset
        / "indexes"
        / "Ours"
        / f"{dataset}_Ours_R64_Lbuild400.graph.bin",
    )
    return next((path for path in candidates if path.exists()), candidates[0])


def _diskann_system_graph(dataset: str) -> Path:
    filename = "diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    candidates = (
        REPO_ROOT
        / "artifacts" / "graphs"
        / dataset
        / "03_system_fair"
        / "DiskANN-PQ-Disk"
        / filename,
        _shared_graph(dataset),
    )
    return next((path for path in candidates if path.exists()), candidates[0])


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
    storage_modes: tuple[str, ...],
    budget_gib: float = DEFAULT_BUDGET_GIB,
    cache_mode: str = "standard",
    baseline_budgets: dict[str, float] | None = None,
) -> list[WorkItem]:
    # Freeze each method's own configuration across validation and test.
    primary_cache = "c0" if specs[0].layer == "05a" else cache_mode
    selected_modes = set(storage_modes)
    result = []
    for repeat_id in range(repeats if phase == "test" else 1):
        for wi, worker in enumerate((1,) if phase == "export" else workers):
            shift = (repeat_id + wi) % len(specs)
            ordered = specs[shift:] + specs[:shift]
            result.extend(WorkItem(spec, mode, repeat_id, worker,
                                  method_budget_gib(spec.method, budget_gib, baseline_budgets), primary_cache)
                          for spec in ordered for mode in spec.storage_modes
                          if mode in selected_modes)
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
    method_root = dataset_run_root(run_root, layer, dataset) / "raw" / _safe(item.spec.method)
    # CLI validate and internal validation name the same query split. Reuse
    # existing validated evidence rather than repeating the sweep during tune.
    if phase == "validation" and (method_root / "validate" / stem).is_file():
        return method_root / "validate" / stem
    return method_root / phase / stem


def _finalize_dataset_manifest(run_root: Path, out_root: Path, run_id: str, layer: str, dataset: str) -> None:
    """Finalize the manifest in place; do not copy raw data or tables elsewhere."""
    source = dataset_run_root(run_root, layer, dataset)
    source.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "tables", "figures", "manifests"):
        (source / name).mkdir(exist_ok=True)
    aggregate_manifest = source / "tables" / "formal_test_rows.manifest.json"
    evidence = json.loads(aggregate_manifest.read_text()) if aggregate_manifest.exists() else {}
    accepted = evidence.get("formal_ready") is True
    atomic_write_json(source / "manifest.json", {
        "schema_version": 1, "layout_version": LAYOUT_VERSION, "run_id": run_id,
        "protocol_id": PROTOCOL_ID, "experiment": experiment_directory(layer, run_root), "dataset": dataset,
        "formal_ready": accepted, "acceptance": "passed" if accepted else "pending",
        "run_metadata_relative": os.path.relpath(run_root, source),
        "protocol_sha256": sha256_file(run_root / "protocol.json") if (run_root / "protocol.json").exists() else None,
        "tables_manifest": "tables/formal_test_rows.manifest.json" if evidence else None,
        "note": "Acceptance refers to source protocol checks, not directory placement.",
    })


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


def _fvec_shape(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        raw = stream.read(4)
    if len(raw) != 4:
        raise ContractError(f"empty fvecs file: {path}")
    dimension = struct.unpack("<i", raw)[0]
    return _fvec_count(path), dimension


def _dataset_admission_errors(
    dataset: str,
    layers: tuple[str, ...],
    data_root: Path,
    budget_gib: float,
    methods: tuple[str, ...] = (),
) -> list[str]:
    metric_error = metric_compatibility_error(dataset)
    base_count, dimension = _fvec_shape(dataset_paths(dataset, data_root)["base"])
    return ([metric_error] if metric_error else []) + resident_budget_errors(
        dataset,
        base_count,
        dimension,
        tuple(layer for layer in layers if not methods or "Ours-Disk" in methods),
        budget_gib,
    )


def _query_order_file(artifact_path: Path, query_path: Path, seed: int) -> tuple[Path, str]:
    phase = artifact_path.parent.name
    query_hash = sha256_file(query_path)[:12]
    order_path = (
        artifact_path.parents[3]
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


def _proc_value(pid: int, key: str) -> int | None:
    """Read one /proc/<pid>/status field (e.g. VmHWM) in bytes."""
    try:
        text = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def _proc_read_bytes(pid: int) -> int | None:
    """Actual storage bytes read by the process (``/proc/<pid>/io``)."""
    try:
        text = Path(f"/proc/{pid}/io").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("read_bytes:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
                return int(parts[1])
    return None


def _run_capture_build(args: list[str], log, stats_path: Path) -> int:
    """Run one native subprocess while sampling peak RSS and storage reads.

    Used only for the export phase under ``QG05_CAPTURE_BUILD_STATS=1``. The
    result is written next to the export artifact as ``*.build_stats.json`` and
    never changes the formal artifact itself.
    """
    started = time.monotonic()
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, text=True)
    peak_rss_bytes = 0
    read_bytes = 0
    while proc.poll() is None:
        rss = _proc_value(proc.pid, "VmHWM")
        if rss is not None:
            peak_rss_bytes = max(peak_rss_bytes, rss)
        rb = _proc_read_bytes(proc.pid)
        if rb is not None:
            read_bytes = max(read_bytes, rb)
        time.sleep(0.05)
    elapsed_s = time.monotonic() - started
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "wall_seconds": round(elapsed_s, 6),
                "peak_rss_bytes": peak_rss_bytes,
                "read_bytes": read_bytes,
            },
            indent=2,
        )
        + "\n"
    )
    return proc.returncode if proc.returncode is not None else 1


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
    cgroup_parent: Path | None = None,
    cpu_affinity: tuple[int, ...] | None = None,
    numa_node: int | None = None,
    experiment: str | None = None,
    fixed_beam: int | None = None,
    search_widths: tuple[int, ...] | None = None,
    ours_cache_allocation: str = "graph_first",
    ours_hot_profile: Path | None = None,
    ours_reference_source: Path | None = None,
    ours_frozen_route: Path | None = None,
    prepare_only: bool = False,
    measurement_width: int | None = None,
    warmup_query: Path | None = None,
) -> dict[str, Any]:
    if port.get('source_snapshot_path'):
        from diskfair.system03 import verify_source_snapshot
        verify_source_snapshot(port['source_snapshot_path'], port['source_snapshot_sha256'])
    if port.get("artifact_bridge") == "official_cli_system03_v1":
        from diskfair.official_system03 import invoke
        return invoke(**locals())
    if prepare_only and not (item.spec.layer == "05c" and item.spec.method == "Ours-Disk"
                             and phase in ("validate", "validation")):
        raise ContractError("profile-only preparation requires Ours validation queries")
    command = [str(x) for x in port["command"]]
    if search_widths is not None and item.spec.method != "DiskANN-PQ-Disk":
        if item.spec.method != "Ours-Disk" or item.spec.layer != "05c":
            raise ContractError("explicit search widths currently require formal Ours")
        command += ["--search-widths", ",".join(map(str,search_widths))]
    if item.spec.method == "Ours-Disk":
        command += ["--ours-cache-allocation", ours_cache_allocation]
    if fixed_beam is not None and item.spec.method in ("Ours-Disk", "DiskANN-PQ-Disk"):
        command += ["--fixed-beam", str(fixed_beam)]
    if measurement_width is not None:
        command += ["--measurement-width", str(measurement_width)]
    warmup_evidence = None
    if warmup_query is not None:
        from diskfair.system03 import verify_disjoint
        warmup_evidence = verify_disjoint(warmup_query, query_path, FORMAL_WARMUP_QUERIES)
        command += ["--warmup-query", str(warmup_query.resolve()),
                    "--warmup-query-sha256", warmup_evidence["warmup_query_sha256"]]
    command[0] = _resolve_executable(command[0])
    binary_sha256 = sha256_file(Path(command[0]))
    if binary_sha256 != port["binary_sha256"]:
        raise ContractError(
            f"native binary changed for {item.spec.key}: got {binary_sha256}, "
            f"registry pins {port['binary_sha256']}"
        )
    index_layer_dir = LAYER_DIRS[item.spec.layer]
    # Published experiment directory names must not relocate reusable indexes.
    legacy_system = disk_root / "05_disk_system_fair" / "05C_disk_system_fair"
    if item.spec.layer == "05c" and (legacy_system / dataset / _safe(item.spec.method) / _safe(item.storage_mode)).is_dir():
        index_layer_dir = "05C_disk_system_fair"
    index_dir = (
        disk_root
        / "05_disk_system_fair"
        / index_layer_dir
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
        "--source-results-root", str((REPO_ROOT / "artifacts" / "indexes" / "legacy_aliases").resolve()),
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
        item.spec.layer == "05c"
        and item.spec.method in ("Ours-Disk", "DiskANN-PQ-Disk")
    ):
        ours = item.spec.method == "Ours-Disk"
        graph = (
            _ours_graph(dataset)
            if ours
            else _diskann_system_graph(dataset)
            if item.spec.layer == "05c"
            else _shared_graph(dataset)
        )
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

    capture_build = (
        os.environ.get("QG05_CAPTURE_BUILD_STATS") == "1" and phase == "export"
    )
    build_stats_path = artifact_path.with_suffix(".build_stats.json")
    measured = phase in ("validate", "validation", "test")
    if not prepare_only and (artifact_path.exists() or log_path.exists()):
        raise ContractError(f"refusing to overwrite existing attempt: {artifact_path}; use a fresh run-id")
    if measured and item.spec.layer == "05c" and item.spec.method == "Ours-Disk":
        from diskfair.ours_pca import prepare_route_args
        sources = dataset_paths(dataset, data_root)
        args += prepare_route_args(
            binary=Path(command[0]), index=index_dir, base=sources["base"],
            query_source=sources["query"], groundtruth_source=sources["gt"],
            workers=item.workers, budget_gib=item.budget_gib, artifact=artifact_path,
            frozen_route=ours_frozen_route,
        )
        if tuning_lock is not None and phase == "test":
            frozen = json.loads(tuning_lock.read_text())
            selected = frozen["selected"][f"{item.spec.method}::{item.storage_mode}"]
            if not frozen.get("fast_mode"):
                actual_plan = json.loads(artifact_path.with_suffix(".route_plan.json").read_text())
                asset_hash = args[args.index("--pca-assets-sha256") + 1] if "--pca-assets-sha256" in args else ""
                if selected.get("ours_route_plan") != actual_plan or selected.get("pca_assets_sha256", "") != asset_hash:
                    raise ContractError("Ours routing plan/assets differ from validation; run validation for this budget/workers first")
        from diskfair.ours_records import prepare_record_args
        args += prepare_record_args(args, artifact_path, phase=phase, tuning_lock=tuning_lock,
                                    cpu_affinity=cpu_affinity, numa_node=numa_node, shared_profile=ours_hot_profile)
    if prepare_only:
        def flag(name, default=""):
            return args[args.index(name)+1] if name in args else default
        profile = flag("--ours-hot-profile-manifest")
        if profile and fixed_beam is not None:
            configs = json.loads(Path(profile).read_text())["configs"]
            if ({r["beam"] for r in configs} != {fixed_beam}
                    or {r["width"] for r in configs} != set(search_widths or (10,20,40,60,100,160,240,400,580))
                    or len(configs) != len(search_widths or (10,20,40,60,100,160,240,400,580))):
                raise ContractError("hot profile must cover exactly the fixed beam and requested widths")
        return dict(method=item.spec.method, storage_mode=item.storage_mode,
                    preparation_only=True, formal_ready=False, ours_cache_allocation=ours_cache_allocation,
                    ours_route_plan=json.loads(artifact_path.with_suffix(".route_plan.json").read_text()),
                    pca_assets_sha256=flag("--pca-assets-sha256"),
                    ours_record_cache_policy=("validation_hot_then_dynamic_fifo16_v1" if profile else "off"),
                    ours_hot_profile_manifest=profile,
                    ours_hot_profile_sha256=flag("--ours-hot-profile-sha256"))
    reference_manifest = None
    if measured and item.spec.layer == "05c" and item.spec.method in ("Ours-Disk", "DiskANN-PQ-Disk", "SymphonyQG-DiskPort", "Glass-NSG-DiskPort"):
        from diskfair.admission import prepare_reference
        if ours_reference_source is not None:
            from functools import partial
            from diskfair.paired_reference import prepare_results as prepare
            prepare_reference = partial(prepare, source_path=ours_reference_source)
        args, reference_manifest = prepare_reference(
            args, artifact_path, cpu_affinity=cpu_affinity, numa_node=numa_node,
            env=dict(os.environ, OMP_NUM_THREADS=str(item.workers), MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1"))
    if measured:
        from diskfair.admission import measurement_environment
        measured_env = measurement_environment(
            args, dict(os.environ, OMP_NUM_THREADS=str(item.workers),
                       MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1"))
        evidence_path = artifact_path.with_suffix(".resources.json")
        reference = item.storage_mode == "resident"
        capped = requires_memory_limit(item.spec.method, item.storage_mode)
        storage_evidence_path = None
        if item.spec.layer == "05c" and not reference:
            storage_evidence_path = artifact_path.with_suffix(".storage_precondition.json")
            prepare_search(args, storage_evidence_path)
        evidence = run_measured(
            args, evidence_path=evidence_path, log_path=log_path,
            budget_bytes=int(item.budget_gib * (1 << 30)) if not reference else None,
            cgroup_parent=cgroup_parent if capped else None, reference=reference,
            rss_budget=not reference,
            cpu_affinity=cpu_affinity, numa_node=numa_node,
            env=measured_env,
        )
        if evidence["status"] != "completed":
            raise ContractError(f"{item.spec.key}: {evidence['status']}; evidence: {evidence_path}")
        attach_resource_evidence(artifact_path, evidence_path, evidence)
        if storage_evidence_path is not None:
            artifact = json.loads(artifact_path.read_text())
            artifact.update(storage_precondition_protocol=STORAGE_PRECONDITION_PROTOCOL,
                            storage_precondition_path=str(storage_evidence_path.resolve()),
                            storage_precondition_sha256=sha256_file(storage_evidence_path))
            atomic_write_json(artifact_path, artifact)
    else:
        with log_path.open("x") as log:
            log.write("argv=" + json.dumps(args) + "\n"); log.flush()
            returncode = (_run_capture_build(args, log, build_stats_path) if capture_build else
                          subprocess.run(args, stdout=log, stderr=subprocess.STDOUT).returncode)
        if returncode:
            raise ContractError(f"native port failed ({returncode}) for {item.spec.key}; log: {log_path}")
    if experiment is not None:
        artifact = json.loads(artifact_path.read_text())
        artifact["experiment"] = experiment
        atomic_write_json(artifact_path, artifact)
    if measurement_width is not None:
        from diskfair.system03 import PROTOCOL as SYSTEM03_PROTOCOL
        artifact = json.loads(artifact_path.read_text())
        artifact.update(system03_protocol=SYSTEM03_PROTOCOL, measurement_width=measurement_width,
                        independent_warmup=warmup_evidence,
                        source_snapshot_path=port['source_snapshot_path'],
                        source_snapshot_sha256=port['source_snapshot_sha256'],
                        source_commit=port.get('source_commit'))
        atomic_write_json(artifact_path, artifact)
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
    if measured:
        expected.update(protocol_id=PROTOCOL_ID)
    if experiment is not None:
        expected["experiment"] = experiment
    if reference_manifest is not None:
        from diskfair.admission import finalize
        finalize(artifact_path, reference_manifest, spec=item.spec, expected=expected, disk_root=disk_root)
    return validate_artifact(
        artifact_path,
        spec=item.spec,
        expected=expected,
        disk_root=disk_root,
        require_formal_ready=os.environ.get("QG05_SKIP_EXTERNAL_PARITY") != "1",
    )


def _reuse_export_artifact(
    path: Path,
    *,
    item: WorkItem,
    port: dict[str, Any],
    dataset: str,
    run_id: str,
    experiment: str | None = None,
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
        or (experiment is not None and artifact.get("experiment") != experiment)
        or artifact.get("dataset") != dataset
        or artifact.get("method") != item.spec.method
        or artifact.get("storage_mode") != item.storage_mode
        or artifact.get("phase") != "export"
        or artifact.get("run_id") != run_id
        or artifact.get("repeat_id") != item.repeat_id
        or artifact.get("workers") != item.workers
        or artifact.get("cache_mode") != item.cache_mode
        or artifact.get("port_kind") != port.get("port_kind")
        or artifact.get("implementation_fingerprint") != port.get("implementation_fingerprint")
        or not artifact.get("query_order_sha256")
        or artifact.get("query_order_seed") is None
        or artifact.get("search_dram_budget_gib") is None
        or artifact.get("native_binary_sha256") != port.get("binary_sha256")
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
    experiment: str | None = None,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        recorded_phase = json.loads(path.read_text()).get("phase")
        if phase in ("validate", "validation") and recorded_phase in ("validate", "validation"):
            phase = recorded_phase
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
        expected["protocol_id"] = PROTOCOL_ID
        if experiment is not None:
            expected["experiment"] = experiment
        artifact = validate_artifact(
            path, spec=item.spec, expected=expected, disk_root=disk_root,
            require_formal_ready=os.environ.get("QG05_SKIP_EXTERNAL_PARITY") != "1",
        )
        if item.spec.layer == "05c" and item.storage_mode != "resident":
            if artifact.get("storage_precondition_protocol") != STORAGE_PRECONDITION_PROTOCOL:
                return None
            preparation_path = Path(artifact["storage_precondition_path"])
            if sha256_file(preparation_path) != artifact["storage_precondition_sha256"]:
                return None
            preparation = json.loads(preparation_path.read_text())
            if preparation.get("status") != "completed" or preparation.get("protocol") != STORAGE_PRECONDITION_PROTOCOL:
                return None
        requested_widths = os.environ.get("QG05_FAST_WIDTHS")
        if phase == "test" and FAST_MODE and requested_widths:
            expected_widths = {
                int(value) for value in requested_widths.split(",") if value.strip()
            }
            actual_widths = {
                int(row["search_width"]) for row in artifact.get("summary_rows", [])
            }
            if actual_widths != expected_widths:
                return None
        return artifact
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
            "ours_cache_allocation": artifact.get("ours_cache_allocation", "graph_first"),
            "ours_route_plan": artifact.get("ours_route_plan"),
            "pca_assets_sha256": artifact.get("pca_assets_sha256", ""),
            "ours_record_cache_policy": artifact.get("ours_record_cache_policy", ""),
            "ours_hot_profile_manifest": artifact.get("ours_hot_profile_manifest", ""),
            "ours_hot_profile_sha256": artifact.get("ours_hot_profile_sha256", ""),
        }
    lock = {
        "schema_version": 2,
        "layer": layer,
        "dataset": dataset,
        "phase": "validation",
        "selection_target": 0.95,
        "selected": selected,
    }
    path = dataset_run_root(run_root, layer, dataset) / "manifests" / "tuning.lock.json"
    atomic_write_json(path, lock)
    return path


FAST_CONFIG_IDS = {
    ("05c", "Ours-Disk"): "beam1",
    ("05c", "SymphonyQG-DiskPort"): "QG_R64_EF400_t3",
    ("05c", "OG-LVQ-DiskPort"): "LVQ4_R64_W400",
    ("05c", "Glass-NSG-DiskPort"): "NSG_R64_L400_SQ4U",
    ("05c", "DiskANN-PQ-Disk"): "DiskANN_PQ_R64_L400",
}


def _fast_config_id(layer: str, method: str) -> str:
    return FAST_CONFIG_IDS.get((layer, method), "beam1")


def _write_fast_tuning_lock(
    run_root: Path,
    layer: str,
    dataset: str,
    specs: list[MethodSpec],
    storage_modes: tuple[str, ...],
) -> Path:
    selected = {}
    selected_modes = set(storage_modes)
    for spec in specs:
        for mode in spec.storage_modes:
            if mode not in selected_modes:
                continue
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
    path = dataset_run_root(run_root, layer, dataset) / "manifests" / "tuning.lock.json"
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


def _single_test_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate a single measured run per point without statistical aggregation."""
    try:
        return aggregate_repeats(rows)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def _write_tuning_frontier(
    run_root: Path,
    layer: str,
    dataset: str,
    artifacts: list[tuple[Path, dict[str, Any]]],
) -> Path:
    """Write the full validation recall-QPS Pareto frontier per method/ablation.

    docs/plans/DISK_SYSTEM_EXPERIMENT_PLAN.md section 7 requires the complete validation
    recall-cost Pareto front, not only the single Recall@10=0.95 point frozen
    into the tuning lock.
    """
    rows: list[dict[str, Any]] = []
    for path, artifact in artifacts:
        rows.extend(flatten_artifact(path, artifact))
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["method"]),
            str(row.get("storage_mode") or ""),
            str(row.get("ablation") or ""),
        )
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
    path = dataset_run_root(run_root, layer, dataset) / "manifests" / "tuning_frontier.csv"
    atomic_write_csv(path, frontier)
    lock_path = dataset_run_root(run_root, layer, dataset) / "manifests" / "tuning.lock.json"
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
    path = run_root / "inputs" / f"{layer}_{dataset}.json"
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
    policy = policy_for(dataset)
    if path.exists():
        document = json.loads(path.read_text())
        if document.get("dataset_policy") != policy.to_dict():
            raise ContractError(f"stale dataset metric policy in input manifest: {path}")
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
        {
            "schema_version": 2,
            "dataset": dataset,
            "dataset_policy": policy.to_dict(),
            "files": files,
        },
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
    try:
        policy_errors = _dataset_admission_errors(
            dataset,
            (layer,),
            args.data_root,
            args.search_dram_budget_gib,
            args.methods,
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if policy_errors:
        raise ContractError("dataset is not formal-ready:\n  - " + "\n  - ".join(policy_errors))
    specs = specs_for(layer, getattr(args, "methods", ()) or None)
    if getattr(args, "system03_fixed", False):
        from diskfair.system03 import unsupported_reason
        _, dimension = _fvec_shape(args.data_root / dataset / f"{dataset}_base.fvecs")
        missing = {spec.method: unsupported_reason(spec.method, dimension, 48) for spec in specs
                   if unsupported_reason(spec.method, dimension, 48)}
        if missing:
            atomic_write_json(dataset_run_root(run_root, layer, dataset) / 'manifests' / 'unsupported_methods.json', missing)
            specs = [spec for spec in specs if spec.method not in missing]
            if not specs:
                print(f"[{layer}/{dataset}] unsupported: {missing}", flush=True)
                return []
    if getattr(args, "methods", ()):
        wanted = set(args.methods)
        if getattr(args, "system03_fixed", False):
            wanted -= set(missing)
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
    splits = prepare_query_splits(dataset, args.data_root, REPO_ROOT / "artifacts" / "query_splits" / "runs" / args.run_id, args.val_queries)
    native_phase = "validation" if args.phase == "tune" else ("test" if args.phase == "run" else args.phase)
    if native_phase == "test":
        query_path, gt_path = splits["test_query"], splits["test_gt"]
    else:
        query_path, gt_path = splits["validation_query"], splits["validation_gt"]
    split_sha256 = hashlib_pair(query_path, gt_path)
    candidate_row_offset = _fvec_count(splits["validation_query"]) if native_phase == "test" else 0
    workers = args.workers
    storage_modes = (
        tuple(dict.fromkeys(mode for spec in specs for mode in spec.storage_modes))
        if args.storage_modes == ("auto",)
        else args.storage_modes
    )
    items = _work_items(specs, native_phase, workers, args.repeats, dataset, storage_modes,
                        args.search_dram_budget_gib, args.cache_mode, args.baseline_native_budgets)
    if not items:
        raise ContractError(
            f"no work items selected for {layer}/{dataset}; storage modes "
            f"{storage_modes} do not match selected methods"
        )
    _finalize_dataset_manifest(run_root, args.out_root, args.run_id, layer, dataset)
    tuning_lock = getattr(args, "tuning_lock_override", None) or (dataset_run_root(run_root, layer, dataset) / "manifests" / "tuning.lock.json")
    if native_phase == "test" and FAST_MODE and not tuning_lock.exists():
        tuning_lock = _write_fast_tuning_lock(run_root, layer, dataset, specs, storage_modes)
    if native_phase == "test" and not tuning_lock.exists():
        raise ContractError(
            f"missing validation lock {tuning_lock}; run --phase tune with the same --run-id first"
        )
    artifacts: list[tuple[Path, dict[str, Any]]] = []
    isolated = getattr(args, "system03_fixed", False) and native_phase == "test"
    widths = (10,20,40,60,100,160,240,400,580) if isolated else (None,)
    work = [(item, width) for item in items for width in widths]
    for index, (item, measurement_width) in enumerate(work, 1):
        path = _artifact_path(run_root, layer, dataset, native_phase, item)
        if measurement_width is not None:
            path = path.with_name(path.stem + f"__L{measurement_width}" + path.suffix)
        if native_phase == "export":
            reused = _reuse_export_artifact(
                path,
                item=item,
                port=ports[item.spec.key],
                dataset=dataset,
                run_id=args.run_id,
                experiment=args.experiment,
            )
            if reused is not None:
                print(
                    f"[{layer}/{dataset}] {index}/{len(items)} export "
                    f"{item.spec.method} {item.storage_mode} reused existing artifact",
                    flush=True,
                )
                artifacts.append((path, reused))
                continue
        elif native_phase in ("validation", "validate", "test") and not getattr(args, "fixed_preparation", False):
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
                experiment=args.experiment,
            )
            if reused is not None:
                if isolated:
                    from diskfair.system03 import PROTOCOL as SYSTEM03_PROTOCOL, verify_disjoint
                    evidence = verify_disjoint(splits["validation_query"], query_path, FORMAL_WARMUP_QUERIES)
                    if (reused.get("system03_protocol") != SYSTEM03_PROTOCOL
                            or reused.get("measurement_width") != measurement_width
                            or reused.get("independent_warmup") != evidence):
                        raise ContractError("stale 03 point/warmup evidence; use a new run-id")
                print(
                    f"[{layer}/{dataset}] {index}/{len(items)} {native_phase} "
                    f"{item.spec.method} {item.storage_mode} reused existing artifact",
                    flush=True,
                )
                artifacts.append((path, reused))
                continue
        stage_label = "hot-profile preparation only" if getattr(args, "fixed_preparation", False) else native_phase
        print(
            f"[{layer}/{dataset}] {index}/{len(items)} {stage_label} "
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
            cgroup_parent=args.cgroup_parent,
            cpu_affinity=args.cpu_affinity,
            numa_node=args.numa_node,
            experiment=args.experiment,
            fixed_beam=getattr(args, "fixed_beam", None),
            search_widths=getattr(args, "search_widths", None),
            ours_cache_allocation=getattr(args, "ours_cache_allocation", "graph_first"),
            ours_reference_source=getattr(args, "ours_reference_source", None),
            ours_hot_profile=getattr(args, "ours_hot_profile", None),
            ours_frozen_route=getattr(args, "ours_frozen_route", None),
            prepare_only=getattr(args, "fixed_preparation", False),
            measurement_width=measurement_width,
            warmup_query=splits["validation_query"] if isolated else None,
        )
        artifacts.append((path, artifact))

    if getattr(args, "fixed_preparation", False):
        return artifacts
    if getattr(args, "resource_preflight", False):
        return artifacts
    if native_phase == "validation":
        _build_tuning_lock(run_root, layer, dataset, artifacts)
        _write_tuning_frontier(run_root, layer, dataset, artifacts)
    if native_phase == "test":
        if getattr(args, "defer_aggregate", False):
            return
        rows = [row for path, artifact in artifacts for row in flatten_artifact(path, artifact)]
        if isolated:
            from diskfair.system03 import PROTOCOL as SYSTEM03_PROTOCOL, check_rows
            for row in rows:
                row["system03_protocol"] = SYSTEM03_PROTOCOL
            check_rows(rows, tuple(spec.method for spec in specs))
        if not FAST_MODE:
            validate_layer_completeness(
                rows,
                layer=layer,
                dataset=dataset,
                repeats=args.repeats,
                workers=workers,
                budget_gib=args.search_dram_budget_gib, cache_mode=args.cache_mode,
                baseline_budgets=args.baseline_native_budgets,
                methods=[spec.method for spec in specs], storage_modes=storage_modes,
            )
        aggregate = dataset_run_root(run_root, layer, dataset) / "tables" / "formal_test_rows.csv"
        formal_rows = rows if FAST_MODE else _single_test_rows(rows)
        atomic_write_csv(aggregate, rows)
        frontier_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in formal_rows:
            key = (
                str(row["method"]),
                str(row.get("ablation") or ""),
                str(row["workers"]),
                str(row.get("storage_mode") or ""),
                str(row["search_dram_budget_gib"]), str(row["cache_mode"]),
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
                str(row.get("storage_mode") or ""),
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
                "protocol_id": PROTOCOL_ID,
                "formal_ready": not FAST_MODE,
                "experiment_group": args.experiment_group,
                "experiment": experiment_directory(layer, run_root),
                "search_dram_budget_gib": args.search_dram_budget_gib,
                "memory_comparison": "shared_ram_budget_rss",
                "baseline_native_budgets": args.baseline_native_budgets,
                "cache_mode": "c0" if layer == "05a" else args.cache_mode,
                "storage_modes": list(storage_modes),
                "raw_csv_sha256": sha256_file(aggregate),
                "statistic": "single_run",
                "system03_protocol": rows[0].get("system03_protocol", "") if rows else "",
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
        _finalize_dataset_manifest(run_root, args.out_root, args.run_id, layer, dataset)


def _fixed_parameter_lock(*, args, run_root, layer, dataset, preparations):
    """Record user-fixed parameters, without validation scores or test tuning."""
    selected = {}
    for spec in specs_for(layer, args.methods or None):
        if spec.method in ("Ours-Disk", "DiskANN-PQ-Disk", "Starling-Disk", "AiSAQ-Disk"):
            config = f"beam{args.fixed_beam}"
        elif spec.method in ("Glass-NSG-DiskPort", "SymphonyQG-DiskPort"):
            config = FAST_CONFIG_IDS[(layer, spec.method)]
        else:
            raise ContractError(f"fixed-parameter pipeline unsupported: {spec.method}")
        for mode in spec.storage_modes:
            if args.storage_modes != ("auto",) and mode not in args.storage_modes:
                continue
            entry = dict(config_id=config, parameter_source="user_fixed", search_widths=list(getattr(args,"search_widths",None) or [10,20,40,60,100,160,240,400,580]),
                         target_recall=None, target_reached=None, max_measured_recall=None,
                         validation_artifact=None, validation_artifact_sha256=None,
                         note="No validation performance sweep or test-based parameter selection.")
            if spec.method == "Ours-Disk":
                key = f"{spec.method}::{mode}"
                if key not in preparations:
                    raise ContractError("Ours fixed parameters require validation-only hot preparation")
                entry.update({k:v for k,v in preparations[key].items() if k.startswith("ours_") or k == "pca_assets_sha256"})
            if getattr(args, "system03_fixed", False) and spec.method in ("Starling-Disk", "AiSAQ-Disk"):
                from diskfair.official_system03 import configuration
                entry["official_configuration"] = configuration(spec.method, dataset)
            preflight = preparations.get(f"resource::{spec.method}::{mode}")
            if preflight:
                entry["resource_preflight"] = preflight
            selected[f"{spec.method}::{mode}"] = entry
    lock = dict(schema_version=2, layer=layer, dataset=dataset, phase="fixed_parameters",
                selection_policy="user_fixed_no_validation_performance_sweep", selected=selected)
    if getattr(args, "system03_fixed", False):
        from diskfair.system03 import PROTOCOL as SYSTEM03_PROTOCOL
        lock.update(system03_protocol=SYSTEM03_PROTOCOL, beam=4, repeats=1, workers=32,
                    budget_bytes=4*(1<<30), ours_cache_allocation="graph_first",
                    representation_selection="full_else_pca512_256_128_v1")
    path = dataset_run_root(run_root, layer, dataset) / "manifests" / "fixed_parameters.lock.json"
    if path.exists():
        if json.loads(path.read_text()) != lock:
            raise ContractError("fixed parameter lock changed; refusing to retune")
    else:
        atomic_write_json(path, lock)
    return path


def _run_fixed_pipeline(*, args, ports, run_root, layer, dataset):
    import copy
    if layer != "05c":
        raise ContractError("fixed-parameter pipeline is for complete system searches")
    order = list(args.methods) or [s.method for s in specs_for(layer)]
    preparations = {}
    if getattr(args, "system03_fixed", False):
        for method in order:
            if method == "Ours-Disk":
                continue
            preflight = copy.copy(args)
            preflight.methods = (method,)
            preflight.phase = "validate"
            preflight.resource_preflight = True
            records = _run_layer_dataset(args=preflight, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
            for path, record in records:
                preparations[f"resource::{method}::{record['storage_mode']}"] = dict(
                    path=str(path.resolve()), sha256=sha256_file(path),
                    purpose="validation resource/correctness only; no QPS selection")
    if "Ours-Disk" in order:
        prepare = copy.copy(args)
        prepare.methods = ("Ours-Disk",)
        prepare.phase = "tune"  # supplies validation split, never test queries
        prepare.fixed_preparation = True
        print(f"[{layer}/{dataset}] Ours validation-only hot preparation; performance sweep disabled", flush=True)
        artifacts = _run_layer_dataset(args=prepare, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
        preparations.update({f"{a['method']}::{a['storage_mode']}":a for _,a in artifacts})
    lock = _fixed_parameter_lock(args=args, run_root=run_root, layer=layer, dataset=dataset, preparations=preparations)
    for method in order:
        current = copy.copy(args)
        current.methods = (method,)
        current.phase = "run"
        current.tuning_lock_override = lock
        current.defer_aggregate = True
        print(f"[{layer}/{dataset}] fixed parameters recorded; {method} direct test with reference/resource admission", flush=True)
        _run_layer_dataset(args=current, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
    final = copy.copy(args)
    final.phase = "run"
    final.tuning_lock_override = lock
    _run_layer_dataset(args=final, ports=ports, run_root=run_root, layer=layer, dataset=dataset)


def _run_method_pipeline(*, args, ports, run_root, layer, dataset):
    """Freeze each method's lock before test; publish only after all finish."""
    import copy
    if getattr(args, "fixed_beam", None) is not None:
        return _run_fixed_pipeline(args=args, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
    specs = specs_for(layer, args.methods or None)
    # Preserve the requested scheduling order without changing the method set.
    order = list(args.methods) or [s.method for s in specs]
    for method in order:
        current = copy.copy(args)
        current.methods = (method,)
        current.phase = "tune"
        print(f"[{layer}/{dataset}] method pipeline: {method} validation", flush=True)
        _run_layer_dataset(args=current, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
        manifests = dataset_run_root(run_root, layer, dataset) / "manifests"
        lock = json.loads((manifests / "tuning.lock.json").read_text())
        frontier = manifests / f"tuning_frontier.{_safe(method)}.csv"
        contents = Path(lock["frontier_csv"]).read_bytes()
        if frontier.exists() and frontier.read_bytes() != contents:
            raise ContractError(f"frozen method frontier changed: {method}")
        if not frontier.exists():
            frontier.write_bytes(contents)
        lock.update(frontier_csv=str(frontier), frontier_csv_sha256=sha256_file(frontier))
        frozen = manifests / f"tuning.{_safe(method)}.lock.json"
        if frozen.exists() and json.loads(frozen.read_text()) != lock:
            raise ContractError(f"frozen method tuning changed: {method}")
        if not frozen.exists():
            atomic_write_json(frozen, lock)
        current.phase = "run"
        current.tuning_lock_override = frozen
        current.defer_aggregate = True
        print(f"[{layer}/{dataset}] method pipeline: {method} locked; starting test", flush=True)
        _run_layer_dataset(args=current, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
    # Revalidate saved evidence and aggregate the complete declared method set.
    final = copy.copy(args)
    final.phase = "tune"
    _run_layer_dataset(args=final, ports=ports, run_root=run_root, layer=layer, dataset=dataset)
    final.phase = "run"
    _run_layer_dataset(args=final, ports=ports, run_root=run_root, layer=layer, dataset=dataset)


def parse_search_widths(value):
    try:
        widths=tuple(int(x) for x in value.split(','))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("widths must be comma-separated integers") from exc
    if not widths or any(w<10 for w in widths) or list(widths)!=sorted(set(widths)):
        raise argparse.ArgumentTypeError("widths must be increasing, unique and >= 10")
    return widths


def make_parser(default_layer: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Disk experiments: 01 quantizer / 02 shared graph / 03 full system / 05 memory budgets"
    )
    parser.add_argument(
        "--layers",
        "--layer",
        dest="layers",
        default=default_layer or "01,02,03",
        help="01,02,03 or all; 05 runs memory budgets separately; native 05a/05b/05c IDs remain accepted",
    )
    parser.add_argument(
        "--phase",
        choices=("doctor", "export", "validate", "tune", "run", "plot"),
        required=True,
    )
    parser.add_argument("--datasets", "--dataset", dest="datasets", default="gist,bigann10m,agnews,dbpedia" if default_layer == "03" else "agnews,gist,dbpedia")
    parser.add_argument("--disk-root", type=Path)
    parser.add_argument(
        "--disk-profile",
        choices=("auto", "nvme", "hdd_raid"),
        default=os.environ.get("DISK_PROFILE", "auto"),
        help="physical disk profile; auto requires rotational or actual NVMe device evidence",
    )
    parser.add_argument("--ports", type=Path, default=PACKAGE_DIR / ("ports.fixed03.local.json" if default_layer == "03" else "ports.local.json"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--out-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--search-dram-budget-gib", type=float, default=None,
                        help="shared RAM budget; 03 defaults to 4 GiB, 01/02/05 to 2 GiB; RSS admission, no OS cap")
    parser.add_argument("--baseline-native-budget", action="append", default=[], metavar="METHOD=GIB",
                        help="compatibility option; must equal the shared RAM budget")
    parser.add_argument("--cgroup-parent", type=Path, default=None)
    parser.add_argument("--cpu-affinity", default="", help="explicit comma-separated logical CPU IDs; required for measured phases")
    parser.add_argument("--numa-node", type=int, help="explicit memory node; numactl membind must succeed")
    parser.add_argument("--experiment-group", choices=("primary", "budget_scan", "thread_scaling"), default=None,
                        help="05 defaults to budget_scan; other experiments default to primary")
    parser.add_argument("--cache-mode", choices=("standard", "c0"), default="standard",
                        help="native cache policy; c0 is a separately labelled controlled experiment")
    parser.add_argument(
        "--storage-modes",
        default="auto",
        help="auto selects every required mode for each layer; otherwise use a comma-separated subset",
    )
    parser.add_argument("--workers", default="32")
    parser.add_argument("--methods", default="", help="optional comma-separated method filter")
    parser.add_argument("--search-widths", type=parse_search_widths, default=None)
    parser.add_argument("--ours-cache-allocation", choices=("graph_first", "records_only"), default="graph_first")
    parser.add_argument("--ours-reference-source", type=Path, help="Admitted graph_first source for records_only exact cache-pair proof")
    parser.add_argument("--ours-hot-profile", type=Path, help="Reuse a verified validation logical-access profile")
    parser.add_argument("--ours-frozen-route", type=Path, help="Frozen resident representation plan from paired preparation")
    parser.add_argument("--fixed-beam", type=int, choices=(1,2,4,8,16,32), default=None,
                        help="fixed Ours/DiskANN beam for validation and test; other methods unchanged")
    parser.add_argument("--system03-fixed", action="store_true", default=default_layer == "03",
                        help="03 official beam4/single-run/independent-warmup protocol")
    parser.add_argument("--method-pipeline", action="store_true",
                        help="with --phase run: serial validation, immutable tuning lock and test per method")
    parser.add_argument("--repeats", type=int, default=FORMAL_REPEATS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--val-queries",
        type=int,
        default=0,
        help="validation query count; 0 selects the dataset policy default",
    )
    return parser


def configure_experiment(args: argparse.Namespace, default_layer: str | None = None) -> None:
    args.experiment = memory_experiment(args.layers)
    args.layers = normalize_layers(args.layers)
    if default_layer and (args.layers != normalize_layers(default_layer)
                          or args.experiment != memory_experiment(default_layer)):
        raise ContractError("this entry point runs only its own experiment layer")
    if args.experiment == MEMORY_EXPERIMENT:
        if args.experiment_group not in (None, "budget_scan"):
            raise ContractError("05 memory budgets require experiment-group budget_scan")
        args.experiment_group = "budget_scan"
    else:
        if args.experiment_group == "budget_scan":
            raise ContractError("memory budgets are experiment 05; use experiments/05_memory_budget/run.py or --layers 05")
        args.experiment_group = args.experiment_group or "primary"
    args.primary_budget_gib = (SYSTEM_PRIMARY_BUDGET_GIB
                              if args.experiment != MEMORY_EXPERIMENT and "05c" in args.layers
                              else DEFAULT_BUDGET_GIB)
    if args.search_dram_budget_gib is None:
        args.search_dram_budget_gib = args.primary_budget_gib


def validate_primary_budget(args: argparse.Namespace) -> None:
    if args.experiment_group != "budget_scan" and args.search_dram_budget_gib != args.primary_budget_gib:
        raise ContractError(f"primary RAM budget is {args.primary_budget_gib:g} GiB for these layers; "
                            "other budgets require independent experiment 05")


def run(argv: list[str] | None = None, default_layer: str | None = None) -> int:
    parser = make_parser(default_layer)
    args = parser.parse_args(argv)
    try:
        configure_experiment(args, default_layer)
        from diskfair.system03 import apply_defaults as apply_system03
        apply_system03(args)
        if args.system03_fixed and args.phase in ("validate", "run"):
            from diskfair.system03 import storage_errors
            errors = storage_errors(args.disk_root)
            if errors:
                raise ContractError("; ".join(errors))
        args.datasets = _csv_list(args.datasets)
        args.workers = _int_list(args.workers)
        args.baseline_native_budgets = {}
        baseline_names = {s.method for layer in LAYER_DIRS for s in specs_for(layer)} - {"Ours-Disk", "Ours_RaBitQ_K1"}
        # Supplementary methods are opt-in but also accept native configurations.
        from diskfair.native_contract import METHOD_SPECS
        baseline_names.update(s.method for s in METHOD_SPECS if s.method not in ("Ours-Disk", "Ours_RaBitQ_K1"))
        for setting in args.baseline_native_budget:
            name, separator, raw = setting.partition("=")
            if not separator or name not in baseline_names or name in args.baseline_native_budgets:
                raise ContractError("--baseline-native-budget requires a unique baseline METHOD=GIB")
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                raise ContractError("native configuration budgets must be finite and positive")
            if value != args.search_dram_budget_gib:
                raise ContractError('baseline budget must equal --search-dram-budget-gib')
            args.baseline_native_budgets[name] = value
        if args.cgroup_parent is not None:
            raise ContractError('RAM-budget protocol does not use --cgroup-parent; use the explicit legacy cgroup runner')
        args.storage_modes = _csv_list(args.storage_modes)
        args.methods = _csv_list(args.methods)
        args.cpu_affinity = tuple(sorted(set(int(x) for x in _csv_list(args.cpu_affinity)))) or None
        if args.cpu_affinity and (min(args.cpu_affinity) < 0 or not set(args.cpu_affinity) <= os.sched_getaffinity(0)):
            raise ContractError("CPU affinity contains unavailable logical CPUs")
        args.data_root = resolve_repo_path(args.data_root, REPO_ROOT)
        args.out_root = resolve_repo_path(args.out_root, REPO_ROOT)
        args.ports = resolve_repo_path(args.ports, REPO_ROOT)
        args.run_id = validate_run_id(args.run_id or _default_run_id())
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
                ports_path=args.ports,
                disk_root=args.disk_root,
                disk_profile=args.disk_profile,
                search_dram_budget_gib=args.search_dram_budget_gib,
                val_queries=args.val_queries,
            )
        if args.repeats != FORMAL_REPEATS:
            raise ContractError("this protocol runs each configuration once; use --repeats 1")
        if args.search_dram_budget_gib not in BUDGET_GRID_GIB:
            raise ContractError(f"budget must be one of {BUDGET_GRID_GIB}; use a separate run-id per budget")
        validate_primary_budget(args)
        if len(args.workers) != 1:
            raise ContractError("use a separate run-id for each worker count so tuning stays matched")
        if args.experiment_group != "thread_scaling" and args.workers != (32,):
            raise ContractError("primary and budget_scan require 32 workers; use thread_scaling otherwise")
        if args.experiment_group == "thread_scaling" and args.workers[0] not in (1,4,8,16,32):
            raise ContractError("thread_scaling supports 1,4,8,16,32 workers")
        if FAST_MODE and args.phase in ("tune", "run", "plot"):
            raise ContractError("QG05_FAST is diagnostic only; it cannot emit formal aggregates or plots")
        if args.method_pipeline and args.phase != "run":
            raise ContractError("--method-pipeline requires --phase run")
        if args.phase in ("export", "validate", "tune", "run"):
            if args.cpu_affinity is None or args.numa_node is None:
                raise ContractError("pin --cpu-affinity and --numa-node for the entire run, including export")
            if len(args.cpu_affinity) < args.workers[0] or args.numa_node < 0 or not shutil.which("numactl"):
                raise ContractError("require at least one selected CPU per worker and numactl for NUMA binding")
        if "05a" in args.layers and args.storage_modes not in (
            ("auto",),
            ("resident", "payload_on_ssd"),
        ):
            raise ContractError(
                "formal 05A requires paired --storage-modes resident,payload_on_ssd"
            )
        if args.seed != SEED:
            raise ContractError(f"formal suite seed is fixed at {SEED}")
        if args.phase in ("export", "validate", "tune", "run"):
            admission_errors: list[str] = []
            for dataset in args.datasets:
                try:
                    admission_errors.extend(
                        _dataset_admission_errors(
                            dataset,
                            args.layers,
                            args.data_root,
                            args.search_dram_budget_gib,
                            args.methods,
                        )
                    )
                except ValueError as exc:
                    admission_errors.append(str(exc))
            if admission_errors:
                raise ContractError(
                    "dataset selection is not formal-ready:\n  - "
                    + "\n  - ".join(admission_errors)
                )
        if args.phase == "plot":
            command = [
                sys.executable,
                str(PACKAGE_DIR / "plot_05_disk_suite.py"),
                "--run-root", str(run_metadata_root(args.out_root, args.run_id)),
                "--layers", "05" if args.experiment == MEMORY_EXPERIMENT else ",".join(args.layers),
                "--datasets", ",".join(args.datasets),
            ]
            if args.methods:
                command.extend(["--methods", ",".join(args.methods)])
            status = subprocess.call(command)
            if status == 0:
                run_root = run_metadata_root(args.out_root, args.run_id)
                for layer in args.layers:
                    for dataset in args.datasets:
                        _finalize_dataset_manifest(run_root, args.out_root, args.run_id, layer, dataset)
            return status
        if args.disk_root is None:
            args.disk_root = DEFAULT_DISK_ROOT
        args.disk_root = resolve_repo_path(args.disk_root, REPO_ROOT)
        if args.disk_profile == "auto":
            args.disk_profile = _auto_disk_profile(args.disk_root)
        ports = load_port_registry(args.ports)
        validate_registry(ports, args.layers, args.methods or None)
        run_root = run_metadata_root(args.out_root, args.run_id)
        config = {"layout_version": LAYOUT_VERSION, "protocol_id": PROTOCOL_ID, "workers": list(args.workers),
                  "budget_gib": args.search_dram_budget_gib, "cache_mode": args.cache_mode,
                  "experiment_group": args.experiment_group, "repeats": args.repeats,
                  "seed": args.seed, "methods": list(args.methods), "layers": list(args.layers),
                  "datasets": list(args.datasets), "storage_modes": list(args.storage_modes),
                  "val_queries": args.val_queries, "disk_root": str(args.disk_root),
                  "data_root": str(args.data_root), "ports_registry_sha256": sha256_file(args.ports)}
        config["effective_methods"] = {
            layer: [spec.method for spec in specs_for(layer, args.methods or None)]
            for layer in args.layers
        }
        config.update(cpu_affinity=list(args.cpu_affinity), numa_node=args.numa_node,
                      ours_cache_allocation=args.ours_cache_allocation,
                      ours_reference_source=str(args.ours_reference_source) if args.ours_reference_source else None,
                      ours_hot_profile=str(args.ours_hot_profile) if args.ours_hot_profile else None,
                      ours_frozen_route=str(args.ours_frozen_route) if args.ours_frozen_route else None)
        if args.search_widths is not None:
            config["search_widths"] = list(args.search_widths)
        if args.fixed_beam is not None:
            config["fixed_beam"] = args.fixed_beam
        config.update(memory_comparison="shared_ram_budget_rss",
                      baseline_native_budgets=args.baseline_native_budgets)
        if args.experiment is not None:
            config["experiment"] = args.experiment
        config_path = run_root / "protocol.json"
        if config_path.exists():
            if json.loads(config_path.read_text()) != config:
                raise ContractError("run configuration changed; use a fresh run-id")
        elif run_root.exists():
            raise ContractError("legacy/unversioned run directory; use a fresh run-id")
        else:
            atomic_write_json(config_path, config)
        if args.phase in ("validate", "tune", "run"):
            readiness_path = run_root / f"memory_environment_{args.phase}.json"
            selected_specs = [spec for layer in args.layers for spec in specs_for(layer, args.methods or None)]
            needs_limit = any(requires_memory_limit(spec.method, mode) for spec in selected_specs
                              for mode in spec.storage_modes
                              if args.storage_modes == ("auto",) or mode in args.storage_modes)
            try:
                readiness = (check_environment(args.cgroup_parent, int(args.search_dram_budget_gib*(1<<30)))
                             if needs_limit else {"status": "ram_budget_rss_ready",
                                                   "memory_enforcement": "none",
                                                   "planned_budget_bytes": int(args.search_dram_budget_gib*(1<<30)),
                                                   "note": "all methods share planned RAM budget; observed RSS admission"})
            except MemoryEnvironmentError as exc:
                atomic_write_json(readiness_path, {"status": "blocked_environment", "reason": str(exc),
                                                  "protocol_id": PROTOCOL_ID, "formal_ready": False})
                raise
            atomic_write_json(readiness_path, readiness)
        _formal_preflight(args.disk_root, args.out_root, args.run_id, args.disk_profile)
        invocation_path = run_root / f"invocation_{args.phase}.json"
        invocation = dict(config, schema_version=2, run_id=args.run_id,
                          argv=sys.argv if argv is None else argv, disk_profile=args.disk_profile,
                          ports_registry=str(args.ports))
        if invocation_path.exists():
            if json.loads(invocation_path.read_text()) != invocation:
                raise ContractError("phase invocation changed; use a fresh run-id")
        else:
            atomic_write_json(invocation_path, invocation)
        for layer in args.layers:
            for dataset in args.datasets:
                runner = _run_method_pipeline if args.method_pipeline else _run_layer_dataset
                runner(
                    args=args,
                    ports=ports,
                    run_root=run_root,
                    layer=layer,
                    dataset=dataset,
                )
        print(f"formal phase complete: run_id={args.run_id}")
        return 0
    except (ContractError, MemoryEnvironmentError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
