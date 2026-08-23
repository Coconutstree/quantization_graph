"""Standalone tests for the formal native-port result contract.

Runs standalone (no pytest dependency):

    python experiments/05_disk_system_fair/tests/test_native_contract.py
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
    sha256_file,
    validate_artifact,
    validate_registry,
)


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


def main() -> int:
    failed = 0
    cases = [
        (test_valid_resident_artifact, True),
        (test_memory_row_cannot_claim_ssd, True),
        (test_pending_ports_fail, False),
        (test_plan_parity_thresholds_are_enforced, False),
        (test_official_diskann_requires_io_uring, True),
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
        (test_pending_ports_fail, ()),
        (test_plan_parity_thresholds_are_enforced, ()),
        (test_official_diskann_requires_io_uring, (tmp,)),
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
