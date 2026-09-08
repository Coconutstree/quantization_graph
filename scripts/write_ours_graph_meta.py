#!/usr/bin/env python3
"""Write a provenance sidecar for an existing canonical Ours graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fvecs_shape(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        dimension = struct.unpack("<i", stream.read(4))[0]
    row_bytes = 4 * (dimension + 1)
    if dimension <= 0 or path.stat().st_size % row_bytes:
        raise ValueError(f"malformed fvecs file: {path}")
    return path.stat().st_size // row_bytes, dimension


def parse_log(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    graph_build_us: str | None = None
    for line in path.read_text().splitlines():
        if "=" in line and " " not in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            values.setdefault(key, value)
        match = re.search(r"build_stage=graph_build us=(\d+) status=done", line)
        if match:
            graph_build_us = match.group(1)
    if graph_build_us is not None:
        values["graph_build_us"] = graph_build_us
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--build-log", type=Path, required=True)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    base = args.base or Path("data") / args.dataset / f"{args.dataset}_base.fvecs"
    count, dimension = fvecs_shape(base)
    log = parse_log(args.build_log)
    required = ("max_degree", "build_beam", "alpha", "seed", "graph_build_distance")
    missing = [key for key in required if not log.get(key)]
    if missing:
        raise RuntimeError(f"missing metadata {missing} in {args.build_log}")
    output = args.output or args.graph.with_suffix(".json")
    metadata = {
        "schema_version": 1,
        "suite": "02_diskann_fair",
        "graph_role": "ours_exrabitq4_vamana",
        "dataset": args.dataset,
        "base_path": str(base.resolve()),
        "base_count": count,
        "dimension": dimension,
        "metric": "squared_l2",
        "max_degree": int(log["max_degree"]),
        "build_beam": int(log["build_beam"]),
        "alpha": float(log["alpha"]),
        "seed": int(log["seed"]),
        "graph_build_distance": log["graph_build_distance"],
        "graph_build_mode": log.get("graph_build_mode", "built_in_run"),
        "graph_build_builder": log.get("graph_build_builder", ""),
        "graph_build_time_ms": (
            int(log["graph_build_us"]) / 1000 if log.get("graph_build_us") else None
        ),
        "graph_path": str(args.graph.resolve()),
        "graph_bytes": args.graph.stat().st_size,
        "graph_sha256": sha256_file(args.graph),
        "source_build_log": str(args.build_log.resolve()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
