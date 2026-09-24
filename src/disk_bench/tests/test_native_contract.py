"""Standalone tests for the formal native-port result contract.

Runs standalone (no pytest dependency):

    python src/disk_bench/tests/test_native_contract.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
if "diskfair" not in sys.modules:
    import types

    _mod = types.ModuleType("diskfair")
    _mod.__path__ = [str(_PKG)]
    sys.modules["diskfair"] = _mod

from diskfair.native_contract import (  # noqa: E402
    ContractError,
    METHOD_SPECS,
    SUMMARY_FIELDS,
    flatten_artifact,
    sha256_file,
    validate_artifact,
    validate_registry,
)
from diskfair.orchestrator import _run_capture_build  # noqa: E402


def _artifact(tmp: Path) -> tuple[Path, Path]:
    disk_root = tmp / "nvme"
    index_path = disk_root / "05" / "pq"
    index_path.mkdir(parents=True, exist_ok=True)
    trace = tmp / "trace.jsonl"
    trace_row = {
        "layer": "05a", "storage_mode": "resident", "cache_mode": "c0", "dataset": "gist",
        "method": "PQ_4bit", "config_id": "formal", "repeat_id": 0,
        "query_id": 0, "search_width": 100, "beam_width": 1, "workers": 1,
        "search_dram_budget_gib": 2, "cache_nodes": 0, "resident_bytes": 100,
        "cache_bytes": 0, "peak_rss_bytes": 1000, "recall_at_10": 0.9,
        "fixed_candidate_recall_at_10": 0.9, "mean_relative_error": 0.1,
        "p95_relative_error": 0.2, "pairwise_flip_rate": 0.01,
        "latency_us": 100, "query_prep_us": 1, "queue_compute_us": 1,
        "io_wait_us": 0, "distance_compute_us": 90, "rerank_us": 0,
        "visited_nodes": 0, "distance_evaluations": 100, "io_requests": 0,
        "sectors_4k": 0, "bytes_read": 0, "average_read_bytes": 0,
        "coalesced_requests": 0, "duplicate_pages_removed": 0,
        "shared_cache_hits": 0, "shared_cache_misses": 0,
        "query_cache_hits": 0, "query_cache_misses": 0,
    }
    trace.write_text(json.dumps(trace_row) + "\n")
    doc = {
        "schema_version": 2,
        "status": "done",
        "layer": "05a",
        "dataset": "gist",
        "method": "PQ_4bit",
        "source_suite": "01_quantizer_fair",
        "source_kernel": "faiss::ProductQuantizer",
        "port_kind": "algorithm_preserving_disk_port",
        "storage_mode": "resident",
        "cache_mode": "c0",
        "phase": "test",
        "run_id": "contract_test",
        "repeat_id": 0,
        "workers": 1,
        "warmup_queries": 100,
        "query_split_sha256": "split",
        "index_path": str(index_path.resolve()),
        "formal_ready": True,
        "memory_accounting_complete": True,
        "measurement_scope": "test_only_excludes_warmup",
        "storage_cache_protocol": "controlled_warm",
        "page_size": 4096,
        "whole_graph_in_memory": False,
        "whole_payload_in_memory": True,
        "direct_io": False,
        "native_aio": False,
        "io_backend": "resident",
        "implementation_fingerprint": "test-build",
        "native_binary_sha256": "0" * 64,
        "input_manifest_sha256": "input-manifest",
        "source_index_manifest_sha256": "source-index-manifest",
        "git_commit": "deadbeef",
        "compiler": "test",
        "simd": "test",
        "base_count": 1000,
        "dimension": 960,
        "search_dram_budget_gib": 2,
        "resident_bytes": 100,
        "codebook_bytes": 10,
        "worker_scratch_bytes": 10,
        "cache_bytes": 0,
        "cache_nodes": 0,
        "peak_rss_bytes": 1000,
        "cpu_affinity": "0",
        "numa_node": 0,
        "implementation_parity": "passed",
        "parity": {
            "reference_artifact_sha256": "reference",
            "query_comparisons": 1,
            "max_recall_delta": 0.0,
            "max_distance_delta": 0.0,
        },
        "query_trace_path": str(trace.resolve()),
        "query_trace_sha256": sha256_file(trace),
        "summary_rows": [
            {
                "config_id": "formal",
                "search_param": "rerank=100",
                "search_width": 100,
                "beam_width": 1,
                "recall": 0.9,
                "fixed_candidate_recall_at_10": 0.9,
                "code_bytes_per_vector": 480,
                "effective_bits_per_dim": 4.0,
                "read_amplification": 0.0,
                "qps": 1000,
                "latency_mean_us": 1000,
                "latency_p50_us": 900,
                "latency_p95_us": 1100,
                "latency_p99_us": 1200,
                "query_count": 1,
                "index_size_mb": 10,
                "resident_bytes": 100,
                "peak_rss_bytes": 1000,
                "io_requests_per_query": 0,
                "sectors_4k_per_query": 0,
                "bytes_read_per_query": 0,
            }
        ],
    }
    path = tmp / "artifact.json"
    path.write_text(json.dumps(doc))
    return path, disk_root


def test_valid_resident_artifact(tmp: Path) -> None:
    path, disk_root = _artifact(tmp)
    artifact = validate_artifact(
        path,
        spec=METHOD_SPECS[0],
        expected={
            "dataset": "gist",
            "phase": "test",
            "run_id": "contract_test",
            "repeat_id": 0,
            "workers": 1,
            "storage_mode": "resident",
            "query_split_sha256": "split",
            "warmup_queries": 100,
        },
        disk_root=disk_root,
    )
    assert artifact["method"] == "PQ_4bit"


def test_incomplete_memory_accounting_is_rejected(tmp: Path) -> None:
    path, disk_root = _artifact(tmp)
    doc = json.loads(path.read_text())
    doc["memory_accounting_complete"] = False
    path.write_text(json.dumps(doc))
    try:
        validate_artifact(
            path, spec=METHOD_SPECS[0], disk_root=disk_root,
            expected={
                "dataset": "gist", "phase": "test", "run_id": "contract_test",
                "repeat_id": 0, "workers": 1, "storage_mode": "resident",
                "query_split_sha256": "split", "warmup_queries": 100,
            },
        )
    except ContractError as exc:
        assert "search memory accounting is incomplete" in str(exc)
    else:
        raise AssertionError("incomplete memory accounting accepted")


def test_memory_row_cannot_claim_ssd(tmp: Path) -> None:
    path, disk_root = _artifact(tmp)
    doc = json.loads(path.read_text())
    doc["storage_mode"] = "payload_on_ssd"
    doc["whole_payload_in_memory"] = True
    path.write_text(json.dumps(doc))
    try:
        validate_artifact(
            path,
            spec=METHOD_SPECS[0],
            expected={
                "dataset": "gist", "phase": "test", "run_id": "contract_test",
                "repeat_id": 0, "workers": 1, "storage_mode": "payload_on_ssd",
                "query_split_sha256": "split", "warmup_queries": 100,
            },
            disk_root=disk_root,
        )
    except ContractError:
        return
    raise AssertionError("memory payload was accepted as an SSD result")


def test_05b_baselines_accept_disk_payload(tmp: Path) -> None:
    """The shared-graph baselines have both hybrid and disk-payload contracts."""
    for method in ("PQ-DiskANN-Disk", "SQ-DiskANN-Disk", "SAQ-DiskANN-Disk"):
        spec = next(item for item in METHOD_SPECS if item.method == method)
        assert spec.storage_modes == ("hybrid_disk", "disk_payload")

        path, disk_root = _artifact(tmp / method.replace("-", "_"))
        doc = json.loads(path.read_text())
        doc.update(
            {
                "layer": spec.layer,
                "method": spec.method,
                "source_suite": spec.source_suite,
                "source_kernel": spec.source_kernel,
                "storage_mode": "disk_payload",
                "cache_mode": "standard",
                "graph_role": "shared_baseline",
                "shared_graph_sha256": "shared-graph",
                "source_graph_sha256": "shared-graph",
                "whole_payload_in_memory": False,
                "direct_io": True,
                "native_aio": True,
                "io_backend": "linux_native_aio_odirect",
                "implementation_parity": "passed",
                "parity": {
                    "reference_artifact_sha256": "reference",
                    "max_recall_delta": 0.0,
                    "query_comparisons": 1,
                    "mean_top10_overlap": 1.0,
                    "mean_visited_count_relative_delta": 0.0,
                    "mean_distance_count_relative_delta": 0.0,
                },
            }
        )
        trace = Path(doc["query_trace_path"])
        trace_row = json.loads(trace.read_text())
        trace_row.update(
            {
                "layer": spec.layer,
                "method": spec.method,
                "storage_mode": "disk_payload",
                "cache_mode": "standard",
                "io_requests": 1,
                "sectors_4k": 1,
                "bytes_read": 4096,
                "io_wait_us": 1,
            }
        )
        trace.write_text(json.dumps(trace_row) + "\n")
        doc["query_trace_sha256"] = sha256_file(trace)
        doc["summary_rows"][0]["io_requests_per_query"] = 1
        doc["summary_rows"][0]["sectors_4k_per_query"] = 1
        doc["summary_rows"][0]["bytes_read_per_query"] = 4096
        path.write_text(json.dumps(doc))
        validate_artifact(
            path,
            spec=spec,
            expected={
                "dataset": "gist",
                "phase": "test",
                "run_id": "contract_test",
                "repeat_id": 0,
                "workers": 1,
                "storage_mode": "disk_payload",
                "query_split_sha256": "split",
                "warmup_queries": 100,
            },
            disk_root=disk_root,
        )


def test_pending_ports_fail() -> None:
    spec = METHOD_SPECS[0]
    ports = {
        spec.key: {
            "status": "pending",
            "command": ["/missing"],
            "source_suite": spec.source_suite,
            "source_kernel": spec.source_kernel,
            "port_kind": spec.port_kind,
        }
    }
    try:
        validate_registry(ports, ("05a",))
    except ContractError:
        return
    raise AssertionError("pending/incomplete ports were accepted")


def test_plan_parity_thresholds_are_enforced() -> None:
    """Guard the thresholds specified by plan section 6.3."""
    source = Path(__file__).resolve().parents[1] / "native_contract.py"
    contract = source.read_text()
    assert "recall_delta > 1e-3" in contract
    assert 'parity.get("mean_top10_overlap", 0.0)' in contract
    assert 'parity.get("mean_visited_count_relative_delta", 1.0)' in contract
    assert 'parity.get("mean_distance_count_relative_delta", 1.0)' in contract


def test_official_diskann_requires_io_uring(tmp: Path) -> None:
    """The official DiskANN port must not masquerade as the common libaio port."""
    path, disk_root = _artifact(tmp)
    doc = json.loads(path.read_text())
    spec = next(item for item in METHOD_SPECS if item.method == "DiskANN-PQ-Disk")
    doc.update(
        {
            "layer": spec.layer,
            "method": spec.method,
            "source_suite": spec.source_suite,
            "source_kernel": spec.source_kernel,
            "port_kind": spec.port_kind,
            "storage_mode": "hybrid_disk",
            "cache_mode": "standard",
            "whole_payload_in_memory": False,
            "direct_io": True,
            "native_aio": True,
            "io_backend": "linux_native_aio_odirect",
            "implementation_parity": "passed",
            "parity": {
                "reference_artifact_sha256": "reference",
                "max_recall_delta": 0.0,
                "query_comparisons": 1,
                "mean_top10_overlap": 1.0,
                "mean_visited_count_relative_delta": 0.0,
                "mean_distance_count_relative_delta": 0.0,
            },
        }
    )
    trace = Path(doc["query_trace_path"])
    trace_row = json.loads(trace.read_text())
    trace_row.update(
        {
            "layer": spec.layer,
            "method": spec.method,
            "storage_mode": "hybrid_disk",
            "cache_mode": "standard",
        }
    )
    trace.write_text(json.dumps(trace_row) + "\n")
    doc["query_trace_sha256"] = sha256_file(trace)
    doc["summary_rows"][0]["bytes_read_per_query"] = 4096
    path.write_text(json.dumps(doc))
    expected = {
        "dataset": "gist",
        "phase": "test",
        "run_id": "contract_test",
        "repeat_id": 0,
        "workers": 1,
        "storage_mode": "hybrid_disk",
        "query_split_sha256": "split",
        "warmup_queries": 100,
    }
    try:
        validate_artifact(path, spec=spec, expected=expected, disk_root=disk_root)
    except ContractError as exc:
        assert "linux_io_uring_odirect" in str(exc)
    else:
        raise AssertionError("official DiskANN artifact accepted the libaio backend")

    doc["io_backend"] = "linux_io_uring_odirect"
    path.write_text(json.dumps(doc))
    validate_artifact(path, spec=spec, expected=expected, disk_root=disk_root)
    del doc["memory_accounting_complete"]
    path.write_text(json.dumps(doc))
    try:
        validate_artifact(path, spec=spec, expected=expected, disk_root=disk_root)
    except ContractError as exc:
        assert "verified complete memory accounting" in str(exc)
    else:
        raise AssertionError("missing memory accounting accepted")


def test_ours_index_size_fields_flow_into_rows(tmp: Path) -> None:
    """Ours 4bit/8bit index-size fields survive flattening into the CSV rows."""
    path, disk_root = _artifact(tmp)
    doc = json.loads(path.read_text())
    doc.update(
        {
            "layer": "05b",
            "method": "Ours-Disk",
            "source_suite": "02_diskann_fair",
            "source_kernel": "ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search",
            "port_kind": "algorithm_preserving_disk_port",
            "storage_mode": "hybrid_disk",
            "cache_mode": "standard",
            "ablation": "db1+coalescing+reuse",
            "graph_role": "ours_native",
            "source_graph_sha256": "source-graph",
            "shared_graph_sha256": "",
            "ablations": [
                "full4-resident/no-gate",
                "db1-resident/full4-on-ssd",
                "db1+coalescing",
                "db1+coalescing+reuse",
            ],
            "parity": {
                "reference_artifact_sha256": "reference",
                "max_recall_delta": 0.0,
                "query_comparisons": 1,
                "mean_top10_overlap": 1.0,
                "mean_visited_count_relative_delta": 0.0,
                "mean_distance_count_relative_delta": 0.0,
            },
            "whole_payload_in_memory": False,
            "direct_io": True,
            "native_aio": True,
            "io_backend": "linux_native_aio_odirect",
            "ours_4bit_payload_bytes": 480_000_000,
            "ours_8bit_payload_bytes": 960_000_000,
            "ours_adjacency_bytes": 256_000_000,
            "ours_fp32_base_bytes": 0,
        }
    )
    trace = Path(doc["query_trace_path"])
    trace_row = json.loads(trace.read_text())
    trace_row.update(
        {
            "layer": "05b",
            "method": "Ours-Disk",
            "storage_mode": "hybrid_disk",
            "cache_mode": "standard",
            "db1_checks": 10,
            "db1_survivors": 5,
            "full4_candidates": 20,
            "full4_page_reads": 20,
            "rerank_candidates": 3,
            "rerank_page_reads": 3,
        }
    )
    trace.write_text(json.dumps(trace_row) + "\n")
    doc["query_trace_sha256"] = sha256_file(trace)
    doc["summary_rows"][0]["bytes_read_per_query"] = 4096
    path.write_text(json.dumps(doc))
    artifact = validate_artifact(
        path,
        spec=next(item for item in METHOD_SPECS if item.method == "Ours-Disk"),
        expected={
            "dataset": "gist",
            "phase": "test",
            "run_id": "contract_test",
            "repeat_id": 0,
            "workers": 1,
            "storage_mode": "hybrid_disk",
            "query_split_sha256": "split",
            "warmup_queries": 100,
        },
        disk_root=disk_root,
    )
    rows = flatten_artifact(path, artifact)
    assert rows
    assert str(rows[0]["ours_4bit_payload_bytes"]) == "480000000"
    assert str(rows[0]["ours_8bit_payload_bytes"]) == "960000000"
    assert str(rows[0]["ours_adjacency_bytes"]) == "256000000"
    assert str(rows[0]["ours_fp32_base_bytes"]) == "0"
    artifact["ours_route_plan"] = dict(policy="resident_full_else_pca1bit_m32_no_residual_v1",
        mode="pca1bit", dimension=128, shortlist_m=32, residual_norm_bytes=0, required_bytes=530733696)
    artifact["pca_assets_sha256"] = "a" * 64
    pca_row = flatten_artifact(path, artifact)[0]
    assert pca_row["ours_route_dimension"] == 128 and pca_row["ours_route_m"] == 32
    assert pca_row["ours_route_residual_norm_bytes"] == 0
    assert pca_row["pca_assets_sha256"] == "a" * 64
    for field in (
        "ours_4bit_payload_bytes",
        "ours_8bit_payload_bytes",
        "ours_adjacency_bytes",
        "ours_fp32_base_bytes",
    ):
        assert field in SUMMARY_FIELDS


def test_capture_build_writes_sidecar(tmp: Path) -> None:
    """QG05_CAPTURE_BUILD_STATS export wrapper writes a valid sidecar."""
    stats_path = tmp / "artifact.build_stats.json"
    log_path = tmp / "artifact.terminal.log"
    with log_path.open("w") as log:
        code = "import time; time.sleep(0.2)"
        returncode = _run_capture_build(
            [sys.executable, "-c", code], log, stats_path
        )
    assert returncode == 0
    record = json.loads(stats_path.read_text())
    assert record["wall_seconds"] > 0
    assert record["peak_rss_bytes"] > 0
    assert record["read_bytes"] >= 0


def test_validate_alias_does_not_skip_trace_checks(tmp: Path) -> None:
    path, disk_root = _artifact(tmp)
    doc = json.loads(path.read_text())
    doc["phase"] = "validate"
    doc["query_trace_sha256"] = "0" * 64
    path.write_text(json.dumps(doc))
    try:
        validate_artifact(path, spec=METHOD_SPECS[0], expected={"phase": "validate"}, disk_root=disk_root)
    except ContractError as exc:
        assert "query trace SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("validate alias bypassed trace verification")


def test_single_run_results_preserve_values_and_reject_duplicates() -> None:
    from diskfair.native_contract import FORMAL_REPEATS, single_test_rows
    assert FORMAL_REPEATS == 1
    source = {"method": "Ours-Disk", "repeat_id": 0, "search_width": 64,
              "recall": 0.95, "qps": 123.45, "latency_p95_us": 456.78}
    assert single_test_rows([source]) == [source]
    assert "qps_iqr" not in single_test_rows([source])[0]
    for invalid in ([source, source], [dict(source, repeat_id=1)],
                    [dict(source, repeat_id="median_of_5")]):
        try:
            single_test_rows(invalid)
        except ContractError:
            pass
        else:
            raise AssertionError("invalid single-run result accepted")


def main() -> int:
    failed = 0
    cases = [
        (test_valid_resident_artifact, True),
        (test_memory_row_cannot_claim_ssd, True),
        (test_05b_baselines_accept_disk_payload, True),
        (test_pending_ports_fail, False),
        (test_plan_parity_thresholds_are_enforced, False),
        (test_official_diskann_requires_io_uring, True),
        (test_ours_index_size_fields_flow_into_rows, True),
        (test_capture_build_writes_sidecar, True),
        (test_incomplete_memory_accounting_is_rejected, True),
        (test_single_run_results_preserve_values_and_reject_duplicates, False),
        (test_validate_alias_does_not_skip_trace_checks, True),
    ]
    for fn, needs_tmp in cases:
        try:
            if needs_tmp:
                with tempfile.TemporaryDirectory(prefix="diskfair_contract_test_") as tmp:
                    fn(Path(tmp))
            else:
                fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            import traceback

            print(f"FAIL {fn.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qgraph05_contract_"))
    cases = (
        (test_valid_resident_artifact, (tmp,)),
        (test_memory_row_cannot_claim_ssd, (tmp,)),
        (test_05b_baselines_accept_disk_payload, (tmp,)),
        (test_pending_ports_fail, ()),
        (test_plan_parity_thresholds_are_enforced, ()),
        (test_official_diskann_requires_io_uring, (tmp,)),
        (test_ours_index_size_fields_flow_into_rows, (tmp,)),
        (test_capture_build_writes_sidecar, (tmp,)),
        (test_incomplete_memory_accounting_is_rejected, (tmp,)),
    )
    failed = 0
    for fn, args in cases:
        try:
            fn(*args)
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"{len(cases) - failed}/{len(cases)} passed")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
