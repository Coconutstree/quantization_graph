#!/usr/bin/env python3
"""Validate fvecs/ivecs datasets and write experiment manifests."""

from __future__ import annotations

import argparse
import json
import os
import platform
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DATASETS = ("dbpedia", "gist", "agnews")


def read_i32_at(path: Path, offset: int) -> int:
    with path.open("rb") as f:
        f.seek(offset)
        raw = f.read(4)
    if len(raw) != 4:
        raise ValueError(f"cannot read int32 at offset {offset}")
    return struct.unpack("<i", raw)[0]


def inspect_vecs(path: Path, value_kind: str) -> dict[str, Any]:
    if value_kind not in {"float32", "int32"}:
        raise ValueError(f"unsupported value kind: {value_kind}")

    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "status": "missing",
        }

    size = path.stat().st_size
    info: dict[str, Any] = {
        "path": str(path),
        "abs_path": str(path.resolve()),
        "exists": True,
        "size_bytes": size,
        "mtime_unix": int(path.stat().st_mtime),
        "value_kind": value_kind,
    }

    if size < 4:
        info["status"] = "invalid"
        info["error"] = "file is smaller than one int32 dimension header"
        return info

    dim = read_i32_at(path, 0)
    value_size = 4
    row_size = 4 + dim * value_size

    info.update(
        {
            "dimension": dim,
            "row_size_bytes": row_size,
        }
    )

    if dim <= 0:
        info["status"] = "invalid"
        info["error"] = "dimension header must be positive"
        return info

    if row_size <= 4 or size % row_size != 0:
        info["status"] = "invalid"
        info["error"] = "file size is not divisible by row size"
        info["remainder_bytes"] = size % row_size
        return info

    count = size // row_size
    info["count"] = count

    last_dim = read_i32_at(path, (count - 1) * row_size) if count else None
    info["first_dimension_header"] = dim
    info["last_dimension_header"] = last_dim

    if last_dim != dim:
        info["status"] = "invalid"
        info["error"] = "last row dimension header does not match first row"
        return info

    info["status"] = "ok"
    return info


def dataset_manifest(dataset: str, data_root: Path) -> dict[str, Any]:
    dataset_dir = data_root / dataset
    base_path = dataset_dir / f"{dataset}_base.fvecs"
    query_path = dataset_dir / f"{dataset}_query.fvecs"
    gt_path = dataset_dir / f"{dataset}_groundtruth.ivecs"

    manifest: dict[str, Any] = {
        "dataset": dataset,
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_root),
        "dataset_dir": str(dataset_dir),
        "expected_files": {
            "base": str(base_path),
            "query": str(query_path),
            "groundtruth": str(gt_path),
        },
        "files": {
            "base": inspect_vecs(base_path, "float32"),
            "query": inspect_vecs(query_path, "float32"),
            "groundtruth": inspect_vecs(gt_path, "int32"),
        },
        "checks": [],
        "errors": [],
        "warnings": [],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": os.getcwd(),
        },
    }

    files = manifest["files"]
    for name, info in files.items():
        if info.get("status") != "ok":
            manifest["errors"].append(f"{name}: {info.get('error', info.get('status'))}")

    if not manifest["errors"]:
        base = files["base"]
        query = files["query"]
        gt = files["groundtruth"]

        checks = manifest["checks"]

        same_dim = base["dimension"] == query["dimension"]
        checks.append(
            {
                "name": "base_query_dimension_match",
                "status": "ok" if same_dim else "failed",
                "base_dimension": base["dimension"],
                "query_dimension": query["dimension"],
            }
        )
        if not same_dim:
            manifest["errors"].append("base/query dimensions differ")

        same_query_count = query["count"] == gt["count"]
        checks.append(
            {
                "name": "query_groundtruth_count_match",
                "status": "ok" if same_query_count else "failed",
                "query_count": query["count"],
                "groundtruth_count": gt["count"],
            }
        )
        if not same_query_count:
            manifest["errors"].append("query/groundtruth row counts differ")

        gt_k_ok = gt["dimension"] >= 10
        checks.append(
            {
                "name": "groundtruth_k_at_least_10",
                "status": "ok" if gt_k_ok else "failed",
                "groundtruth_k": gt["dimension"],
            }
        )
        if not gt_k_ok:
            manifest["errors"].append("groundtruth k is smaller than 10")

        manifest["summary"] = {
            "base_count": base["count"],
            "query_count": query["count"],
            "dimension": base["dimension"],
            "groundtruth_k": gt["dimension"],
            "base_size_bytes": base["size_bytes"],
            "query_size_bytes": query["size_bytes"],
            "groundtruth_size_bytes": gt["size_bytes"],
        }

    if manifest["errors"]:
        manifest["status"] = "failed"

    return manifest


def write_manifest(manifest: dict[str, Any], out_root: Path) -> Path:
    dataset = manifest["dataset"]
    manifest_dir = out_root / dataset / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    out_path = manifest_dir / f"{dataset}_dataset_manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=list(DEFAULT_DATASETS),
        help="Dataset names to check. Default: dbpedia gist.",
    )
    parser.add_argument(
        "--data-root",
        default=str(Path(__file__).resolve().parents[1] / "data"),
        help="Dataset root directory (default: <repo>/data).",
    )
    parser.add_argument(
        "--out-root",
        default="results/memory_environment/dataset_artifacts",
        help="Result root directory.",
    )
    args = parser.parse_args()

    data_root = Path(args.data_root)
    out_root = Path(args.out_root)

    exit_code = 0
    for dataset in args.datasets:
        manifest = dataset_manifest(dataset, data_root)
        out_path = write_manifest(manifest, out_root)
        summary = manifest.get("summary", {})
        if manifest["status"] == "ok":
            print(
                f"{dataset}: ok "
                f"base={summary.get('base_count')} "
                f"query={summary.get('query_count')} "
                f"dim={summary.get('dimension')} "
                f"gt_k={summary.get('groundtruth_k')} "
                f"manifest={out_path}"
            )
        else:
            exit_code = 1
            print(f"{dataset}: failed manifest={out_path}", file=sys.stderr)
            for error in manifest["errors"]:
                print(f"  error: {error}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
