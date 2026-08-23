#!/usr/bin/env python3
"""Small exact 05B PQ/SQ/SAQ/Ours export/search/contract integration test.

This test deliberately uses the real O_DIRECT + libaio graph reader.  Its
numbers are correctness-only and must never be used as paper measurements.
PQ/SQ/SAQ share one baseline graph; Ours uses a distinct native graph with
exactly N base nodes, matching the source 02/03 experiment contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

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


def write_graph(path: Path, count: int, degree: int, *, virtual_entry: bool) -> None:
    adjacency: list[list[int]] = []
    for node in range(count):
        neighbors = []
        for delta in range(1, degree // 2 + 1):
            neighbors.extend(((node - delta) % count, (node + delta) % count))
        adjacency.append(neighbors)
    if virtual_entry:
        adjacency.append([0])  # virtual entry used by the baseline shared graph
    size = 24 + sum(4 + 4 * len(row) for row in adjacency)
    with path.open("wb") as stream:
        stream.write(struct.pack("<QIIQ", size, degree, count if virtual_entry else 0, 1))
        for row in adjacency:
            stream.write(struct.pack("<I", len(row)))
            stream.write(struct.pack(f"<{len(row)}I", *row))


def make_common(
    binary: Path,
    root: Path,
    data_root: Path,
    graph: Path,
    ours_graph: Path,
    query: Path,
    groundtruth: Path,
    order: Path,
    result: Path,
    trace: Path,
    phase: str,
    method: str,
    layer: str = "05b",
) -> list[str]:
    slug = method.split("-")[0].lower()
    command = [
        str(binary),
        "--contract-version", "2",
        "--phase", phase,
        "--layer", layer,
        "--dataset", "tiny",
        "--method", method,
        "--storage-mode", "hybrid_disk",
        "--data-root", str(data_root),
        "--disk-index-dir", str(root / "disk" / f"{layer}_{slug}"),
        "--query", str(query),
        "--groundtruth", str(groundtruth),
        "--query-order", str(order),
        "--query-order-sha256", sha256(order),
        "--query-order-seed", "20260813",
        "--query-split-sha256", "1" * 64,
        "--result-json", str(result),
        "--query-trace", str(trace),
        "--run-id", "native_integration_pq",
        "--repeat-id", "0",
        "--workers", "1",
        "--warmup-queries", "2",
        "--search-dram-budget-gib", "2.0",
        "--cache-mode", "c0",
        "--seed", "20260813",
        "--page-size", "4096",
        "--max-inflight-io", "128",
        "--direct-io", "required",
        "--native-aio", "required",
        "--implementation-fingerprint", f"native-integration-{slug}-v1",
        "--native-binary-sha256", sha256(binary),
        "--git-commit", "integration",
        "--input-manifest-sha256", "2" * 64,
        "--integration-widths", "10",
        "--integration-beams", "1",
    ]
    if method == "Ours-Disk":
        command.extend([
            "--ours-graph", str(ours_graph),
            "--ours-graph-sha256", sha256(ours_graph),
        ])
        if layer == "05b":
            command.extend([
                "--ablations",
                "full4-resident/no-gate,db1-resident/full4-on-ssd,db1+coalescing,db1+coalescing+reuse",
            ])
    else:
        command.extend([
            "--shared-graph", str(graph),
            "--shared-graph-sha256", sha256(graph),
        ])
    return command


def main() -> int:
    repo = SUITE.parents[1]
    binary = repo / "experiments/02_diskann_fair/target/debug/qgraph05_shared_graph_port"
    if not binary.exists():
        raise SystemExit(f"build the integration binary first: {binary}")
    with tempfile.TemporaryDirectory(prefix="qgraph05_pq_integration_") as tmp:
        root = Path(tmp)
        data_root = root / "data"
        dataset_root = data_root / "tiny"
        dataset_root.mkdir(parents=True)
        rng = random.Random(20260813)
        count, dimension = 512, 8
        base = [[rng.uniform(-2.0, 2.0) for _ in range(dimension)] for _ in range(count)]
        queries = [
            [base[index][dim] + rng.uniform(-0.02, 0.02) for dim in range(dimension)]
            for index in range(8)
        ]
        truth = []
        for query in queries:
            ranked = sorted(
                range(count),
                key=lambda index: sum((base[index][dim] - query[dim]) ** 2 for dim in range(dimension)),
            )
            truth.append(ranked[:10])
        base_path = dataset_root / "tiny_base.fvecs"
        query_path = root / "query.fvecs"
        gt_path = root / "gt.ivecs"
        graph_path = root / "shared.graph.bin"
        ours_graph_path = root / "ours.graph.bin"
        order_path = root / "order.u32"
        write_fvecs(base_path, base)
        write_fvecs(query_path, queries)
        write_ivecs(gt_path, truth)
        write_graph(graph_path, count, 16, virtual_entry=True)
        write_graph(ours_graph_path, count, 16, virtual_entry=False)
        graph_path.with_suffix(".json").write_text(
            "suite=02_diskann_fair\nstart_index=0\nbase_count=512\ndimension=8\n"
        )
        order_path.write_bytes(b"".join(struct.pack("<I", index) for index in range(8)))

        for method in (
            "PQ-DiskANN-Disk",
            "SQ-DiskANN-Disk",
            "SAQ-DiskANN-Disk",
            "Ours-Disk",
        ):
            slug = method.split("-")[0].lower()
            export_result = root / f"{slug}_export.json"
            subprocess.run(
                make_common(
                    binary, root, data_root, graph_path, ours_graph_path, query_path, gt_path,
                    order_path, export_result, root / f"{slug}_export.trace", "export", method,
                ),
                check=True,
            )
            result = root / f"{slug}_validation.json"
            trace = root / f"{slug}_validation.queries.jsonl"
            subprocess.run(
                make_common(
                    binary, root, data_root, graph_path, ours_graph_path, query_path, gt_path,
                    order_path, result, trace, "validation", method,
                ),
                check=True,
            )
            expected = {
                "dataset": "tiny",
                "phase": "validation",
                "run_id": "native_integration_pq",
                "repeat_id": 0,
                "workers": 1,
                "storage_mode": "hybrid_disk",
                "cache_mode": "c0",
                "implementation_fingerprint": f"native-integration-{slug}-v1",
                "native_binary_sha256": sha256(binary),
                "query_split_sha256": "1" * 64,
                "input_manifest_sha256": "2" * 64,
                "query_order_sha256": sha256(order_path),
                "query_order_seed": 20260813,
                "warmup_queries": 2,
                "search_dram_budget_gib": 2.0,
            }
            artifact = validate_artifact(
                result,
                spec=SPECS_BY_KEY[f"05b:{method}"],
                expected=expected,
                disk_root=root / "disk",
            )
            row = artifact["summary_rows"][0]
            assert row["bytes_read_per_query"] > 0
            assert artifact["parity"]["mean_top10_overlap"] == 1.0
            if method == "Ours-Disk":
                assert artifact["graph_role"] == "ours_native"
                assert artifact["source_graph_sha256"] == sha256(ours_graph_path)
                assert artifact["shared_graph_sha256"] == ""
                assert [
                    row["ablation"] for row in artifact["summary_rows"]
                ] == [
                    "full4-resident/no-gate",
                    "db1-resident/full4-on-ssd",
                    "db1+coalescing",
                    "db1+coalescing+reuse",
                ]
                gated = artifact["summary_rows"][1:]
                assert all(row["db1_checks"] > 0 for row in gated)
                assert all(row["full4_page_reads"] > 0 for row in gated)
            else:
                assert artifact["graph_role"] == "shared_baseline"
                assert artifact["shared_graph_sha256"] == sha256(graph_path)
            print(
                f"05B {method} native integration passed: "
                f"recall={row['recall']:.3f}, bytes/query={row['bytes_read_per_query']:.0f}"
            )

        # 05C invokes the exact same Ours binary, graph and production search
        # path, but runs only the complete db1+coalescing+reuse configuration.
        method = "Ours-Disk"
        export_result = root / "ours_05c_export.json"
        subprocess.run(
            make_common(
                binary, root, data_root, graph_path, ours_graph_path, query_path, gt_path,
                order_path, export_result, root / "ours_05c_export.trace", "export", method,
                layer="05c",
            ),
            check=True,
        )
        result = root / "ours_05c_validation.json"
        trace = root / "ours_05c_validation.queries.jsonl"
        subprocess.run(
            make_common(
                binary, root, data_root, graph_path, ours_graph_path, query_path, gt_path,
                order_path, result, trace, "validation", method, layer="05c",
            ),
            check=True,
        )
        artifact = validate_artifact(
            result,
            spec=SPECS_BY_KEY["05c:Ours-Disk"],
            expected={
                "dataset": "tiny",
                "phase": "validation",
                "run_id": "native_integration_pq",
                "repeat_id": 0,
                "workers": 1,
                "storage_mode": "hybrid_disk",
                "cache_mode": "c0",
                "implementation_fingerprint": "native-integration-ours-v1",
                "native_binary_sha256": sha256(binary),
                "query_split_sha256": "1" * 64,
                "input_manifest_sha256": "2" * 64,
                "query_order_sha256": sha256(order_path),
                "query_order_seed": 20260813,
                "warmup_queries": 2,
                "search_dram_budget_gib": 2.0,
            },
            disk_root=root / "disk",
        )
        assert artifact["source_suite"] == "03_system_fair"
        assert artifact["graph_role"] == "ours_native"
        assert artifact["source_graph_sha256"] == sha256(ours_graph_path)
        assert len(artifact["summary_rows"]) == 1
        assert artifact["summary_rows"][0]["ablation"] == "db1+coalescing+reuse"
        print("05C Ours-Disk same-method native integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
