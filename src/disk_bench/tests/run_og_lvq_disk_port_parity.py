#!/usr/bin/env python3
"""Parity check: OG-LVQ disk port versus official SVS memory search."""

from __future__ import annotations

import hashlib
import json
import os
import numpy as np
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
REPO = SUITE.parents[1]
sys.path.insert(0, str(SUITE))
sys.path.insert(0, os.environ.get("SVS_PYTHONPATH", str(REPO / "baselines/svs/python")))

import svs  # noqa: E402


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


def recall(ids: list[int], truth: list[int], k: int = 10) -> float:
    wanted = set(truth[:k])
    return sum(1 for x in ids[:k] if int(x) in wanted) / k


def main() -> int:
    binary = REPO / "build/disk/native/qgraph05_og_lvq_disk_port"
    if not binary.exists():
        raise SystemExit(f"build qgraph05_og_lvq_disk_port first: {binary}")

    with tempfile.TemporaryDirectory(prefix="qgraph05_og_lvq_parity_") as tmp:
        root = Path(tmp)
        dataset_root = root / "data/tiny"
        dataset_root.mkdir(parents=True)
        rng = random.Random(20260814)
        count, dim, query_count, width = 384, 64, 8, 40
        base = [[rng.uniform(-1.0, 1.0) for _ in range(dim)] for _ in range(count)]
        queries = [
            [base[i][j] + rng.uniform(-0.015, 0.015) for j in range(dim)]
            for i in range(query_count)
        ]
        truth = [
            sorted(
                range(count),
                key=lambda i: sum((base[i][j] - query[j]) ** 2 for j in range(dim)),
            )[:10]
            for query in queries
        ]
        base_path = dataset_root / "tiny_base.fvecs"
        query_path = root / "query.fvecs"
        gt_path = root / "gt.ivecs"
        order_path = root / "order.u32"
        input_manifest = root / "input.json"
        result = root / "validation.json"
        trace = root / "validation.queries.jsonl"
        disk_index = root / "nvme/index"
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
            "--method", "OG-LVQ-DiskPort",
            "--storage-mode", "hybrid_disk",
            "--data-root", str(root / "data"),
            "--disk-index-dir", str(disk_index),
            "--query", str(query_path),
            "--groundtruth", str(gt_path),
            "--query-split-sha256", "parity-split",
            "--query-order", str(order_path),
            "--query-order-sha256", sha256(order_path),
            "--query-order-seed", "20260813",
            "--result-json", str(result),
            "--query-trace", str(trace),
            "--run-id", "og_lvq_disk_parity",
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
            "--implementation-fingerprint", "og-lvq-svs-layout-disk-parity",
            "--native-binary-sha256", sha256(binary),
            "--git-commit", "integration",
            "--input-manifest-sha256", sha256(input_manifest),
            "--integration-widths", str(width),
        ]
        subprocess.run(command, cwd=REPO, check=True)

        disk_rows = {}
        for line in trace.read_text().splitlines():
            item = json.loads(line)
            disk_rows[int(item["query_id"])] = item["result_ids"]

        queries_np = svs.read_vecs(str(query_path))
        index = svs.Vamana(
            str(disk_index / "official_svs/config"),
            str(disk_index / "official_svs/graph"),
            str(disk_index / "official_svs/data"),
            svs.DistanceType.L2,
            num_threads=1,
        )
        index.search_window_size = width
        overlaps = []
        recall_deltas = []
        for qid, query in enumerate(queries_np):
            ids, _ = index.search(query, 10)
            memory_ids = [int(x) for x in np.asarray(ids).reshape(-1)]
            disk_ids = [int(x) for x in disk_rows[qid]]
            overlaps.append(len(set(memory_ids[:10]) & set(disk_ids[:10])) / 10)
            recall_deltas.append(abs(recall(memory_ids, truth[qid]) - recall(disk_ids, truth[qid])))

        mean_overlap = sum(overlaps) / len(overlaps)
        max_recall_delta = max(recall_deltas)
        assert mean_overlap >= 0.99, (mean_overlap, overlaps)
        assert max_recall_delta <= 1e-3, (max_recall_delta, recall_deltas)
        print(
            "PASS OG-LVQ disk/native parity vs official SVS memory "
            f"mean_top10_overlap={mean_overlap:.3f} max_recall_delta={max_recall_delta:.3g}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
