#!/usr/bin/env python3
"""Run the unified Ours method (Ours-DiskANN) for one dataset and degrees.

The paper's method is Ours-DiskANN: ExRaBitQ4 4-bit symmetric Vamana
construction inside the DiskANN3 framework, paper-pruned search, residual4
rerank. It is measured once per dataset at M=32 and M=64 (degree-matched
comparison in experiment 02 and the full-system configuration in experiment
03). This entry invokes the repository-root Ours-DiskANN binary with the
shared 03 test-subset query/GT files and archives the raw logs under
Ours/logs/.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _fvec_count(path: Path) -> int:
    import struct

    with path.open("rb") as f:
        dim = struct.unpack("<i", f.read(4))[0]
    return path.stat().st_size // (4 * (dim + 1))


def prepare_query_splits(
    dataset: str, data_root: Path, out_root: Path, val_queries: int
) -> tuple[Path, Path, Path, Path]:
    """Reuse the 03 shared split (first N queries = validation, rest = test)."""
    split_dir = out_root / dataset / "csv" / "03_system_fair" / "_query_splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    test_gt = split_dir / "test_gt.ivecs"
    if test_gt.exists():
        return (
            split_dir / "validation_query.fvecs",
            split_dir / "validation_gt.ivecs",
            split_dir / "test_query.fvecs",
            test_gt,
        )
    import numpy as np

    def read_fvecs(path: Path):
        with path.open("rb") as f:
            dim = int(np.fromfile(f, dtype=np.int32, count=1)[0])
            raw = np.fromfile(f, dtype=np.float32)
        n = (raw.size + 1) // (dim + 1)
        mask = np.ones(raw.size, dtype=bool)
        if n > 1:
            mask[np.arange(1, n) * (dim + 1) - 1] = False
        return raw[mask].reshape(n, dim)

    def read_ivecs(path: Path):
        rows = []
        with path.open("rb") as f:
            while True:
                head = f.read(4)
                if not head:
                    break
                width = int(np.frombuffer(head, dtype=np.int32)[0])
                rows.append(
                    np.frombuffer(f.read(width * 4), dtype=np.int32).astype(np.int64)
                )
        return np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.int64)

    def write_fvecs(path: Path, arr):
        arr = np.ascontiguousarray(arr, dtype=np.float32)
        with path.open("wb") as f:
            for row in arr:
                f.write(np.int32(arr.shape[1]).tobytes())
                f.write(row.tobytes())

    def write_ivecs(path: Path, arr):
        arr = np.ascontiguousarray(arr, dtype=np.int32)
        with path.open("wb") as f:
            for row in arr:
                f.write(np.int32(row.size).tobytes())
                f.write(row.tobytes())

    queries = read_fvecs(data_root / dataset / f"{dataset}_query.fvecs")
    gt = read_ivecs(data_root / dataset / f"{dataset}_groundtruth.ivecs")
    n_val = min(val_queries, len(queries))
    write_fvecs(split_dir / "validation_query.fvecs", queries[:n_val])
    write_ivecs(split_dir / "validation_gt.ivecs", gt[:n_val])
    write_fvecs(split_dir / "test_query.fvecs", queries[n_val:])
    write_ivecs(test_gt, gt[n_val:])
    return (
        split_dir / "validation_query.fvecs",
        split_dir / "validation_gt.ivecs",
        split_dir / "test_query.fvecs",
        test_gt,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument(
        "--M",
        default="32,64",
        help="comma-separated max-degree values to build (default: 32,64)",
    )
    ap.add_argument(
        "--centroid-count",
        type=int,
        default=1,
        help="ExRaBitQ centroid count K (unified default K=1; K>1 reserved "
        "for future multi-centroid symmetric Vamana work)",
    )
    ap.add_argument(
        "--centroid-train-samples",
        type=int,
        default=100000,
        help="training sample size used when K>1 becomes available",
    )
    ap.add_argument("--data-root", default=str(_REPO / "data"))
    ap.add_argument("--out-root", default=str(_REPO / "results"))
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--val-queries", type=int, default=1000)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    degrees = [int(v.strip()) for v in args.M.split(",") if v.strip()]
    if not degrees:
        ap.error("--M must contain at least one degree")
    for degree in degrees:
        if degree not in (32, 64):
            ap.error("--M values must be 32 or 64")
    if args.centroid_count != 1:
        raise SystemExit(
            "K>1 ExRaBitQ is not enabled for the unified Ours pipeline; "
            "the formal configuration is K=1 (02/03 symmetric Vamana and "
            "01 quantizer comparison share the same K)."
        )

    _, _, test_q, test_gt = prepare_query_splits(
        args.dataset, Path(args.data_root), Path(args.out_root), args.val_queries
    )
    binary = (
        _REPO / "experiments" / "02_diskann_fair"
        / "target" / "release" / "run_diskann_fair"
    )
    if not binary.exists():
        raise SystemExit(f"missing binary: {binary}")
    for degree in degrees:
        cmd = [
            str(binary),
            "--dataset", args.dataset,
            "--methods", "Ours",
            "--max-degree", str(degree),
            "--repeats", str(args.repeats),
            "--threads", str(args.threads),
            "--query-path", str(test_q),
            "--gt-path", str(test_gt),
        ]
        print("$ " + " ".join(cmd))
        rc = subprocess.run(cmd, cwd=_REPO).returncode
        if rc != 0:
            return rc
        raw = (
            Path(args.out_root) / args.dataset / "raw" / "02_diskann_fair" / "Ours"
            / f"{args.dataset}_Ours_R{degree}_Lbuild400.log"
        )
        if raw.exists():
            dst = _REPO / "Ours" / "logs" / args.dataset
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw, dst / raw.name)
            print(f"log archived to {dst / raw.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
