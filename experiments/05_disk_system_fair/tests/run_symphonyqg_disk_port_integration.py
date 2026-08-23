#!/usr/bin/env python3
"""Small integration test for the 05C SymphonyQG disk port."""

from __future__ import annotations

import hashlib
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
sys.path.insert(0, str(SUITE))

from native_contract import SPECS_BY_KEY, validate_artifact  # noqa: E402


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
    binary = repo / "build/formal_local/05_disk_system_fair/qgraph05_symphonyqg_disk_port"
    if not binary.exists():
        raise SystemExit(f"build qgraph05_symphonyqg_disk_port first: {binary}")
    with tempfile.TemporaryDirectory(prefix="qgraph05_symphonyqg_disk_") as tmp:
        root = Path(tmp)
        dataset_root = root / "data/tiny"
        dataset_root.mkdir(parents=True)
        rng = random.Random(20260813)
        count, dim, query_count = 256, 64, 4
        base = [[rng.uniform(-1.0, 1.0) for _ in range(dim)] for _ in range(count)]
        queries = [
            [base[i][j] + rng.uniform(-0.01, 0.01) for j in range(dim)]
            for i in range(query_count)
        ]
        truth = []
        for query in queries:
            truth.append(
                sorted(
                    range(count),
                    key=lambda i: sum((base[i][j] - query[j]) ** 2 for j in range(dim)),
                )[:10]
            )
        base_path = dataset_root / "tiny_base.fvecs"
        query_path = root / "query.fvecs"
        gt_path = root / "gt.ivecs"
        order_path = root / "order.u32"
        input_manifest = root / "input.json"
        result = root / "validation.json"
        trace = root / "validation.queries.jsonl"
        write_fvecs(base_path, base)
        write_fvecs(query_path, queries)
        write_ivecs(gt_path, truth)
        order_path.write_bytes(b"".join(struct.pack("<I", i) for i in range(query_count)))
        input_manifest.write_text("{}\n")
        command = [
            str(binary),
            "--contract-version", "2",
            "--phase", "validation",
            "--layer", "05c",
            "--dataset", "tiny",
            "--method", "SymphonyQG-DiskPort",
            "--storage-mode", "hybrid_disk",
            "--data-root", str(root / "data"),
            "--disk-index-dir", str(root / "nvme/index"),
            "--query", str(query_path),
            "--groundtruth", str(gt_path),
            "--query-split-sha256", "integration-split",
            "--query-order", str(order_path),
            "--query-order-sha256", sha256(order_path),
            "--query-order-seed", "20260813",
            "--result-json", str(result),
            "--query-trace", str(trace),
            "--run-id", "symphonyqg_disk_integration",
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
            "--implementation-fingerprint", "symphonyqg-fastscan-disk-native-integration",
            "--native-binary-sha256", sha256(binary),
            "--git-commit", "integration",
            "--input-manifest-sha256", sha256(input_manifest),
            "--integration-widths", "20",
        ]
        subprocess.run(command, check=True)
        artifact = validate_artifact(
            result,
            spec=SPECS_BY_KEY["05c:SymphonyQG-DiskPort"],
            expected={
                "dataset": "tiny",
                "phase": "validation",
                "run_id": "symphonyqg_disk_integration",
                "repeat_id": 0,
                "workers": 1,
                "storage_mode": "hybrid_disk",
                "cache_mode": "standard",
                "implementation_fingerprint": "symphonyqg-fastscan-disk-native-integration",
                "native_binary_sha256": sha256(binary),
                "query_split_sha256": "integration-split",
                "input_manifest_sha256": sha256(input_manifest),
                "query_order_sha256": sha256(order_path),
                "query_order_seed": 20260813,
                "warmup_queries": 0,
                "search_dram_budget_gib": 2.0,
            },
            disk_root=root / "nvme",
        )
        row = artifact["summary_rows"][0]
        assert row["bytes_read_per_query"] > 0
        assert row["recall"] >= 0.2
        print(
            "PASS SymphonyQG-DiskPort O_DIRECT native integration "
            f"recall={row['recall']:.3f} bytes/query={row['bytes_read_per_query']:.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
