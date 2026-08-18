#!/usr/bin/env python3
"""Parse hnsw_rabitq experiment logs into comparison-friendly CSV."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


RESULT_RE = re.compile(
    r"^(?P<search_param>\d+)\s+"
    r"(?P<recall>[0-9.]+)\s+"
    r"(?P<latency_mean_us>[0-9.eE+-]+)\s+us\b"
)

KEY_VALUE_RE = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")


FRONT_COLUMNS = [
    "dataset",
    "method",
    "recall",
    "qps",
    "latency_mean_us",
    "latency_p50_us",
    "latency_p95_us",
    "k",
    "search_param",
    "rerank",
]

DETAIL_COLUMNS = [
    "suite",
    "metric",
    "graph_degree",
    "build_ef",
    "rerank_candidates",
    "visited_nodes",
    "distance_calls",
    "index_size_mb",
    "build_time_ms",
    "graph_build_time_ms",
    "encoded_bytes_per_vector",
    "residual_record_bytes_per_vector",
    "base_count",
    "query_count",
    "dimension",
    "threads",
    "source_log",
]


def parse_log(path: Path, dataset: str, method: str) -> list[dict[str, str]]:
    meta: dict[str, str] = {
        "dataset": dataset,
        "method": method,
        "suite": "03_system_fair",
        "metric": "L2",
        "k": "10",
        "rerank": "primary4_plus_residual_rerank",
        "latency_p50_us": "",
        "threads": "",
        "source_log": str(path),
    }
    rows: list[dict[str, str]] = []

    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        all_parts = dict(KEY_VALUE_RE.findall(line))
        for key in (
            "encoded_bytes_per_vector",
            "residual_record_bytes_per_vector",
            "compact_primary_bytes_per_vector",
            "residual_code_bytes_per_vector",
        ):
            if key in all_parts:
                meta[key] = all_parts[key]

        if line.startswith("dataset="):
            meta["dataset_log_name"] = line.split("=", 1)[1]
        elif line.startswith("base_count="):
            meta["base_count"] = line.split("=", 1)[1]
        elif line.startswith("query_count="):
            meta["query_count"] = line.split("=", 1)[1]
        elif line.startswith("dimension="):
            meta["dimension"] = line.split("=", 1)[1]
        elif line.startswith("M="):
            parts = dict(KEY_VALUE_RE.findall(line))
            if "M" in parts:
                meta["graph_degree"] = parts["M"]
            if "efConstruction" in parts:
                meta["build_ef"] = parts["efConstruction"]
        elif line.startswith("build_runtime"):
            parts = dict(KEY_VALUE_RE.findall(line))
            meta["threads"] = parts.get("omp_max_threads", "")
        elif line.startswith("Build time:"):
            match = re.search(r"Build time:\s*([0-9.eE+-]+)\s+seconds", line)
            if match:
                meta["build_time_ms"] = str(float(match.group(1)) * 1000.0)
        elif line.startswith("Graph construction time:"):
            match = re.search(
                r"Graph construction time:\s*([0-9.eE+-]+)\s+seconds", line
            )
            if match:
                meta["graph_build_time_ms"] = str(float(match.group(1)) * 1000.0)
        elif line.startswith("Index storage size:"):
            match = re.search(r"Index storage size:\s*([0-9.eE+-]+)\s+MB", line)
            if match:
                meta["index_size_mb"] = match.group(1)

        result = RESULT_RE.match(line)
        if result is None:
            continue

        parts = dict(KEY_VALUE_RE.findall(line))
        row = dict(meta)
        row.update(
            {
                "recall": result.group("recall"),
                "qps": parts.get("qps", ""),
                "latency_mean_us": parts.get(
                    "total_us_per_query", result.group("latency_mean_us")
                ),
                "latency_p95_us": parts.get("p95_us", ""),
                "search_param": result.group("search_param"),
                "rerank_candidates": parts.get("rerank_candidates", ""),
                "visited_nodes": parts.get("visited_nodes", ""),
                "distance_calls": parts.get("distance_computations", ""),
            }
        )
        rows.append(row)

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    rows = parse_log(args.log, args.dataset, args.method)
    if not rows:
        raise SystemExit(f"no result rows parsed from {args.log}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    columns = FRONT_COLUMNS + DETAIL_COLUMNS
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {args.out} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
