#!/usr/bin/env python3
"""Verify accidental memory-only 05C bindings are blocked."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
sys.path.insert(0, str(SUITE))

from native_contract import ContractError, MethodSpec, validate_artifact  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_fvecs(path: Path, rows: list[list[float]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(struct.pack("<i", len(row)))
            stream.write(struct.pack(f"<{len(row)}f", *row))


def write_ivecs(path: Path, rows: list[list[int]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(struct.pack("<i", len(row)))
            stream.write(struct.pack(f"<{len(row)}i", *row))


def main() -> int:
    repo = SUITE.parents[1]
    port = repo / "experiments/05_disk_system_fair/blocked_05c_memory_system_port.py"
    with tempfile.TemporaryDirectory(prefix="qgraph05_blocked_05c_") as tmp:
        root = Path(tmp)
        data_root = root / "data"
        dataset_root = data_root / "tiny"
        dataset_root.mkdir(parents=True)
        base = [[0.0, 1.0], [1.0, 0.0], [2.0, 2.0], [3.0, 3.0]]
        query = [[0.1, 0.9], [2.1, 2.0]]
        write_fvecs(dataset_root / "tiny_base.fvecs", base)
        query_path = root / "query.fvecs"
        gt_path = root / "gt.ivecs"
        order_path = root / "order.u32"
        write_fvecs(query_path, query)
        write_ivecs(gt_path, [[0, 1, 2, 3], [2, 3, 1, 0]])
        order_path.write_bytes(b"".join(struct.pack("<I", value) for value in (0, 1)))
        input_manifest = root / "input.json"
        input_manifest.write_text("{}\n")
        result = root / "result.json"
        trace = root / "trace.jsonl"
        command = [
            sys.executable,
            str(port),
            "--contract-version", "2",
            "--phase", "validation",
            "--layer", "05c",
            "--dataset", "tiny",
            "--method", "Legacy-MemoryOnly-DiskPort",
            "--storage-mode", "hybrid_disk",
            "--data-root", str(data_root),
            "--disk-index-dir", str(root / "nvme/index"),
            "--query", str(query_path),
            "--groundtruth", str(gt_path),
            "--query-split-sha256", "split",
            "--query-order", str(order_path),
            "--query-order-sha256", sha256(order_path),
            "--query-order-seed", "20260813",
            "--result-json", str(result),
            "--query-trace", str(trace),
            "--run-id", "blocked_05c_test",
            "--repeat-id", "0",
            "--workers", "1",
            "--warmup-queries", "0",
            "--search-dram-budget-gib", "2.0",
            "--cache-mode", "standard",
            "--seed", "20260813",
            "--page-size", "4096",
            "--max-inflight-io", "128",
            "--direct-io", "required",
            "--native-aio", "required",
            "--implementation-fingerprint", "blocked-memory-only-binding",
            "--native-binary-sha256", sha256(port),
            "--git-commit", "integration",
            "--input-manifest", str(input_manifest),
            "--input-manifest-sha256", sha256(input_manifest),
        ]
        proc = subprocess.run(command, text=True, capture_output=True)
        assert proc.returncode == 3, proc.stdout + proc.stderr
        artifact = json.loads(result.read_text())
        assert artifact["status"] == "blocked"
        assert artifact["formal_ready"] is False
        assert artifact["whole_graph_in_memory"] is True
        try:
            validate_artifact(
                result,
                spec=MethodSpec(
                    "05c",
                    "Legacy-MemoryOnly-DiskPort",
                    "legacy",
                    "memory-only reference binding",
                    ("hybrid_disk",),
                    "blocked_memory_only_binding",
                ),
                expected={
                    "dataset": "tiny",
                    "phase": "validation",
                    "run_id": "blocked_05c_test",
                    "repeat_id": 0,
                    "workers": 1,
                    "storage_mode": "hybrid_disk",
                    "query_split_sha256": "split",
                    "query_order_sha256": sha256(order_path),
                    "query_order_seed": 20260813,
                    "warmup_queries": 0,
                },
                disk_root=root / "nvme",
            )
        except ContractError as exc:
            message = str(exc)
            assert "formal_ready must be true" in message
            assert "whole_graph_in_memory must be false" in message
        else:
            raise AssertionError("blocked memory-only 05C port passed the formal contract")
    print("PASS blocked 05C memory-only systems are rejected by the formal contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
