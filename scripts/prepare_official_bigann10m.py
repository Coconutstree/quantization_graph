#!/usr/bin/env python3
"""Prepare official BIGANN/SIFT1B first-10M files for this repository.

Source files:
  ftp://ftp.irisa.fr/local/texmex/corpus/bigann_base.bvecs.gz
  ftp://ftp.irisa.fr/local/texmex/corpus/bigann_query.bvecs.gz
  ftp://ftp.irisa.fr/local/texmex/corpus/bigann_gnd.tar.gz

The output contract is:
  data/bigann10m/bigann10m_base.fvecs
  data/bigann10m/bigann10m_query.fvecs
  data/bigann10m/bigann10m_groundtruth.ivecs
"""

from __future__ import annotations

import argparse
import gzip
import io
import shutil
import struct
import socket
import tarfile
import urllib.request
from pathlib import Path

import numpy as np


BASE_URL = "ftp://ftp.irisa.fr/local/texmex/corpus"
BASE_GZ = f"{BASE_URL}/bigann_base.bvecs.gz"
QUERY_GZ = f"{BASE_URL}/bigann_query.bvecs.gz"
GND_TAR_GZ = f"{BASE_URL}/bigann_gnd.tar.gz"


def open_local_or_url(path: Path | None, url: str):
    if path is not None and path.is_file():
        return path.open("rb")
    return urllib.request.urlopen(url, timeout=60)


def fvecs_shape(path: Path) -> tuple[int, int] | None:
    if not path.is_file() or path.stat().st_size < 4:
        return None
    with path.open("rb") as f:
        dim = struct.unpack("<i", f.read(4))[0]
    if dim <= 0:
        return None
    row_size = 4 + dim * 4
    size = path.stat().st_size
    if size % row_size != 0:
        return None
    return size // row_size, dim


def write_bvecs_as_fvecs(src, out_path: Path, max_rows: int | None) -> tuple[int, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    dim: int | None = None
    with gzip.GzipFile(fileobj=src, mode="rb") as gz, out_path.open("wb") as out:
        while max_rows is None or rows < max_rows:
            header = gz.read(4)
            if not header:
                break
            if len(header) != 4:
                raise RuntimeError("truncated bvecs dimension header")
            row_dim = struct.unpack("<i", header)[0]
            if dim is None:
                dim = row_dim
            elif dim != row_dim:
                raise RuntimeError(f"dimension changed from {dim} to {row_dim}")
            payload = gz.read(row_dim)
            if len(payload) != row_dim:
                raise RuntimeError("truncated bvecs payload")
            vec = np.frombuffer(payload, dtype=np.uint8).astype(np.float32)
            out.write(struct.pack("<i", row_dim))
            out.write(vec.tobytes(order="C"))
            rows += 1
            if rows % 1_000_000 == 0:
                print(f"{out_path.name}: {rows} rows")
    if dim is None:
        raise RuntimeError("no vectors were read")
    return rows, dim


def extract_idx_10m(src, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=src, mode="r:gz") as tar:
        member = next((m for m in tar.getmembers() if m.name.endswith("idx_10M.ivecs")), None)
        if member is None:
            raise RuntimeError("idx_10M.ivecs not found in BIGANN ground-truth archive")
        extracted = tar.extractfile(member)
        if extracted is None:
            raise RuntimeError("could not extract idx_10M.ivecs")
        with out_path.open("wb") as out:
            shutil.copyfileobj(extracted, out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="data/bigann10m")
    parser.add_argument("--cache-dir", default="downloads/bigann_official")
    parser.add_argument("--base-gz", type=Path)
    parser.add_argument("--query-gz", type=Path)
    parser.add_argument("--gnd-tar-gz", type=Path)
    parser.add_argument("--base-rows", type=int, default=10_000_000)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    base_path = out_dir / "bigann10m_base.fvecs"
    query_path = out_dir / "bigann10m_query.fvecs"
    gt_path = out_dir / "bigann10m_groundtruth.ivecs"

    if fvecs_shape(base_path) == (args.base_rows, 128):
        print(f"base: cached rows={args.base_rows} dim=128 path={base_path}")
    else:
        try:
            with open_local_or_url(args.base_gz, BASE_GZ) as src:
                rows, dim = write_bvecs_as_fvecs(src, base_path, args.base_rows)
                print(f"base: rows={rows} dim={dim} path={base_path}")
        except socket.timeout as exc:
            raise RuntimeError("timed out while reading BIGANN base stream") from exc

    if fvecs_shape(query_path) == (10_000, 128):
        print(f"query: cached rows=10000 dim=128 path={query_path}")
    else:
        with open_local_or_url(args.query_gz, QUERY_GZ) as src:
            rows, dim = write_bvecs_as_fvecs(src, query_path, None)
            print(f"query: rows={rows} dim={dim} path={query_path}")

    with open_local_or_url(args.gnd_tar_gz, GND_TAR_GZ) as src:
        # tarfile wants seekable behavior less than gzip conversion does, so buffer
        # the 512 MiB archive when it is streamed from FTP.
        if not hasattr(src, "seekable") or not src.seekable():
            blob = io.BytesIO(src.read())
            src = blob
        extract_idx_10m(src, gt_path)
        print(f"groundtruth: path={gt_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
