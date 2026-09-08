#!/usr/bin/env python3
"""Prepare OpenSearch Benchmark official Cohere-10M HDF5 for this repository.

Source:
  https://dbyiw3u3rf9yr.cloudfront.net/corpora/vectorsearch/
  cohere-wikipedia-22-12-en-embeddings/documents-10m.hdf5.bz2

The HDF5 contains:
  train     -> base vectors
  test      -> query vectors
  neighbors -> ground-truth IDs
"""

from __future__ import annotations

import argparse
import bz2
import shutil
import urllib.request
from pathlib import Path

import h5py
import numpy as np


URL = (
    "https://dbyiw3u3rf9yr.cloudfront.net/corpora/vectorsearch/"
    "cohere-wikipedia-22-12-en-embeddings/documents-10m.hdf5.bz2"
)


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        print(f"cached: {path}")
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with urllib.request.urlopen(url) as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out)
    tmp.rename(path)


def decompress_bz2(src_path: Path, dst_path: Path) -> None:
    if dst_path.is_file():
        print(f"cached: {dst_path}")
        return
    tmp = dst_path.with_suffix(dst_path.suffix + ".tmp")
    with bz2.open(src_path, "rb") as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out)
    tmp.rename(dst_path)


def write_fvecs_dataset(ds, out_path: Path, chunk_rows: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n, dim = ds.shape
    with out_path.open("wb") as out:
        for start in range(0, n, chunk_rows):
            end = min(start + chunk_rows, n)
            vecs = np.asarray(ds[start:end], dtype=np.float32)
            rows = np.empty((end - start, dim + 1), dtype=np.float32)
            rows[:, 0].view(np.int32)[:] = dim
            rows[:, 1:] = vecs
            rows.tofile(out)
            if end == n or end % 1_000_000 == 0:
                print(f"{out_path.name}: {end}/{n}")


def write_ivecs_dataset(ds, out_path: Path, chunk_rows: int) -> None:
    n, k = ds.shape
    with out_path.open("wb") as out:
        for start in range(0, n, chunk_rows):
            end = min(start + chunk_rows, n)
            ids = np.asarray(ds[start:end], dtype=np.int32)
            rows = np.empty((end - start, k + 1), dtype=np.int32)
            rows[:, 0] = k
            rows[:, 1:] = ids
            rows.tofile(out)
            if end == n or end % 1_000_000 == 0:
                print(f"{out_path.name}: {end}/{n}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="data/cohere10m")
    parser.add_argument("--cache-dir", default="downloads/cohere10m_official")
    parser.add_argument("--hdf5-bz2", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    cache_dir = Path(args.cache_dir)
    archive = args.hdf5_bz2 or cache_dir / "documents-10m.hdf5.bz2"
    hdf5_path = cache_dir / "documents-10m.hdf5"

    if args.hdf5_bz2 is None:
        download(URL, archive)
    decompress_bz2(archive, hdf5_path)

    with h5py.File(hdf5_path, "r") as h5:
        write_fvecs_dataset(h5["train"], out_dir / "cohere10m_base.fvecs", args.chunk_rows)
        write_fvecs_dataset(h5["test"], out_dir / "cohere10m_query.fvecs", args.chunk_rows)
        write_ivecs_dataset(
            h5["neighbors"], out_dir / "cohere10m_groundtruth.ivecs", args.chunk_rows
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
