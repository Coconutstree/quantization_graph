#!/usr/bin/env python3
"""Prepare a reproducible 10M ANN benchmark from the official UCI SIFT10M.

UCI publishes 11,164,866 descriptors without an ANN base/query split.  This
repository uses rows [0, 10_000_000) as base vectors and the final 10,000 rows
as queries.  The intervening rows are reserved and are not searched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import tarfile
import zipfile
from pathlib import Path

import h5py
import numpy as np


SOURCE_URL = "https://archive.ics.uci.edu/static/public/353/sift10m.zip"
SOURCE_DOI = "10.24432/C5S603"
EXPECTED_ROWS = 11_164_866
DIMENSION = 128
BASE_ROWS = 10_000_000
QUERY_ROWS = 10_000
GROUNDTRUTH_K = 1_000
MAT_MEMBER = "SIFT10M/SIFT10Mfeatures.mat"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vecs_shape(path: Path, value_bytes: int = 4) -> tuple[int, int]:
    with path.open("rb") as stream:
        raw = stream.read(4)
    if len(raw) != 4:
        raise ValueError(f"empty vecs file: {path}")
    dimension = struct.unpack("<i", raw)[0]
    row_bytes = 4 + dimension * value_bytes
    size = path.stat().st_size
    if dimension <= 0 or size % row_bytes:
        raise ValueError(f"malformed vecs file: {path}")
    return size // row_bytes, dimension


def extract_feature_mat(archive: Path, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size == 1_059_151_392:
        print(f"feature MAT already present: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(archive) as outer:
        with outer.open("SIFT10M.tar.gz") as compressed_tar:
            with tarfile.open(fileobj=compressed_tar, mode="r|gz") as inner:
                for member in inner:
                    if member.name != MAT_MEMBER:
                        continue
                    source = inner.extractfile(member)
                    if source is None:
                        raise RuntimeError(f"could not extract {MAT_MEMBER}")
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(source, output, length=8 << 20)
                    temporary.replace(destination)
                    print(f"extracted {member.name} ({member.size} bytes)")
                    return
    raise RuntimeError(f"{MAT_MEMBER} not found in {archive}")


def select_descriptor_dataset(h5: h5py.File) -> h5py.Dataset:
    matches: list[h5py.Dataset] = []

    def visit(_name: str, obj: object) -> None:
        if not isinstance(obj, h5py.Dataset) or obj.ndim != 2:
            return
        if DIMENSION in obj.shape and EXPECTED_ROWS in obj.shape:
            matches.append(obj)

    h5.visititems(visit)
    if len(matches) != 1:
        found = [(item.name, item.shape, str(item.dtype)) for item in matches]
        raise RuntimeError(f"expected one {DIMENSION}D descriptor dataset, found {found}")
    if matches[0].dtype != np.dtype(np.uint8):
        raise RuntimeError(
            f"expected uint8 SIFT descriptors, found {matches[0].dtype} at {matches[0].name}"
        )
    return matches[0]


def read_rows(dataset: h5py.Dataset, start: int, end: int) -> np.ndarray:
    if dataset.shape == (DIMENSION, EXPECTED_ROWS):
        return np.asarray(dataset[:, start:end], dtype=np.uint8).T.copy()
    if dataset.shape == (EXPECTED_ROWS, DIMENSION):
        return np.asarray(dataset[start:end, :], dtype=np.uint8)
    raise RuntimeError(f"unexpected descriptor shape: {dataset.shape}")


def write_partition(
    dataset: h5py.Dataset,
    start: int,
    end: int,
    fvecs_path: Path,
    bin_path: Path,
    chunk_rows: int,
) -> None:
    expected = end - start
    if (
        fvecs_path.is_file()
        and vecs_shape(fvecs_path) == (expected, DIMENSION)
        and bin_path.is_file()
        and bin_path.stat().st_size == 8 + expected * DIMENSION
    ):
        print(f"partition already present: {fvecs_path}")
        return

    fvecs_tmp = fvecs_path.with_suffix(fvecs_path.suffix + ".tmp")
    bin_tmp = bin_path.with_suffix(bin_path.suffix + ".tmp")
    fvecs_tmp.unlink(missing_ok=True)
    bin_tmp.unlink(missing_ok=True)
    with fvecs_tmp.open("wb") as fvecs_out, bin_tmp.open("wb") as bin_out:
        bin_out.write(struct.pack("<II", expected, DIMENSION))
        for offset in range(start, end, chunk_rows):
            stop = min(offset + chunk_rows, end)
            vectors = read_rows(dataset, offset, stop)
            if vectors.shape != (stop - offset, DIMENSION):
                raise RuntimeError(f"bad descriptor block shape: {vectors.shape}")
            bin_out.write(vectors.tobytes(order="C"))
            rows = np.empty((len(vectors), DIMENSION + 1), dtype=np.float32)
            rows[:, 0].view(np.int32)[:] = DIMENSION
            rows[:, 1:] = vectors
            fvecs_out.write(rows.tobytes(order="C"))
            done = stop - start
            if done == expected or done % 1_000_000 == 0:
                print(f"{fvecs_path.name}: {done:,}/{expected:,}")
    fvecs_tmp.replace(fvecs_path)
    bin_tmp.replace(bin_path)


def convert_vectors(mat_path: Path, out_dir: Path, chunk_rows: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(mat_path, "r") as h5:
        descriptors = select_descriptor_dataset(h5)
        print(
            f"descriptor dataset: {descriptors.name} shape={descriptors.shape} "
            f"dtype={descriptors.dtype}"
        )
        write_partition(
            descriptors,
            0,
            BASE_ROWS,
            out_dir / "sift10m_base.fvecs",
            out_dir / "sift10m_base.u8bin",
            chunk_rows,
        )
        query_start = EXPECTED_ROWS - QUERY_ROWS
        write_partition(
            descriptors,
            query_start,
            EXPECTED_ROWS,
            out_dir / "sift10m_query.fvecs",
            out_dir / "sift10m_query.u8bin",
            chunk_rows,
        )


def convert_diskann_groundtruth(source: Path, destination: Path) -> None:
    with source.open("rb") as stream:
        rows, width = struct.unpack("<II", stream.read(8))
        if (rows, width) != (QUERY_ROWS, GROUNDTRUTH_K):
            raise RuntimeError(
                f"unexpected DiskANN ground truth shape {(rows, width)}; "
                f"expected {(QUERY_ROWS, GROUNDTRUTH_K)}"
            )
        ids = np.fromfile(stream, dtype="<u4", count=rows * width).reshape(rows, width)
        distances = np.fromfile(stream, dtype="<f4", count=rows * width).reshape(rows, width)
        if stream.read(1):
            raise RuntimeError(f"trailing bytes in {source}")
    if np.any(ids >= BASE_ROWS):
        raise RuntimeError("ground truth contains an out-of-range base ID")
    if not np.isfinite(distances).all() or np.any(distances[:, 1:] < distances[:, :-1]):
        raise RuntimeError("ground-truth distances are non-finite or not sorted")

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    rows_out = np.empty((rows, width + 1), dtype="<i4")
    rows_out[:, 0] = width
    rows_out[:, 1:] = ids.astype(np.int32, copy=False)
    with temporary.open("wb") as output:
        output.write(rows_out.tobytes(order="C"))
    temporary.replace(destination)
    print(f"wrote {destination} shape=({rows}, {width})")


def fnv1a64(path: Path) -> str:
    value = 1_469_598_103_934_665_603
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            for byte in chunk:
                value ^= byte
                value = (value * 1_099_511_628_211) & 0xFFFF_FFFF_FFFF_FFFF
    return f"{value:x}"


def write_fixed_candidates(out_dir: Path, candidate_root: Path) -> None:
    gt_path = out_dir / "sift10m_groundtruth.ivecs"
    rows, width = vecs_shape(gt_path)
    if (rows, width) != (QUERY_ROWS, GROUNDTRUTH_K):
        raise RuntimeError(f"unexpected ground-truth shape: {(rows, width)}")
    raw = np.fromfile(gt_path, dtype="<i4").reshape(rows, width + 1)
    if not np.all(raw[:, 0] == width):
        raise RuntimeError("inconsistent ivecs row headers")
    candidates = np.ascontiguousarray(raw[:, 1:], dtype="<i4")
    if np.any(candidates < 0) or np.any(candidates >= BASE_ROWS):
        raise RuntimeError("fixed candidates contain an out-of-range base ID")

    target_dir = candidate_root / "01_quantizer_fair" / "sift10m"
    target_dir.mkdir(parents=True, exist_ok=True)
    binary = target_dir / "fixed_candidates_k1000.bin"
    temporary = binary.with_suffix(binary.suffix + ".tmp")
    with temporary.open("wb") as output:
        output.write(candidates.tobytes(order="C"))
    temporary.replace(binary)
    meta = {
        "dataset": "sift10m",
        "status": "ok",
        "strategy": "exact_groundtruth_top1000",
        "candidate_size": GROUNDTRUTH_K,
        "base_count": BASE_ROWS,
        "indexed_base_count": BASE_ROWS,
        "query_count": QUERY_ROWS,
        "dimension": DIMENSION,
        "groundtruth_k": GROUNDTRUTH_K,
        "hard_negative_source": None,
        "hnsw_negative_count": 0,
        "fallback_random_count": 0,
        "fingerprint_fnv1a64": fnv1a64(binary),
        "binary_format": {
            "dtype": "int32_little_endian",
            "shape": [QUERY_ROWS, GROUNDTRUTH_K],
            "row_headers": False,
        },
        "paths": {
            "candidate_bin": str(binary.resolve()),
            "groundtruth": str(gt_path.resolve()),
        },
        "notes": [
            "The exact top-1000 ground truth fills every candidate row, so no approximate hard negatives are needed.",
            "This is byte-identical to the candidate rows produced by the generic builder when groundtruth_k equals candidate_size.",
        ],
    }
    meta_path = target_dir / "fixed_candidates_k1000.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(f"wrote {binary} and {meta_path}")


def write_manifest(archive: Path, mat_path: Path, out_dir: Path) -> None:
    files = {
        "archive": archive,
        "feature_mat": mat_path,
        "base_fvecs": out_dir / "sift10m_base.fvecs",
        "query_fvecs": out_dir / "sift10m_query.fvecs",
        "groundtruth_ivecs": out_dir / "sift10m_groundtruth.ivecs",
        "base_u8bin": out_dir / "sift10m_base.u8bin",
        "query_u8bin": out_dir / "sift10m_query.u8bin",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"cannot write complete manifest; missing {missing}")
    with h5py.File(mat_path, "r") as h5:
        descriptors = select_descriptor_dataset(h5)
        descriptor_info = {
            "hdf5_path": descriptors.name,
            "shape": list(descriptors.shape),
            "dtype": str(descriptors.dtype),
        }
    manifest = {
        "dataset": "sift10m",
        "status": "ready",
        "source": {
            "name": "UCI SIFT10M",
            "url": SOURCE_URL,
            "doi": SOURCE_DOI,
            "upstream_rows": EXPECTED_ROWS,
            "upstream_dimension": DIMENSION,
            "descriptor_dataset": descriptor_info,
        },
        "derived_split": {
            "base_rows": [0, BASE_ROWS],
            "reserved_rows": [BASE_ROWS, EXPECTED_ROWS - QUERY_ROWS],
            "query_rows": [EXPECTED_ROWS - QUERY_ROWS, EXPECTED_ROWS],
            "base_count": BASE_ROWS,
            "query_count": QUERY_ROWS,
            "overlap": 0,
            "rationale": "deterministic disjoint contiguous ranges; final rows separate queries from the base boundary",
        },
        "metric": "squared_l2",
        "normalized": False,
        "groundtruth": {"exact": True, "k": GROUNDTRUTH_K, "base_id_origin": 0},
        "files": {
            name: {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in files.items()
        },
    }
    manifest_path = out_dir / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=(
            "extract",
            "vectors",
            "convert-gt",
            "fixed-candidates",
            "manifest",
            "prepare",
        ),
    )
    parser.add_argument("--archive", type=Path, default=Path("downloads/uci_sift10m/sift10m.zip"))
    parser.add_argument(
        "--mat", type=Path, default=Path("downloads/uci_sift10m/SIFT10Mfeatures.mat")
    )
    parser.add_argument("--out-dir", type=Path, default=Path("data/sift10m"))
    parser.add_argument(
        "--diskann-gt", type=Path, default=Path("work/uci_sift10m/exact_l2_top1000.bin")
    )
    parser.add_argument("--candidate-root", type=Path, default=Path("work"))
    parser.add_argument("--chunk-rows", type=int, default=65_536)
    args = parser.parse_args()

    if args.phase in ("extract", "prepare"):
        extract_feature_mat(args.archive, args.mat)
    if args.phase in ("vectors", "prepare"):
        convert_vectors(args.mat, args.out_dir, args.chunk_rows)
    if args.phase == "convert-gt":
        convert_diskann_groundtruth(
            args.diskann_gt, args.out_dir / "sift10m_groundtruth.ivecs"
        )
    if args.phase == "fixed-candidates":
        write_fixed_candidates(args.out_dir, args.candidate_root)
    if args.phase == "manifest":
        write_manifest(args.archive, args.mat, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
