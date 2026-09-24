"""End-to-end parity test for the native 05A PQ/SQ/Ours port.

This creates a disposable fvecs fixture, exports the exact native codecs, and
requires resident and O_DIRECT/native-AIO distance outputs to match through the
formal artifact validator. It is a contract/integration test, never paper data.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

_PKG = Path(__file__).resolve().parents[1]
if "diskfair" not in sys.modules:
    import types

    _mod = types.ModuleType("diskfair")
    _mod.__path__ = [str(_PKG)]
    sys.modules["diskfair"] = _mod

from diskfair.native_contract import METHOD_SPECS, sha256_file, validate_artifact  # noqa: E402


def _write_fvecs(path: Path, values: np.ndarray) -> None:
    values = np.asarray(values, dtype="<f4")
    with path.open("wb") as stream:
        for row in values:
            stream.write(struct.pack("<i", values.shape[1]))
            stream.write(row.tobytes())


def _write_ivecs(path: Path, values: np.ndarray) -> None:
    values = np.asarray(values, dtype="<i4")
    with path.open("wb") as stream:
        for row in values:
            stream.write(struct.pack("<i", values.shape[1]))
            stream.write(row.tobytes())


def _run_one(binary: Path, root: Path, method: str) -> None:
    dataset = "tiny"
    data_root = root / "data"
    dataset_root = data_root / dataset
    work_root = root / "work"
    candidate_root = work_root / "01_quantizer_fair" / dataset
    disk_root = root / "device"
    result_root = root / "results" / method
    for path in (dataset_root, candidate_root, disk_root, result_root):
        path.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(20260813)
    base = rng.standard_normal((2048, 64), dtype=np.float32)
    queries = rng.standard_normal((4, 64), dtype=np.float32)
    _write_fvecs(dataset_root / "tiny_base.fvecs", base)
    _write_fvecs(dataset_root / "tiny_query.fvecs", queries)
    _write_ivecs(dataset_root / "tiny_groundtruth.ivecs", np.zeros((4, 10), dtype=np.int32))

    candidates = np.empty((4, 1000), dtype="<i4")
    for query_id in range(4):
        candidates[query_id] = (np.arange(1000) + query_id * 137) % base.shape[0]
    (candidate_root / "fixed_candidates_k1000.bin").write_bytes(candidates.tobytes())
    order_path = root / "query_order.u32"
    order_path.write_bytes(np.asarray([2, 0, 3, 1], dtype="<u4").tobytes())
    input_manifest = root / "input.json"
    input_manifest.write_text(json.dumps({"fixture": True}) + "\n")

    binary_hash = sha256_file(binary)
    order_hash = sha256_file(order_path)
    input_hash = sha256_file(input_manifest)
    query_split_hash = hashlib.sha256(b"tiny-query-split").hexdigest()
    spec = next(spec for spec in METHOD_SPECS if spec.layer == "05a" and spec.method == method)
    index_dir = disk_root / "05_disk_system_fair" / "05A_disk_quantizer_io" / dataset / method

    artifacts: dict[str, Path] = {}
    for storage_mode in ("resident", "payload_on_ssd"):
        result_path = result_root / f"{storage_mode}.json"
        trace_path = result_root / f"{storage_mode}.queries.jsonl"
        command = [
            str(binary),
            "--contract-version", "2",
            "--phase", "validation",
            "--layer", "05a",
            "--dataset", dataset,
            "--method", method,
            "--storage-mode", storage_mode,
            "--data-root", str(data_root),
            "--work-root", str(work_root),
            "--source-results-root", str(root / "source_results"),
            "--input-manifest", str(input_manifest),
            "--input-manifest-sha256", input_hash,
            "--disk-index-dir", str(index_dir),
            "--query", str(dataset_root / "tiny_query.fvecs"),
            "--groundtruth", str(dataset_root / "tiny_groundtruth.ivecs"),
            "--query-split-sha256", query_split_hash,
            "--query-order", str(order_path),
            "--query-order-sha256", order_hash,
            "--query-order-seed", "20260813",
            "--result-json", str(result_path),
            "--query-trace", str(trace_path),
            "--run-id", "native_integration_test",
            "--repeat-id", "0",
            "--workers", "2",
            "--warmup-queries", "2",
            "--search-dram-budget-gib", "2",
            "--cache-mode", "c0",
            "--seed", "20260813",
            "--page-size", "4096",
            "--max-inflight-io", "128",
            "--direct-io", "required",
            "--native-aio", "required",
            "--implementation-fingerprint", "05a-native-integration-test",
            "--native-binary-sha256", binary_hash,
            "--git-commit", "integration-test",
            "--candidate-row-offset", "0",
        ]
        if storage_mode == "resident":
            export_command = command.copy()
            export_command[export_command.index("--phase") + 1] = "export"
            export_command[export_command.index("--result-json") + 1] = str(
                result_root / "export.json"
            )
            export_command[export_command.index("--query-trace") + 1] = str(
                result_root / "export.queries.jsonl"
            )
            subprocess.run(export_command, check=True)
        subprocess.run(command, check=True)
        validate_artifact(
            result_path,
            spec=spec,
            expected={
                "dataset": dataset,
                "phase": "validation",
                "run_id": "native_integration_test",
                "repeat_id": 0,
                "workers": 2,
                "storage_mode": storage_mode,
                "cache_mode": "c0",
                "implementation_fingerprint": "05a-native-integration-test",
                "native_binary_sha256": binary_hash,
                "query_split_sha256": query_split_hash,
                "input_manifest_sha256": input_hash,
                "query_order_sha256": order_hash,
                "query_order_seed": 20260813,
                "warmup_queries": 2,
                "search_dram_budget_gib": 2.0,
            },
            disk_root=disk_root,
        )
        artifacts[storage_mode] = result_path

    resident = json.loads(artifacts["resident"].read_text())
    direct = json.loads(artifacts["payload_on_ssd"].read_text())
    assert direct["parity"]["max_distance_delta"] <= 1e-5
    for artifact in (resident, direct):
        assert artifact["parity"]["query_comparisons"] == sum(
            row["query_count"] for row in artifact["summary_rows"]
        )
        assert artifact["parity"]["query_comparisons"] > 0
    assert [row["recall"] for row in resident["summary_rows"]] == [
        row["recall"] for row in direct["summary_rows"]
    ]
    # Old parity files without measured counts must not authorize a test run.
    status = index_dir / "parity.passed"
    original = status.read_bytes()
    test_command = list(command)
    test_command[test_command.index("--phase") + 1] = "test"
    try:
        status.write_bytes(b"\n".join(original.splitlines()[:3]) + b"\n")
        rejected = subprocess.run(test_command, capture_output=True, text=True)
        assert rejected.returncode != 0
        assert "validated parity status is missing" in rejected.stderr
    finally:
        status.write_bytes(original)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_quantizer_native_port.py /path/to/qgraph05_quantizer_port")
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="qgraph05_native_port_") as tmp:
        root = Path(tmp)
        for method in ("PQ_4bit", "SQ_4bit", "Ours_RaBitQ_K1"):
            _run_one(binary, root / method, method)
            print(f"PASS native resident/O_DIRECT parity: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
