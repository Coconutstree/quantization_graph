#!/usr/bin/env python3
"""Strict blocker for accidental 05C memory-resident baseline bindings.

This executable is a guardrail: it deliberately implements the same CLI shape
as a native 05C port and fails with a machine-readable artifact, so an
unported, whole-index-in-DRAM baseline cannot be mistaken for a formal disk
result.  It is not used by the current five-system 05C comparison; those
methods are wired through native disk ports in ``ports.example.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
LAYER = "05c"
METHODS = {
    "Legacy-MemoryOnly-DiskPort": {
        "source_kernel": "memory-only reference binding",
        "binding": "legacy memory-resident adapter",
        "reason": (
            "This method intentionally represents an unported whole-index-in-DRAM "
            "binding and must not enter the formal 05C disk comparison."
        ),
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fvec_shape(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        head = stream.read(4)
    if len(head) != 4:
        return 0, 0
    dim = int.from_bytes(head, "little", signed=True)
    if dim <= 0:
        return 0, 0
    size = path.stat().st_size
    record = 4 + 4 * dim
    return (size // record, dim) if size % record == 0 else (0, dim)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Non-formal blocker for memory-only 05C system bindings"
    )
    parser.add_argument("--contract-version", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--layer", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--storage-mode", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--disk-index-dir", type=Path, required=True)
    parser.add_argument("--query", type=Path, required=True)
    parser.add_argument("--groundtruth", type=Path, required=True)
    parser.add_argument("--query-split-sha256", required=True)
    parser.add_argument("--query-order", type=Path, required=True)
    parser.add_argument("--query-order-sha256", required=True)
    parser.add_argument("--query-order-seed", type=int, required=True)
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument("--query-trace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeat-id", type=int, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--warmup-queries", type=int, required=True)
    parser.add_argument("--search-dram-budget-gib", type=float, required=True)
    parser.add_argument("--cache-mode", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--page-size", type=int, required=True)
    parser.add_argument("--max-inflight-io", type=int, required=True)
    parser.add_argument("--direct-io", required=True)
    parser.add_argument("--native-aio", required=True)
    parser.add_argument("--implementation-fingerprint", required=True)
    parser.add_argument("--native-binary-sha256", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--input-manifest-sha256", default="")
    parser.add_argument("--source-results-root", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--saq-data-root", type=Path)
    parser.add_argument("--candidate-row-offset", default="0")
    return parser.parse_args()


def write_blocked_artifact(args: argparse.Namespace, errors: list[str]) -> None:
    spec = METHODS.get(args.method, {})
    base_path = args.data_root / args.dataset / f"{args.dataset}_base.fvecs"
    base_count, dimension = fvec_shape(base_path) if base_path.exists() else (0, 0)
    query_count, _ = fvec_shape(args.query) if args.query.exists() else (0, 0)
    args.disk_index_dir.mkdir(parents=True, exist_ok=True)
    args.query_trace.parent.mkdir(parents=True, exist_ok=True)
    args.query_trace.write_text("")
    blocker_path = args.disk_index_dir / "BLOCKED_NOT_A_DISK_PORT.json"
    blocker = {
        "schema_version": SCHEMA_VERSION,
        "layer": LAYER,
        "method": args.method,
        "status": "blocked",
        "formal_ready": False,
        "reason": spec.get("reason", "method is not implemented as a 05C disk port"),
        "required_fix": (
            "Expose or implement a search path where graph/payload records are fetched "
            "from 4 KiB O_DIRECT pages during traversal, while keeping the official "
            "system's graph construction, codec and distance kernel semantics."
        ),
        "memory_only_binding": spec.get("binding", ""),
        "detected_errors": errors,
    }
    blocker_path.write_text(json.dumps(blocker, indent=2, sort_keys=True) + "\n")
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "layer": LAYER,
        "dataset": args.dataset,
        "method": args.method,
        "source_suite": "03_system_fair",
        "source_kernel": spec.get("source_kernel", ""),
        "port_kind": "algorithm_preserving_disk_port",
        "storage_mode": args.storage_mode,
        "cache_mode": args.cache_mode,
        "phase": args.phase,
        "run_id": args.run_id,
        "repeat_id": args.repeat_id,
        "workers": args.workers,
        "warmup_queries": args.warmup_queries,
        "query_split_sha256": args.query_split_sha256,
        "query_order_sha256": args.query_order_sha256,
        "query_order_seed": args.query_order_seed,
        "index_path": str(args.disk_index_dir.resolve()),
        "formal_ready": False,
        "page_size": args.page_size,
        "whole_graph_in_memory": True,
        "whole_payload_in_memory": True,
        "direct_io": False,
        "native_aio": False,
        "io_backend": "memory_only_official_binding",
        "implementation_fingerprint": args.implementation_fingerprint,
        "native_binary_sha256": args.native_binary_sha256,
        "input_manifest_sha256": args.input_manifest_sha256,
        "source_index_manifest_sha256": sha256(blocker_path),
        "git_commit": args.git_commit,
        "compiler": f"python {platform.python_version()}",
        "simd": platform.machine(),
        "base_count": base_count,
        "dimension": dimension,
        "search_dram_budget_gib": args.search_dram_budget_gib,
        "resident_bytes": 0,
        "codebook_bytes": 0,
        "worker_scratch_bytes": 0,
        "cache_bytes": 0,
        "cache_nodes": 0,
        "peak_rss_bytes": 0,
        "cpu_affinity": ",".join(map(str, sorted(os.sched_getaffinity(0))))
        if hasattr(os, "sched_getaffinity")
        else "",
        "numa_node": 0,
        "implementation_parity": "blocked",
        "parity": {"reference_artifact_sha256": sha256(blocker_path)},
        "query_trace_path": str(args.query_trace.resolve()),
        "query_trace_sha256": sha256(args.query_trace),
        "blocker": blocker,
        "summary_rows": [
            {
                "config_id": "blocked_memory_only_binding",
                "search_param": "blocked",
                "search_width": 0,
                "beam_width": 0,
                "recall": 0.0,
                "qps": 0.0,
                "latency_mean_us": 0.0,
                "latency_p50_us": 0.0,
                "latency_p95_us": 0.0,
                "latency_p99_us": 0.0,
                "query_count": query_count,
                "index_size_mb": blocker_path.stat().st_size / (1024 * 1024),
                "resident_bytes": 0,
                "peak_rss_bytes": 0,
                "io_requests_per_query": 0,
                "sectors_4k_per_query": 0,
                "bytes_read_per_query": 0,
            }
        ],
    }
    args.result_json.parent.mkdir(parents=True, exist_ok=True)
    args.result_json.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")


def main() -> int:
    started = time.perf_counter()
    args = parse_args()
    errors: list[str] = []
    if args.contract_version != str(SCHEMA_VERSION):
        errors.append(f"contract version must be {SCHEMA_VERSION}")
    if args.layer != LAYER:
        errors.append(f"this blocker implements only {LAYER}")
    if args.method not in METHODS:
        errors.append(f"unsupported method {args.method}")
    if args.storage_mode != "hybrid_disk":
        errors.append("05C storage-mode must be hybrid_disk")
    if args.direct_io != "required" or args.native_aio != "required":
        errors.append("formal 05C requires direct I/O and native async I/O")
    errors.append(
        "blocked: repository binding is memory-resident and cannot satisfy 05C "
        "page-level disk-search semantics"
    )
    write_blocked_artifact(args, errors)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print(
        json.dumps(
            {
                "status": "blocked",
                "method": args.method,
                "elapsed_ms": elapsed_ms,
                "artifact": str(args.result_json),
                "reason": errors[-1],
            },
            sort_keys=True,
        )
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
