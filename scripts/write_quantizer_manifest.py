#!/usr/bin/env python3
"""Write suite 01 quantizer fairness manifests."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DATASETS = ("dbpedia", "gist", "agnews")


def run_git_commit(path: Path) -> str:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return output.strip()


def module_path(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return ""
    if spec.origin:
        return spec.origin
    if spec.submodule_search_locations:
        return ";".join(str(path) for path in spec.submodule_search_locations)
    return ""


def exists_status(path: str, executable: bool = False) -> str:
    if not path:
        return "missing"
    p = Path(path)
    if not p.exists():
        return "missing"
    if executable and not p.is_file():
        return "missing"
    if executable and not p.stat().st_mode & 0o111:
        return "not_executable"
    return "ready"


def method_rows(dataset: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    faiss_path = Path("baselines/faiss/upstream")
    saq_path = Path("baselines/saq")
    svs_path = Path("baselines/svs")
    ours_path = Path("Ours")

    svs_module = module_path("svs")

    rows = [
        {
            "suite": "01_quantizer_fair",
            "dataset": dataset,
            "method": "PQ",
            "status": "ready",
            "implementation": "faiss_product_quantizer",
            "source_path": str(faiss_path),
            "commit": run_git_commit(faiss_path),
            "binary_or_module_path": "baselines/builds/faiss-cmake43/faiss/libfaiss.a",
            "build_status": exists_status(
                "baselines/builds/faiss-cmake43/faiss/libfaiss.a"
            ),
            "package_version": "",
            "generated_at_utc": now,
            "notes": "Use Faiss ProductQuantizer/IndexPQ adapter.",
        },
        {
            "suite": "01_quantizer_fair",
            "dataset": dataset,
            "method": "SQ",
            "status": "ready",
            "implementation": "faiss_scalar_quantizer",
            "source_path": str(faiss_path),
            "commit": run_git_commit(faiss_path),
            "binary_or_module_path": "baselines/builds/faiss-cmake43/faiss/libfaiss.a",
            "build_status": exists_status(
                "baselines/builds/faiss-cmake43/faiss/libfaiss.a"
            ),
            "package_version": "",
            "generated_at_utc": now,
            "notes": "Use Faiss ScalarQuantizer/IndexScalarQuantizer adapter; main config SQ4.",
        },
        {
            "suite": "01_quantizer_fair",
            "dataset": dataset,
            "method": "SAQ",
            "status": "ready",
            "implementation": "official_saq_binary",
            "source_path": str(saq_path),
            "commit": run_git_commit(saq_path),
            "binary_or_module_path": "baselines/saq/bin/create_index;baselines/saq/bin/test_relative_error;baselines/saq/bin/test_qps",
            "build_status": "ready"
            if all(
                exists_status(path, executable=True) == "ready"
                for path in (
                    "baselines/saq/bin/create_index",
                    "baselines/saq/bin/test_relative_error",
                    "baselines/saq/bin/test_qps",
                )
            )
            else "missing",
            "package_version": "",
            "generated_at_utc": now,
            "notes": "Official SAQ binaries built with /usr/bin/g++-11; AVX512 required.",
        },
        {
            "suite": "01_quantizer_fair",
            "dataset": dataset,
            "method": "LVQ",
            "status": "ready" if svs_module else "missing",
            "implementation": "intel_svs_scalable_vs_python",
            "source_path": str(svs_path),
            "commit": run_git_commit(svs_path),
            "binary_or_module_path": svs_module,
            "build_status": "import_ok" if svs_module else "missing",
            "package_version": "scalable-vs 0.4.0",
            "generated_at_utc": now,
            "notes": "Official Intel SVS package path for LVQ; record LVQ config per run.",
        },
        {
            "suite": "01_quantizer_fair",
            "dataset": dataset,
            "method": "Ours",
            "status": "ready",
            "implementation": "ours_diskann_rabitq_bridge",
            "source_path": str(ours_path),
            "commit": run_git_commit(ours_path),
            "binary_or_module_path": "experiments/02_diskann_fair/target/release/run_diskann_fair",
            "build_status": exists_status("experiments/02_diskann_fair/target/release/run_diskann_fair", executable=True),
            "package_version": "",
            "generated_at_utc": now,
            "notes": "Use current project encode/query-distance path; build_status may be missing until Ours runner is rebuilt.",
        },
    ]

    for row in rows:
        if row["build_status"] in {"missing", "not_executable"} and row["method"] != "Ours":
            row["status"] = "missing"
    return rows


def write_manifest(dataset: str, out_root: Path) -> Path:
    out_dir = out_root / "01_quantizer_fair" / dataset / "manifests"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "01_quantizer_fair_manifest.csv"
    rows = method_rows(dataset)
    fieldnames = [
        "suite",
        "dataset",
        "method",
        "status",
        "implementation",
        "source_path",
        "commit",
        "binary_or_module_path",
        "build_status",
        "package_version",
        "generated_at_utc",
        "notes",
    ]
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=list(DEFAULT_DATASETS),
        help="Dataset names to write manifests for. Default: dbpedia gist.",
    )
    parser.add_argument("--out-root", default="results")
    args = parser.parse_args()

    out_root = Path(args.out_root)
    for dataset in args.datasets:
        out_path = write_manifest(dataset, out_root)
        print(f"{dataset}: wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
