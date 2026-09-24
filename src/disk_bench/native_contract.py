"""Strict native-port contract for the formal 05 disk experiments.

The 05 suite is an orchestration and auditing layer.  It must never replace
the algorithms measured by experiments 01/02/03 with Python reference
implementations.  Every benchmark process therefore writes one self-contained
JSON artifact and this module verifies its provenance before it can enter an
aggregate CSV or a figure.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
try:
    from .results_paths import resolve_result_path
except ImportError:
    from results_paths import resolve_result_path
from typing import Any, Iterable
try:
    from .protocol import (PROTOCOL_ID, FORMAL_REPEATS, DEFAULT_BUDGET_GIB,
                           RESOURCE_FIELDS, validate_resource_evidence)
except ImportError:  # Standalone CLI/test imports.
    from protocol import (PROTOCOL_ID, FORMAL_REPEATS, DEFAULT_BUDGET_GIB,
                          RESOURCE_FIELDS, validate_resource_evidence)

SCHEMA_VERSION = 2
PAGE_SIZE = 4096
FORMAL_WARMUP_QUERIES = 100
FORMAL_WORKERS = (32,)
SEED = 20260813


def single_test_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy diagnostic reader only; new formal outputs use aggregate_repeats."""
    keys = ("dataset", "method", "storage_mode", "cache_mode", "search_dram_budget_gib",
            "workers", "config_id", "search_param", "search_width", "beam_width", "ablation")
    seen = set()
    result = []
    for row in rows:
        if str(row.get("repeat_id")) not in ("0", "0.0"):
            raise ContractError("legacy single-run diagnostics require repeat_id=0")
        key = tuple(str(row.get(k, "")) for k in keys)
        if key in seen:
            raise ContractError(f"duplicate legacy operating point: {key}")
        seen.add(key)
        result.append(dict(row, repeat_id=0))
    return result


@dataclass(frozen=True)
class MethodSpec:
    layer: str
    method: str
    source_suite: str
    source_kernel: str
    storage_modes: tuple[str, ...]
    port_kind: str
    comparison_group: str = "primary"

    @property
    def key(self) -> str:
        return f"{self.layer}:{self.method}"


METHOD_SPECS: tuple[MethodSpec, ...] = (
    MethodSpec("05a", "PQ_4bit", "01_quantizer_fair", "faiss::ProductQuantizer", ("resident", "payload_on_ssd"), "algorithm_preserving_disk_port"),
    MethodSpec("05a", "SQ_4bit", "01_quantizer_fair", "faiss::ScalarQuantizer(QT_4bit)", ("resident", "payload_on_ssd"), "algorithm_preserving_disk_port"),
    MethodSpec("05a", "SAQ_B4", "01_quantizer_fair", "official SAQ B=4 distance kernel", ("resident", "payload_on_ssd"), "algorithm_preserving_disk_port"),
    MethodSpec("05a", "Ours_RaBitQ_K1", "01_quantizer_fair", "hnswlib::RaBitQSpace K=1", ("resident", "payload_on_ssd"), "algorithm_preserving_disk_port"),
    MethodSpec("05b", "PQ-DiskANN-Disk", "02_diskann_fair", "DiskANN FixedChunkPQTable 4-bit", ("hybrid_disk", "disk_payload"), "algorithm_preserving_disk_port"),
    MethodSpec("05b", "SQ-DiskANN-Disk", "02_diskann_fair", "DiskANN ScalarQuantizer<4>", ("hybrid_disk", "disk_payload"), "algorithm_preserving_disk_port"),
    MethodSpec("05b", "SAQ-DiskANN-Disk", "02_diskann_fair", "DiskANN spherical::Impl<4>", ("hybrid_disk", "disk_payload"), "algorithm_preserving_disk_port"),
    MethodSpec("05b", "Ours-Disk", "02_diskann_fair", "ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search", ("hybrid_disk",), "algorithm_preserving_disk_port"),
    MethodSpec("05c", "Ours-Disk", "03_system_fair", "ExRaBitQ4 symmetric Vamana; resident DB1 or budget-selected PCA+1bit M32 (no residual)", ("hybrid_disk",), "native_disk_system"),
    MethodSpec("05c", "SymphonyQG-DiskPort", "03_system_fair", "official SymphonyQG FastScan LUT+SIMD", ("hybrid_disk",), "algorithm_preserving_disk_port"),
    MethodSpec("05c", "OG-LVQ-DiskPort", "03_system_fair", "official SVS LVQ4 distance kernel", ("hybrid_disk",), "algorithm_preserving_disk_port", "supplementary"),
    MethodSpec("05c", "Glass-NSG-DiskPort", "03_system_fair", "official Glass NSG SQ4U distance kernel", ("hybrid_disk",), "algorithm_preserving_disk_port", "supplementary"),
    MethodSpec("05c", "DiskANN-PQ-Disk", "03_system_fair", "official diskann-disk PQ search", ("hybrid_disk",), "official_native_disk"),
    MethodSpec("05c", "AiSAQ-Disk", "official_disk_baselines", "official AiSAQ PQFlashIndex with use_aisaq", ("hybrid_disk",), "official_native_disk", "supplementary"),
    MethodSpec("05c", "Starling-Disk", "official_disk_baselines", "official Starling PQ page search with navigation graph", ("hybrid_disk",), "official_native_disk"),
)

SPECS_BY_KEY = {s.key: s for s in METHOD_SPECS}
LAYER_METHODS = {
    layer: tuple(s.method for s in METHOD_SPECS if s.layer == layer and s.comparison_group == "primary")
    for layer in ("05a", "05b", "05c")
}

OURS_05B_ABLATIONS = (
    "full4-resident/no-gate",
    "db1-resident/full4-on-ssd",
    "db1+coalescing",
    "db1+coalescing+reuse",
)

SUMMARY_FIELDS = (
    "experiment",
    "layer", "dataset", "method", "storage_mode", "phase", "run_id", "system03_protocol", "source_commit",
    "repeat_id", "workers", "search_dram_budget_gib", "config_id", "search_param",
    "search_width", "ablation", "cache_mode",
    "beam_width", "recall", "qps", "latency_mean_us", "latency_p50_us",
    "latency_p95_us", "latency_p99_us", "fixed_candidate_recall_at_10",
    "mean_relative_error", "p95_relative_error", "pairwise_flip_rate",
    "code_bytes_per_vector", "effective_bits_per_dim", "read_amplification",
    "index_size_mb", "resident_bytes", "cache_bytes", "cache_nodes",
    "peak_rss_bytes", "ours_4bit_payload_bytes", "ours_8bit_payload_bytes",
    "ours_adjacency_bytes", "ours_fp32_base_bytes",
    "ours_route_policy", "ours_route_mode", "ours_route_dimension", "ours_route_m",
    "ours_route_residual_norm_bytes", "ours_route_required_bytes", "pca_assets_sha256",
    "graph_io_requests_per_query", "graph_bytes_read_per_query", "graph_shared_cache_hits_per_query", "graph_query_cache_hits_per_query",
    "full4_record_io_requests_per_query", "full4_record_bytes_read_per_query", "full4_record_query_cache_hits_per_query",
    "rerank_record_io_requests_per_query", "rerank_record_bytes_read_per_query", "rerank_record_query_cache_hits_per_query",
    "ours_cache_allocation", "ours_record_cache_policy", "ours_hot_profile_sha256", "ours_record_cache_budget_bytes",
    "ours_record_cache_reserved_bytes", "ours_graph_cache_bytes", "hot_record_nodes",
    "hot_record_reserved_bytes", "hot_record_part_hits", "dynamic_record_capacity_nodes",
    "dynamic_record_resident_nodes", "dynamic_record_reserved_bytes", "dynamic_record_valid_payload_bytes",
    "dynamic_record_part_hits", "dynamic_record_part_misses", "dynamic_record_insertions", "dynamic_record_evictions",
    "io_requests_per_query", "sectors_4k_per_query",
    "bytes_read_per_query", "io_wait_us", "distance_compute_us",
    "query_prep_us", "queue_compute_us", "rerank_us", "visited_nodes", "traversal_wall_us",
    "distance_evaluations", "db1_checks", "db1_survivors",
    "full4_candidates", "full4_page_reads", "rerank_candidates",
    "rerank_page_reads", "query_count", "source_suite", "source_kernel",
    "implementation_fingerprint", "native_binary_sha256", "input_manifest_sha256",
    "source_index_manifest_sha256",
    "query_split_sha256", "query_order_sha256",
    "query_order_seed", "shared_graph_sha256", "source_graph_sha256", "graph_role",
    "direct_io", "native_aio", "page_size", "formal_ready", "artifact_path",
    *RESOURCE_FIELDS,
    "storage_precondition_protocol", "storage_precondition_path", "storage_precondition_sha256",
    "separate_reference_protocol", "separate_reference_path", "separate_reference_sha256",
    "memory_acceptance_basis",
    "native_search_result_path", "native_search_result_sha256",
)


class ContractError(RuntimeError):
    """Raised when a result could not have come from a fair formal run."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with resolve_result_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(SUMMARY_FIELDS), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def specs_for(layer: str, methods: Iterable[str] | None = None) -> list[MethodSpec]:
    wanted = set(methods or LAYER_METHODS[layer])
    unknown = wanted - {s.method for s in METHOD_SPECS if s.layer == layer}
    if unknown:
        raise ContractError(f"{layer}: unknown methods: {sorted(unknown)}")
    return [s for s in METHOD_SPECS if s.layer == layer and s.method in wanted]


def load_port_registry(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise ContractError(
            f"missing native port registry: {path}. Copy ports.example.json to "
            "ports.local.json and point every entry at a real native disk runner"
        )
    doc = json.loads(path.read_text())
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(
            f"port registry schema={doc.get('schema_version')}, expected {SCHEMA_VERSION}"
        )
    ports = doc.get("ports")
    if not isinstance(ports, dict):
        raise ContractError("port registry must contain an object named 'ports'")
    return ports


def validate_registry(ports: dict[str, dict[str, Any]], layers: Iterable[str],
                      methods: Iterable[str] | None = None) -> None:
    required = [s for layer in layers for s in specs_for(layer, methods)]
    errors: list[str] = []
    for spec in required:
        port = ports.get(spec.key)
        if not port:
            errors.append(f"missing {spec.key}")
            continue
        command = port.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            errors.append(f"{spec.key}: command must be a non-empty argv list")
        if port.get("status") != "ready":
            reason = port.get("blocked_reason", "")
            errors.append(f"{spec.key}: status must be 'ready' (got {port.get('status')!r}) {reason}".rstrip())
        if port.get("source_suite") != spec.source_suite:
            errors.append(f"{spec.key}: source_suite must be {spec.source_suite}")
        if port.get("source_kernel") != spec.source_kernel:
            errors.append(f"{spec.key}: source_kernel mismatch")
        if port.get("port_kind") != spec.port_kind:
            errors.append(f"{spec.key}: port_kind must be {spec.port_kind}")
        if port.get("status") == "ready":
            if not port.get("implementation_fingerprint"):
                errors.append(f"{spec.key}: implementation_fingerprint is required")
            binary_sha = str(port.get("binary_sha256", ""))
            if len(binary_sha) != 64 or any(c not in "0123456789abcdef" for c in binary_sha.lower()):
                errors.append(f"{spec.key}: binary_sha256 must be a 64-digit hex digest")
    if errors:
        raise ContractError("native disk ports are incomplete:\n  - " + "\n  - ".join(errors))


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ContractError(f"{name} is not finite: {value!r}")
    return number


def validate_artifact(
    path: Path,
    *,
    spec: MethodSpec,
    expected: dict[str, Any],
    disk_root: Path,
    require_formal_ready: bool = True,
) -> dict[str, Any]:
    """Validate provenance, storage semantics and numerical result schema."""
    if not path.exists():
        raise ContractError(f"native runner did not create {path}")
    path = resolve_result_path(path)
    artifact = json.loads(path.read_text())
    if artifact.get("artifact_kind") == "official_cli_system03_v1":
        from .official_system03 import validate
        return validate(artifact, {**expected, 'layer':spec.layer, 'method':spec.method,
                        'source_suite':spec.source_suite, 'source_kernel':spec.source_kernel,
                        'port_kind':spec.port_kind}, disk_root)
    errors: list[str] = []
    if artifact.get("system03_protocol"):
        from .system03 import validate_point
        try:
            validate_point(artifact)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            errors.append(f"fixed 03 admission: {exc}")

    exact = {
        "schema_version": SCHEMA_VERSION,
        "status": "done",
        "layer": spec.layer,
        "method": spec.method,
        "source_suite": spec.source_suite,
        "source_kernel": spec.source_kernel,
        "port_kind": spec.port_kind,
        **{k: v for k, v in expected.items() if k != "artifact_path"},
    }
    for key, value in exact.items():
        if artifact.get(key) != value:
            errors.append(f"{key}: got {artifact.get(key)!r}, expected {value!r}")

    storage_mode = artifact.get("storage_mode")
    if storage_mode not in spec.storage_modes:
        errors.append(f"storage_mode {storage_mode!r} not in {spec.storage_modes}")
    index_path = resolve_result_path(str(artifact.get("index_path", "")))
    if not index_path.is_absolute() or not _inside(index_path, disk_root):
        errors.append(f"index_path must be inside disk root {disk_root}: {index_path}")
    elif not index_path.exists():
        errors.append(f"index_path does not exist: {index_path}")
    # User-authorized diagnostic runs may skip external parity.  They retain
    # all schema/resource checks but are explicitly marked formal_ready=false.
    if require_formal_ready and artifact.get("formal_ready") is not True:
        errors.append("formal_ready must be true")
    if artifact.get("page_size") != PAGE_SIZE:
        errors.append(f"page_size must be {PAGE_SIZE}")
    if artifact.get("whole_graph_in_memory") is not False:
        errors.append("whole_graph_in_memory must be false")
    expected_payload_in_memory = spec.layer == "05a" and storage_mode == "resident"
    if artifact.get("whole_payload_in_memory") is not expected_payload_in_memory:
        errors.append(
            "whole_payload_in_memory must be "
            f"{expected_payload_in_memory} for {spec.layer}/{storage_mode}"
        )
    if storage_mode != "resident":
        if artifact.get("direct_io") is not True:
            errors.append("disk-backed mode must use O_DIRECT")
        if artifact.get("native_aio") is not True:
            errors.append("disk-backed mode must use native asynchronous I/O")
        expected_io_backend = (
            "linux_io_uring_odirect"
            if spec.method == "DiskANN-PQ-Disk"
            else "linux_native_aio_odirect"
        )
        if artifact.get("io_backend") != expected_io_backend:
            errors.append(
                f"{spec.key} io_backend must be {expected_io_backend} "
                f"(got {artifact.get('io_backend')!r})"
            )
    elif artifact.get("io_backend") != "resident":
        errors.append("resident mode io_backend must be resident")
    if not artifact.get("implementation_fingerprint"):
        errors.append("implementation_fingerprint is required")
    if len(str(artifact.get("native_binary_sha256", ""))) != 64:
        errors.append("native_binary_sha256 is required")
    cache_mode = artifact.get("cache_mode")
    if cache_mode not in ("standard", "c0"):
        errors.append("cache_mode must be standard or c0")
    if spec.layer == "05a" and cache_mode != "c0":
        errors.append("05A must run with C=0")
    if cache_mode == "c0" and (
        int(artifact.get("cache_nodes", 0) or 0) != 0
        or int(artifact.get("cache_bytes", 0) or 0) != 0
    ):
        errors.append("cache_mode=c0 requires cache_nodes=cache_bytes=0")
    phase = str(artifact.get("phase", ""))
    # The CLI's historical `validate` alias must receive all validation checks.
    if phase == "validate":
        phase = "validation"
    measured_protocol = expected.get("protocol_id") == PROTOCOL_ID or artifact.get("protocol_id") == PROTOCOL_ID
    rss_accepted = False
    if measured_protocol and phase in ("validate", "validation", "test"):
        try:
            resource = validate_resource_evidence(artifact, dict(expected, artifact_path=str(path.resolve())))
            rss_accepted = resource.get("rss_budget") is True
            if spec.layer == "05c" and storage_mode != "resident":
                try:
                    from .admission import METHODS, PROTOCOL as PARITY_PROTOCOL, validate_storage, verify_reference
                except ImportError:  # Standalone entry points install diskfair.
                    from diskfair.admission import METHODS, PROTOCOL as PARITY_PROTOCOL, validate_storage, verify_reference
                validate_storage(artifact, resource)
                if spec.method in METHODS:
                    if artifact.get("separate_reference_protocol") != PARITY_PROTOCOL:
                        raise ValueError("separate native reference evidence is required")
                    measured_parity = verify_reference(artifact, resource,
                        artifact.get("separate_reference_path", ""), artifact.get("separate_reference_sha256", ""))
                    if artifact.get("parity") != measured_parity:
                        raise ValueError("artifact parity differs from separate measured reference")
        except (ValueError, OSError, KeyError, IndexError) as exc:
            errors.append(f"resource evidence: {exc}")
    if phase in ("validate", "validation", "test"):
        if artifact.get("implementation_parity") != "passed":
            errors.append("implementation_parity must be 'passed'")
        parity = artifact.get("parity")
        if not isinstance(parity, dict):
            errors.append("parity must contain measured reference evidence")
            parity = {}
        if not parity.get("reference_artifact_sha256"):
            errors.append("parity.reference_artifact_sha256 is required")
        recall_delta = _finite_number(
            parity.get("max_recall_delta", 1.0), "max_recall_delta"
        )
        parity_comparisons = parity.get("query_comparisons")
        if parity_comparisons is None:
            parity_path = path.with_suffix(".parity.json")
            if parity_path.exists():
                try:
                    parity_comparisons = json.loads(parity_path.read_text()).get("query_comparisons")
                except (OSError, json.JSONDecodeError):
                    parity_comparisons = None
        parity_comparisons = int(parity_comparisons if parity_comparisons is not None else 0)
        if parity_comparisons <= 0 and os.environ.get("QG05_FAST") != "1":
            errors.append("parity requires a positive measured query_comparisons count")
        parity_skipped_fast = os.environ.get("QG05_FAST") == "1" and parity_comparisons == 0
        if spec.layer == "05a":
            if recall_delta > 1e-12:
                errors.append("05A resident/direct-disk recall must be identical")
            if _finite_number(
                parity.get("max_distance_delta", 1.0), "max_distance_delta"
            ) > 1e-5:
                errors.append("05A parity max_distance_delta exceeds 1e-5")
        elif not parity_skipped_fast:
            if recall_delta > 1e-3:
                errors.append(f"{spec.layer.upper()} parity max_recall_delta exceeds 0.001")
            if _finite_number(
                parity.get("mean_top10_overlap", 0.0), "mean_top10_overlap"
            ) < 0.99:
                errors.append(f"{spec.layer.upper()} parity mean_top10_overlap is below 0.99")
            if _finite_number(
                parity.get("mean_visited_count_relative_delta", 1.0),
                "mean_visited_count_relative_delta",
            ) > 0.01:
                errors.append(
                    f"{spec.layer.upper()} parity visited-count relative delta exceeds 1%"
                )
            if _finite_number(
                parity.get("mean_distance_count_relative_delta", 1.0),
                "mean_distance_count_relative_delta",
            ) > 0.01:
                errors.append(
                    f"{spec.layer.upper()} parity distance-count relative delta exceeds 1%"
                )

    rows = artifact.get("summary_rows", [])
    if phase in ("validation", "test"):
        if spec.layer in ("05b", "05c") and artifact.get("measurement_scope") != "test_only_excludes_warmup":
            errors.append("measurement_scope must exclude warmup for all methods")
        if spec.layer in ("05b", "05c") and not (measured_protocol and spec.layer == "05c") and artifact.get("storage_cache_protocol") not in ("controlled_cold", "controlled_warm"):
            errors.append("storage cache protocol has not been verified")
        if not rss_accepted and artifact.get("memory_accounting_complete") is False:
            errors.append("search memory accounting is incomplete")
        elif not rss_accepted and spec.layer in ("05b", "05c") and artifact.get("memory_accounting_complete") is not True:
            errors.append("disk search requires verified complete memory accounting")
        for key in (
            "git_commit", "compiler", "simd", "base_count", "dimension",
            "search_dram_budget_gib", "resident_bytes", "codebook_bytes",
            "worker_scratch_bytes", "cache_bytes", "cache_nodes",
            "peak_rss_bytes", "cpu_affinity", "numa_node",
            "input_manifest_sha256", "source_index_manifest_sha256",
        ):
            # Reservation estimates remain distinct from measured categories.
            # Validated whole-process RSS covers all allocations under this protocol.
            if key == "worker_scratch_bytes" and rss_accepted:
                continue
            if artifact.get(key) in (None, ""):
                errors.append(f"{key} is required for {phase}")
        if artifact.get("query_order_sha256") != expected.get("query_order_sha256"):
            errors.append("query_order_sha256 mismatch")
        if artifact.get("query_order_seed") != expected.get("query_order_seed"):
            errors.append("query_order_seed mismatch")
        if spec.layer in ("05b", "05c"):
            base_count = int(artifact.get("base_count", 0) or 0)
            cache_nodes = int(artifact.get("cache_nodes", 0) or 0)
            if not measured_protocol and cache_nodes > int(base_count * 0.10):
                errors.append("cache_nodes exceeds 10% of the dataset")
            budget = float(artifact.get("search_dram_budget_gib", 0) or 0) * (1 << 30)
            accounted = sum(
                int(artifact.get(key, 0) or 0)
                for key in ("resident_bytes", "codebook_bytes", "worker_scratch_bytes", "cache_bytes")
            )
            if accounted > budget and (not measured_protocol or artifact.get("memory_enforcement") == "cgroup_v2"):
                errors.append(f"accounted search DRAM {accounted} exceeds budget {budget:.0f}")
        trace_path = resolve_result_path(str(artifact.get("query_trace_path", "")))
        if not trace_path.is_absolute() or not trace_path.exists():
            errors.append(f"query_trace_path is missing: {trace_path}")
        elif not artifact.get("query_trace_sha256"):
            errors.append("query_trace_sha256 is required")
        elif sha256_file(trace_path) != artifact.get("query_trace_sha256"):
            errors.append("query trace SHA-256 mismatch")
        if not isinstance(rows, list) or not rows:
            errors.append("summary_rows must be a non-empty list")
            rows = []
    elif not isinstance(rows, list):
        errors.append("summary_rows must be a list when present")
        rows = []
    for row_index, row in enumerate(rows):
        prefix = f"summary_rows[{row_index}]"
        for key in (
            "config_id", "search_param", "search_width", "beam_width", "recall",
            "qps", "latency_mean_us", "latency_p50_us", "latency_p95_us",
            "latency_p99_us", "query_count", "index_size_mb", "resident_bytes",
            "peak_rss_bytes", "io_requests_per_query", "sectors_4k_per_query",
            "bytes_read_per_query",
        ):
            if key not in row:
                errors.append(f"{prefix}.{key} is required")
        if not all(k in row for k in ("recall", "qps", "latency_p50_us", "latency_p95_us", "latency_p99_us")):
            continue
        recall = _finite_number(row["recall"], f"{prefix}.recall")
        qps = _finite_number(row["qps"], f"{prefix}.qps")
        p50 = _finite_number(row["latency_p50_us"], f"{prefix}.latency_p50_us")
        p95 = _finite_number(row["latency_p95_us"], f"{prefix}.latency_p95_us")
        p99 = _finite_number(row["latency_p99_us"], f"{prefix}.latency_p99_us")
        if not 0.0 <= recall <= 1.0:
            errors.append(f"{prefix}.recall outside [0,1]")
        if qps <= 0 or p50 <= 0 or not (p50 <= p95 <= p99):
            errors.append(f"{prefix}: require qps>0 and 0<p50<=p95<=p99")
        if storage_mode != "resident" and _finite_number(
            row.get("bytes_read_per_query", 0), f"{prefix}.bytes_read_per_query"
        ) < 0:
            errors.append(f"{prefix}: disk mode reports negative physical bytes read")
        if spec.layer == "05a":
            for key in (
                "code_bytes_per_vector",
                "effective_bits_per_dim",
                "read_amplification",
            ):
                if key not in row:
                    errors.append(f"{prefix}.{key} is required for 05A")
            fixed_recall = _finite_number(
                row.get("fixed_candidate_recall_at_10", -1),
                f"{prefix}.fixed_candidate_recall_at_10",
            )
            if abs(recall - fixed_recall) > 1e-12:
                errors.append(f"{prefix}: 05A recall must equal fixed-candidate recall")

    if phase in ("validation", "test") and rows and trace_path.exists():
        trace_required = {
            "layer", "storage_mode", "cache_mode", "dataset", "method", "config_id",
            "repeat_id", "query_id", "search_width", "beam_width", "workers",
            "search_dram_budget_gib", "cache_nodes", "resident_bytes",
            "cache_bytes", "peak_rss_bytes", "recall_at_10", "latency_us",
            "query_prep_us", "queue_compute_us", "io_wait_us",
            "distance_compute_us", "rerank_us", "visited_nodes",
            "distance_evaluations", "io_requests", "sectors_4k", "bytes_read",
            "average_read_bytes", "coalesced_requests", "duplicate_pages_removed",
            "shared_cache_hits", "shared_cache_misses", "query_cache_hits",
            "query_cache_misses",
        }
        if spec.layer == "05a":
            trace_required |= {
                "fixed_candidate_recall_at_10", "mean_relative_error",
                "p95_relative_error", "pairwise_flip_rate",
            }
        if spec.method.startswith("Ours"):
            trace_required |= {
                "db1_checks", "db1_survivors", "full4_candidates",
                "full4_page_reads", "rerank_candidates", "rerank_page_reads",
            }
        expected_trace_rows = sum(int(row.get("query_count", 0) or 0) for row in rows)
        trace_rows = 0
        with trace_path.open() as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                trace_rows += 1
                try:
                    trace = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"query trace line {line_number} is invalid JSON: {exc}")
                    break
                missing = trace_required - set(trace)
                if missing:
                    errors.append(
                        f"query trace line {line_number} missing fields: {sorted(missing)}"
                    )
                    break
                for key in ("layer", "storage_mode", "cache_mode", "dataset", "method", "repeat_id", "workers"):
                    if str(trace.get(key)) != str(artifact.get(key)):
                        errors.append(f"query trace line {line_number} has mismatched {key}")
                        break
        if trace_rows != expected_trace_rows:
            errors.append(
                f"query trace rows={trace_rows}, expected {expected_trace_rows} from summaries"
            )

    if spec.layer == "05b":
        source_graph_hash = artifact.get("source_graph_sha256")
        if not source_graph_hash:
            errors.append("05B source_graph_sha256 is required")
        if spec.method == "Ours-Disk":
            if artifact.get("graph_role") != "ours_native":
                errors.append("05B Ours must use graph_role=ours_native")
            if artifact.get("shared_graph_sha256") not in (None, ""):
                errors.append("05B Ours must not claim the baseline shared graph")
            got = tuple(artifact.get("ablations", ()))
            if got != OURS_05B_ABLATIONS:
                errors.append(f"Ours 05B ablations must exactly equal {OURS_05B_ABLATIONS}")
        else:
            graph_hash = artifact.get("shared_graph_sha256")
            if not graph_hash:
                errors.append("05B baseline shared_graph_sha256 is required")
            if artifact.get("graph_role") != "shared_baseline":
                errors.append("05B PQ/SQ/SAQ must use graph_role=shared_baseline")
            if source_graph_hash != graph_hash:
                errors.append("05B baseline source graph must equal its shared graph")
    if spec.layer == "05c" and spec.method == "Ours-Disk":
        if not artifact.get("source_graph_sha256"):
            errors.append("05C Ours source_graph_sha256 is required")
        if artifact.get("graph_role") != "ours_native":
            errors.append("05C Ours must use graph_role=ours_native")
        if artifact.get("shared_graph_sha256") not in (None, ""):
            errors.append("05C Ours must not claim the baseline shared graph")
        if phase in ("validate", "validation", "test") and artifact.get("implementation_fingerprint") in (
                "05c-ours-budget-pca1bit-m32-no-residual-v3", "05c-ours-pca1bit-hot-dynamic-v4",
                "05c-ours-pca1bit-hot-dynamic-v5", "05c-ours-cache-allocation-v6"):
            plan = artifact.get("ours_route_plan") or {}
            if plan.get("policy") != "resident_full_else_pca1bit_m32_no_residual_v1":
                errors.append("03 Ours requires the automatic resident/PCA routing plan")
            elif (plan.get("workers") != artifact.get("workers")
                  or plan.get("count") != artifact.get("base_count")
                  or plan.get("original_dimension") != artifact.get("dimension")
                  or plan.get("budget_bytes") != int(float(artifact["search_dram_budget_gib"])*(1<<30))
                  or plan.get("required_bytes", float("inf")) > plan["budget_bytes"]):
                errors.append("Ours routing plan does not match this run/budget")
            if plan.get("mode") == "pca1bit":
                if (plan.get("shortlist_m") != 32 or plan.get("residual_norm_bytes") != 0
                        or not 0 < plan.get("dimension", 0) < artifact.get("dimension", 0)
                        or not artifact.get("pca_assets_sha256")):
                    errors.append("PCA routing requires M32, no residual norm, and pinned assets")
            elif plan.get("mode") == "full1bit":
                if plan.get("dimension") != artifact.get("dimension") or artifact.get("pca_assets_sha256"):
                    errors.append("resident full 1-bit must preserve original dimensionality")
            else:
                errors.append("formal 03 Ours cannot silently fall back to paged routing")
            if artifact.get("implementation_fingerprint") in (
                    "05c-ours-pca1bit-hot-dynamic-v4", "05c-ours-pca1bit-hot-dynamic-v5", "05c-ours-cache-allocation-v6"):
                try:
                    validate_ours_record_cache(artifact)
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    errors.append(f"invalid Ours hot+dynamic evidence: {exc}")

    if errors:
        raise ContractError(f"invalid native artifact {path}:\n  - " + "\n  - ".join(errors))
    return artifact


def validate_ours_record_cache(artifact: dict[str, Any]) -> None:
    plan = artifact["ours_route_plan"]
    allocation = artifact.get("ours_cache_allocation", "graph_first")
    if allocation not in ("graph_first", "records_only"):
        raise ValueError("invalid Ours cache allocation")
    if allocation == "records_only" and artifact["ours_graph_cache_bytes"] != 0:
        raise ValueError("records_only cannot allocate shared graph cache")
    if artifact.get("implementation_fingerprint") == "05c-ours-cache-allocation-v6":
        if (plan.get("selection_policy") != "full_else_pca512_256_128_v1"
                or (plan["mode"] == "pca1bit" and plan["dimension"] not in (512,256,128))
                or "ours_cache_allocation" not in artifact):
            raise ValueError("missing discrete resident representation/cache policy evidence")
    allocated = artifact["ours_record_cache_reserved_bytes"]
    available = artifact["ours_record_cache_budget_bytes"]
    graph = artifact["ours_graph_cache_bytes"]
    if any(type(v) is not int or v < 0 for v in (allocated, available, graph)):
        raise ValueError("invalid cache byte counts")
    if (plan["required_bytes"] + graph + available != plan["budget_bytes"]
            or allocated > available or artifact["cache_bytes"] != graph + allocated):
        raise ValueError("cache ledger differs from remaining RAM budget")
    if artifact["cache_mode"] == "c0":
        if artifact["ours_record_cache_policy"] != "off" or allocated or graph:
            raise ValueError("C0 cannot use hot/dynamic/shared graph caches")
        return
    if artifact["ours_record_cache_policy"] != "validation_hot_then_dynamic_fifo16_v1":
        raise ValueError("standard Ours requires validation hot+dynamic")
    manifest = Path(artifact["ours_hot_profile_manifest"])
    if sha256_file(manifest) != artifact["ours_hot_profile_sha256"]:
        raise ValueError("hot profile manifest changed")
    profile = json.loads(manifest.read_text())
    if (profile["source_phase"] != "validation" or profile["route_plan"] != plan
            or profile["source_graph_sha256"] != artifact["source_graph_sha256"]
            or profile["native_binary_sha256"] != artifact["native_binary_sha256"]
            or profile["ranks_sha256"] != artifact["ours_hot_ranks_sha256"]
            or sha256_file(manifest.parent / "ranks.u32") != profile["ranks_sha256"]):
        raise ValueError("hot profile source/ranks mismatch")
    snapshots = artifact["ours_record_cache_stats"]
    keys = [(s["width"], s["beam"]) for s in snapshots]
    expected = [(s["search_width"], s["beam_width"]) for s in artifact["summary_rows"]]
    if len(set(keys)) != len(keys) or set(keys) != set(expected):
        raise ValueError("missing/duplicate cache statistics per configuration")
    for item in snapshots:
        cache = item["cache"]
        if (cache["budget_bytes"] != available or cache["reserved_bytes"] != allocated
                or cache["static_reserved_bytes"] + cache["dynamic"]["reserved_bytes"] != allocated
                or cache["dynamic"]["resident_nodes"] > cache["dynamic"]["capacity_nodes"]
                or cache["dynamic"]["valid_payload_bytes"] > cache["dynamic"]["reserved_bytes"]):
            raise ValueError("cache statistics exceed allocation/capacity")


def flatten_artifact(path: Path, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in artifact["summary_rows"]:
        row = {field: "" for field in SUMMARY_FIELDS}
        row.update(source)
        for key in (
            "layer", "dataset", "method", "storage_mode", "cache_mode", "phase", "run_id",
            "repeat_id", "workers", "search_dram_budget_gib", "source_suite", "source_kernel",
            "implementation_fingerprint", "native_binary_sha256", "input_manifest_sha256",
            "source_index_manifest_sha256", "query_split_sha256",
            "query_order_sha256", "query_order_seed", "shared_graph_sha256",
            "source_graph_sha256", "graph_role",
            "cache_bytes", "cache_nodes", "index_size_mb", "source_commit",
            "ours_4bit_payload_bytes", "ours_8bit_payload_bytes",
            "ours_adjacency_bytes", "ours_fp32_base_bytes",
            "direct_io", "native_aio", "page_size",
            "formal_ready", *RESOURCE_FIELDS,
            "storage_precondition_protocol", "storage_precondition_path", "storage_precondition_sha256",
            "separate_reference_protocol", "separate_reference_path", "separate_reference_sha256",
            "memory_acceptance_basis",
            "native_search_result_path", "native_search_result_sha256",
        ):
            row[key] = artifact.get(key, row.get(key, ""))
        from diskfair.layout import LAYER_DIRS
        plan = artifact.get("ours_route_plan") or {}
        for target, key in (("ours_route_policy", "policy"), ("ours_route_mode", "mode"),
                            ("ours_route_dimension", "dimension"), ("ours_route_m", "shortlist_m"),
                            ("ours_route_residual_norm_bytes", "residual_norm_bytes"),
                            ("ours_route_required_bytes", "required_bytes")):
            row[target] = plan.get(key, "")
        row["pca_assets_sha256"] = artifact.get("pca_assets_sha256", "")
        for key in ("ours_cache_allocation", "ours_record_cache_policy", "ours_hot_profile_sha256", "ours_record_cache_budget_bytes",
                    "ours_record_cache_reserved_bytes", "ours_graph_cache_bytes"):
            row[key] = artifact.get(key, "")
        for snapshot in artifact.get("ours_record_cache_stats", []):
            if (snapshot["width"], snapshot["beam"]) != (source.get("search_width"), source.get("beam_width")):
                continue
            cache = snapshot["cache"]; dynamic = cache["dynamic"]
            row.update(hot_record_nodes=cache["static_nodes"], hot_record_reserved_bytes=cache["static_reserved_bytes"],
                       hot_record_part_hits=sum(cache["static_hits"]))
            for target, key in (("capacity_nodes", "capacity_nodes"), ("resident_nodes", "resident_nodes"),
                                ("reserved_bytes", "reserved_bytes"), ("valid_payload_bytes", "valid_payload_bytes"),
                                ("insertions", "node_insertions"), ("evictions", "node_evictions")):
                row["dynamic_record_"+target] = dynamic[key]
            row["dynamic_record_part_hits"] = sum(dynamic["hits"])
            row["dynamic_record_part_misses"] = sum(dynamic["misses"])
        row["experiment"] = artifact.get("experiment", LAYER_DIRS[row["layer"]])
        row["system03_protocol"] = artifact.get("system03_protocol", "")
        row["artifact_path"] = str(path)
        rows.append(row)
    return rows


def validate_layer_completeness(
    rows: list[dict[str, Any]],
    *,
    layer: str,
    dataset: str,
    repeats: int,
    workers: tuple[int, ...],
    budget_gib: float = DEFAULT_BUDGET_GIB,
    cache_mode: str = "standard",
    methods: Iterable[str] | None = None,
    storage_modes: Iterable[str] | None = None,
    baseline_budgets: dict[str, float] | None = None,
) -> None:
    try:
        from .protocol import method_budget_gib
    except ImportError:
        from protocol import method_budget_gib
    errors: list[str] = []
    expected_methods = set(methods or LAYER_METHODS[layer])
    actual_methods = {str(r["method"]) for r in rows}
    if actual_methods != expected_methods:
        errors.append(f"methods={sorted(actual_methods)}, expected={sorted(expected_methods)}")
    for row in rows:
        if (str(row.get("layer")) != layer or int(row["workers"]) not in workers
                or float(row["search_dram_budget_gib"]) != method_budget_gib(str(row["method"]), budget_gib, baseline_budgets)
                or row.get("cache_mode") != ("c0" if layer == "05a" else cache_mode)
                or int(row["repeat_id"]) not in range(repeats)
                or (storage_modes is not None and row["storage_mode"] not in storage_modes)):
            errors.append("row does not belong to the declared layer/budget/cache/worker/repeat matrix")
            break
    for spec in specs_for(layer, expected_methods):
        for storage_mode in spec.storage_modes:
            if storage_modes is not None and storage_mode not in storage_modes:
                continue
            for worker in workers:
                for repeat_id in range(repeats):
                    found = [
                        r for r in rows
                        if r["method"] == spec.method
                        and r["storage_mode"] == storage_mode
                        and int(r["workers"]) == worker
                        and int(r["repeat_id"]) == repeat_id
                        and float(r["search_dram_budget_gib"]) == method_budget_gib(spec.method, budget_gib, baseline_budgets)
                        and r.get("cache_mode") == ("c0" if layer == "05a" else cache_mode)
                    ]
                    if not found:
                        errors.append(
                            f"missing {spec.method}/{storage_mode}/workers={worker}/repeat={repeat_id}"
                        )
    if layer == "05b":
        hashes = {
            str(r["shared_graph_sha256"])
            for r in rows
            if r["method"] != "Ours-Disk"
        }
        if (expected_methods - {"Ours-Disk"}) and (len(hashes) != 1 or "" in hashes):
            errors.append(
                "05B PQ/SQ/SAQ must have one shared graph hash, "
                f"got {sorted(hashes)}"
            )
        ours = [r for r in rows if r["method"] == "Ours-Disk"]
        if any(r.get("graph_role") != "ours_native" for r in ours):
            errors.append("05B Ours rows must retain the native Ours graph")
    if any(str(r["dataset"]) != dataset for r in rows):
        errors.append("mixed datasets")
    if any(str(r["phase"]) != "test" for r in rows):
        errors.append("non-test rows leaked into formal aggregate")
    if errors:
        raise ContractError(f"incomplete {layer}/{dataset}:\n  - " + "\n  - ".join(errors))
