#!/usr/bin/env python3
"""Tiny end-to-end check for the official DiskANN 05C native runner."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

PKG = Path(__file__).resolve().parents[1]
if "diskfair" not in sys.modules:
    import types

    module = types.ModuleType("diskfair")
    module.__path__ = [str(PKG)]
    sys.modules["diskfair"] = module

from diskfair.native_contract import METHOD_SPECS, validate_artifact  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fvecs(path: Path, values: np.ndarray) -> None:
    with path.open("wb") as stream:
        for row in np.asarray(values, dtype="<f4"):
            stream.write(struct.pack("<i", row.size))
            stream.write(row.tobytes())


def write_ivecs(path: Path, values: np.ndarray) -> None:
    with path.open("wb") as stream:
        for row in np.asarray(values, dtype="<i4"):
            stream.write(struct.pack("<i", row.size))
            stream.write(row.tobytes())


def command(
    binary: Path,
    root: Path,
    *,
    phase: str,
    result: Path,
    trace: Path,
    native_hash: str,
    order_hash: str,
) -> list[str]:
    return [
        str(binary),
        "--contract-version", "2",
        "--phase", phase,
        "--layer", "05c",
        "--dataset", "tiny",
        "--method", "DiskANN-PQ-Disk",
        "--storage-mode", "hybrid_disk",
        "--data-root", str(root / "data"),
        "--work-root", str(root / "work"),
        "--saq-data-root", str(root),
        "--source-results-root", str(root),
        "--input-manifest", str(root / "input.json"),
        "--input-manifest-sha256", sha256(root / "input.json"),
        "--disk-index-dir", str(root / "nvme" / "index"),
        "--query", str(root / "query.fvecs"),
        "--groundtruth", str(root / "gt.ivecs"),
        "--query-split-sha256", "integration-split",
        "--query-order", str(root / "order.u32"),
        "--query-order-sha256", order_hash,
        "--query-order-seed", "20260813",
        "--result-json", str(result),
        "--query-trace", str(trace),
        "--run-id", "native_integration_diskann",
        "--repeat-id", "0",
        "--workers", "1",
        "--warmup-queries", "2" if phase != "export" else "0",
        "--search-dram-budget-gib", "0.25",
        "--cache-mode", "c0",
        "--seed", "20260813",
        "--page-size", "4096",
        "--max-inflight-io", "128",
        "--direct-io", "required",
        "--native-aio", "required",
        "--implementation-fingerprint", "official-diskann-v0.55.0-io-uring",
        "--native-binary-sha256", native_hash,
        "--git-commit", "integration",
        "--candidate-row-offset", "0",
        "--integration-widths", "10,20",
        "--integration-beams", "2",
    ]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_diskann_native_integration.py /path/to/qgraph05_diskann_port")
        return 2
    binary = Path(sys.argv[1]).resolve()
    native_hash = sha256(binary)
    root = Path(tempfile.mkdtemp(prefix="qgraph05_diskann_native_"))
    (root / "data" / "tiny").mkdir(parents=True)
    (root / "nvme").mkdir()
    rng = np.random.default_rng(20260813)
    base = rng.normal(size=(256, 16)).astype(np.float32)
    query = rng.normal(size=(16, 16)).astype(np.float32)
    distances = ((query[:, None, :] - base[None, :, :]) ** 2).sum(axis=2)
    truth = np.argsort(distances, axis=1)[:, :10].astype(np.int32)
    write_fvecs(root / "data" / "tiny" / "tiny_base.fvecs", base)
    write_fvecs(root / "query.fvecs", query)
    write_ivecs(root / "gt.ivecs", truth)
    (root / "input.json").write_text(json.dumps({"dataset": "tiny"}) + "\n")
    with (root / "order.u32").open("wb") as stream:
        for query_id in range(query.shape[0]):
            stream.write(struct.pack("<I", query_id))
    order_hash = sha256(root / "order.u32")

    export_result = root / "export.json"
    subprocess.run(
        command(
            binary,
            root,
            phase="export",
            result=export_result,
            trace=root / "unused.jsonl",
            native_hash=native_hash,
            order_hash=order_hash,
        ),
        check=True,
    )
    result = root / "validation.json"
    trace = root / "validation.queries.jsonl"
    subprocess.run(
        command(
            binary,
            root,
            phase="validation",
            result=result,
            trace=trace,
            native_hash=native_hash,
            order_hash=order_hash,
        ),
        check=True,
    )
    spec = next(item for item in METHOD_SPECS if item.method == "DiskANN-PQ-Disk")
    artifact = validate_artifact(
        result,
        spec=spec,
        expected={
            "dataset": "tiny",
            "phase": "validation",
            "run_id": "native_integration_diskann",
            "repeat_id": 0,
            "workers": 1,
            "storage_mode": "hybrid_disk",
            "cache_mode": "c0",
            "implementation_fingerprint": "official-diskann-v0.55.0-io-uring",
            "native_binary_sha256": native_hash,
            "query_split_sha256": "integration-split",
            "input_manifest_sha256": sha256(root / "input.json"),
            "query_order_sha256": order_hash,
            "query_order_seed": 20260813,
            "warmup_queries": 2,
            "search_dram_budget_gib": 0.25,
        },
        disk_root=root / "nvme",
    )
    assert artifact["io_backend"] == "linux_io_uring_odirect"
    assert len(artifact["summary_rows"]) == 2
    print(
        "PASS official DiskANN build/search/parity/contract",
        f"root={root}",
        f"recalls={[round(row['recall'], 4) for row in artifact['summary_rows']]}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
