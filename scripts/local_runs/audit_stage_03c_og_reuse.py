#!/usr/bin/env python3
"""Verify archived OG-LVQ 03C indexes byte-for-byte before COW staging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "formal_hdd_20260824"
METHOD = "OG-LVQ-DiskPort"
FINGERPRINT = "05c-og-lvq-svs-layout-native-odirect-v1"
PAGE_SIZE = 4096
SOURCE_COMMIT = "a7ec907008254764d9c8a31b75282efdaed30228"
SOURCE_FILE = Path("experiments/03_disk_system/native/og_lvq_disk_port.cpp")
ARCHIVE_RUN = (
    ROOT
    / "results/archive/legacy_layout_20260918/disk_environment/archive/05_disk_system_fair_legacy/runs"
    / RUN_ID
)
ARCHIVE_INDEX = (
    ROOT
    / "results/archive/legacy_layout_20260918/disk_environment/archive/05_disk_system_fair_legacy/hdd_raid_store"
    / "05_disk_system_fair/runs"
    / RUN_ID
    / "05C_disk_system_fair"
)
RUNTIME_ROOT = (
    ROOT
    / "work/05_disk_system_fair/disk_root/05_disk_system_fair"
    / "05C_disk_system_fair"
)
CANONICAL_ROOT = ROOT / "artifacts/graphs"
INDEX_FILES = ("graph.pages", "lvq4.pages", "centroids.f32", "index.meta")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def read_meta(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def inspect_fvecs(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        raw = stream.read(4)
    if len(raw) != 4:
        raise RuntimeError(f"empty fvecs file: {path}")
    (dimension,) = struct.unpack("<I", raw)
    record_bytes = 4 * (dimension + 1)
    size = path.stat().st_size
    if dimension == 0 or size % record_bytes:
        raise RuntimeError(f"invalid fvecs geometry: {path}")
    return size // record_bytes, dimension


def extract_export_source(text: str) -> str:
    start = text.index("void export_index(const Args& args) {")
    end = text.index("struct QueryStats {", start)
    return text[start:end]


def verify_export_compatibility() -> str:
    archived = subprocess.run(
        ["git", "show", f"{SOURCE_COMMIT}:{SOURCE_FILE}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    current = (ROOT / SOURCE_FILE).read_text(encoding="utf-8")
    archived_export = extract_export_source(archived)
    current_export = extract_export_source(current)
    if archived_export != current_export:
        raise RuntimeError("OG-LVQ export implementation changed since archived build")
    return hashlib.sha256(current_export.encode()).hexdigest()


def expected_page_bytes(rows: int, records_per_page: int) -> int:
    return ((rows + records_per_page - 1) // records_per_page) * PAGE_SIZE


def copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".reuse-tmp")
    if temporary.exists():
        temporary.unlink()
    subprocess.run(
        ["cp", "--reflink=auto", source, temporary],
        check=True,
    )
    copied_sha256 = sha256(temporary)
    if copied_sha256 != expected_sha256:
        temporary.unlink()
        raise RuntimeError(f"copy verification failed: {destination}")
    os.replace(temporary, destination)


def audit_and_stage(dataset: str, export_source_sha256: str) -> dict:
    input_manifest_path = ARCHIVE_RUN / "manifests/inputs" / f"05c_{dataset}.json"
    artifact_path = (
        ARCHIVE_RUN
        / "05C_disk_system_fair"
        / dataset
        / "artifacts/export"
        / f"{METHOD}__hybrid_disk__B2__standard__w1__r0.json"
    )
    source_dir = ARCHIVE_INDEX / dataset / METHOD / "hybrid_disk"
    destination_dir = RUNTIME_ROOT / dataset / METHOD / "hybrid_disk"
    input_manifest = read_json(input_manifest_path)
    artifact = read_json(artifact_path)
    meta = read_meta(source_dir / "index.meta")

    if artifact.get("status") != "done" or artifact.get("formal_ready") is not True:
        raise RuntimeError(f"archived artifact is not complete: {artifact_path}")
    if artifact.get("dataset") != dataset or artifact.get("method") != METHOD:
        raise RuntimeError(f"archived artifact identity mismatch: {artifact_path}")
    if artifact.get("implementation_fingerprint") != FINGERPRINT:
        raise RuntimeError(f"archived fingerprint mismatch: {artifact_path}")
    if artifact.get("implementation_parity") != "passed":
        raise RuntimeError(f"archived parity did not pass: {artifact_path}")
    if artifact.get("git_commit") != SOURCE_COMMIT:
        raise RuntimeError(f"unexpected archived source commit: {artifact_path}")
    if artifact.get("input_manifest_sha256") != sha256(input_manifest_path):
        raise RuntimeError(f"archived input manifest hash mismatch: {artifact_path}")
    if artifact.get("source_index_manifest_sha256") != sha256(source_dir / "index.meta"):
        raise RuntimeError(f"archived index metadata hash mismatch: {artifact_path}")

    current_inputs = {}
    for role, archived_file in input_manifest["files"].items():
        current_path = ROOT / "data" / dataset / f"{dataset}_{'groundtruth' if role == 'gt' else role}.{'ivecs' if role == 'gt' else 'fvecs'}"
        current_hash = sha256(current_path)
        if current_path.stat().st_size != archived_file["bytes"]:
            raise RuntimeError(f"input size differs from archived build: {current_path}")
        if current_hash != archived_file["sha256"]:
            raise RuntimeError(f"input hash differs from archived build: {current_path}")
        current_inputs[role] = {
            "path": str(current_path),
            "bytes": current_path.stat().st_size,
            "sha256": current_hash,
        }

    base_rows, dimension = inspect_fvecs(Path(current_inputs["base"]["path"]))
    integer_meta = {key: int(value) for key, value in meta.items() if key != "build_time_ms"}
    if integer_meta["n"] != base_rows or integer_meta["dim"] != dimension:
        raise RuntimeError(f"index/base geometry mismatch for {dataset}")
    if integer_meta["degree"] != 64:
        raise RuntimeError(f"index degree is not R64 for {dataset}")
    if integer_meta["graph_record_bytes"] != 260:
        raise RuntimeError(f"unexpected OG graph row size for {dataset}")
    expected_lvq_record_bytes = ((dimension + 127) // 128) * 64 + 9
    if integer_meta["lvq_record_bytes"] != expected_lvq_record_bytes:
        raise RuntimeError(f"unexpected LVQ4 row size for {dataset}")

    expected_sizes = {
        "graph.pages": expected_page_bytes(base_rows, integer_meta["graph_records_per_page"]),
        "lvq4.pages": expected_page_bytes(base_rows, integer_meta["lvq_records_per_page"]),
        "centroids.f32": 16 + integer_meta["centroid_count"] * dimension * 4,
        "index.meta": (source_dir / "index.meta").stat().st_size,
    }
    source_files = {}
    for name in INDEX_FILES:
        source = source_dir / name
        if source.stat().st_size != expected_sizes[name]:
            raise RuntimeError(f"unexpected archived index size: {source}")
        source_files[name] = {
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        }
    if sum(item["bytes"] for item in source_files.values()) != artifact["index_size_bytes"]:
        raise RuntimeError(f"artifact index size mismatch for {dataset}")

    for name, details in source_files.items():
        copy_verified(source_dir / name, destination_dir / name, details["sha256"])

    canonical_dir = CANONICAL_ROOT / dataset / "03_system_fair" / METHOD
    copy_verified(
        destination_dir / "graph.pages",
        canonical_dir / "graph.pages",
        source_files["graph.pages"]["sha256"],
    )
    copy_verified(
        destination_dir / "index.meta",
        canonical_dir / "index.meta",
        source_files["index.meta"]["sha256"],
    )

    report = {
        "schema_version": 1,
        "status": "verified_and_staged",
        "dataset": dataset,
        "method": METHOD,
        "source_run_id": RUN_ID,
        "source_git_commit": SOURCE_COMMIT,
        "export_source_sha256": export_source_sha256,
        "export_implementation_identical_to_current": True,
        "implementation_fingerprint": FINGERPRINT,
        "input_files": current_inputs,
        "index_geometry": integer_meta,
        "index_files": source_files,
        "source_dir": str(source_dir),
        "destination_dir": str(destination_dir),
        "canonical_graph_dir": str(canonical_dir),
        "copy_mode": "cp --reflink=auto with post-copy SHA-256 verification",
    }
    report_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (destination_dir / "reuse_audit.json").write_text(report_text, encoding="utf-8")
    canonical_dir.mkdir(parents=True, exist_ok=True)
    (canonical_dir / "reuse_audit.json").write_text(report_text, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="*", default=["agnews", "gist", "dbpedia"])
    args = parser.parse_args()
    unsupported = sorted(set(args.datasets) - {"agnews", "gist", "dbpedia"})
    if unsupported:
        parser.error(f"unsupported archived reuse datasets: {', '.join(unsupported)}")
    export_source_sha256 = verify_export_compatibility()
    for dataset in args.datasets:
        report = audit_and_stage(dataset, export_source_sha256)
        total = sum(item["bytes"] for item in report["index_files"].values())
        print(f"verified and staged {dataset}/{METHOD}: {total} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
