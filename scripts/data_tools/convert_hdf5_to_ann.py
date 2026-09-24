#!/usr/bin/env python3
"""Convert ANN-Benchmarks/VIBE HDF5 train/test/neighbors to fvecs/ivecs."""

import argparse
import os

import h5py
import numpy as np


def write_fvecs_h5(dataset, output_path, chunk_size=4096):
    """Write an HDF5 2-D dataset to fvecs without loading it all into RAM."""
    if len(dataset.shape) != 2:
        raise ValueError(f"Expected a 2-D dataset, got shape={dataset.shape}")

    n, d = dataset.shape
    print(f"Writing {output_path}")
    print(f"  vectors={n}, dim={d}, output_dtype=float32")

    with open(output_path, "wb") as f:
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)

            vectors = np.asarray(dataset[start:end], dtype=np.float32)
            rows = np.empty((end - start, d + 1), dtype=np.float32)

            # fvecs format:
            # [int32 dimension][float32 x_0]...[float32 x_{d-1}]
            rows[:, 0].view(np.int32)[:] = d
            rows[:, 1:] = vectors
            rows.tofile(f)

            if end == n or end % 100000 < chunk_size:
                print(f"  {end}/{n}")

    expected_size = n * (d + 1) * 4
    actual_size = os.path.getsize(output_path)
    if actual_size != expected_size:
        raise RuntimeError(
            f"Unexpected fvecs size: expected={expected_size}, actual={actual_size}"
        )


def write_ivecs_h5(dataset, output_path, chunk_size=4096):
    """Write an HDF5 2-D integer dataset to ivecs without loading it all into RAM."""
    if len(dataset.shape) != 2:
        raise ValueError(f"Expected a 2-D dataset, got shape={dataset.shape}")

    n, k = dataset.shape
    print(f"Writing {output_path}")
    print(f"  queries={n}, neighbors_per_query={k}, output_dtype=int32")

    with open(output_path, "wb") as f:
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)

            neighbors = np.asarray(dataset[start:end], dtype=np.int32)
            rows = np.empty((end - start, k + 1), dtype=np.int32)

            # ivecs format:
            # [int32 count][int32 id_0]...[int32 id_{k-1}]
            rows[:, 0] = k
            rows[:, 1:] = neighbors
            rows.tofile(f)

            if end == n or end % 10000 < chunk_size:
                print(f"  {end}/{n}")

    expected_size = n * (k + 1) * 4
    actual_size = os.path.getsize(output_path)
    if actual_size != expected_size:
        raise RuntimeError(
            f"Unexpected ivecs size: expected={expected_size}, actual={actual_size}"
        )


def inspect_hdf5(h5):
    print("HDF5 datasets:")
    for key in h5.keys():
        obj = h5[key]
        if isinstance(obj, h5py.Dataset):
            print(f"  {key}: shape={obj.shape}, dtype={obj.dtype}")
        else:
            print(f"  {key}: {type(obj).__name__}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input HDF5 file",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for converted files",
    )
    parser.add_argument(
        "--prefix",
        default="agnews",
        help="Output filename prefix",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=4096,
        help="Number of rows processed per chunk",
    )
    args = parser.parse_args()

    if args.chunk_size <= 0:
        parser.error("--chunk-size must be positive")

    if not os.path.isfile(args.input):
        raise FileNotFoundError(f"Input file does not exist: {args.input}")

    os.makedirs(args.output_dir, exist_ok=True)

    base_path = os.path.join(args.output_dir, f"{args.prefix}_base.fvecs")
    query_path = os.path.join(args.output_dir, f"{args.prefix}_query.fvecs")
    gt_path = os.path.join(args.output_dir, f"{args.prefix}_groundtruth.ivecs")

    with h5py.File(args.input, "r") as h5:
        inspect_hdf5(h5)

        required = ["train", "test", "neighbors"]
        missing = [key for key in required if key not in h5]
        if missing:
            raise KeyError(
                f"Missing required HDF5 dataset(s): {missing}. "
                f"Available keys: {list(h5.keys())}"
            )

        train = h5["train"]
        test = h5["test"]
        neighbors = h5["neighbors"]

        if train.shape[1] != test.shape[1]:
            raise ValueError(
                f"Base/query dimension mismatch: "
                f"train={train.shape}, test={test.shape}"
            )

        print()
        print("Conversion mapping:")
        print(f"  train     -> {base_path}")
        print(f"  test      -> {query_path}")
        print(f"  neighbors -> {gt_path}")
        print()

        write_fvecs_h5(train, base_path, args.chunk_size)
        write_fvecs_h5(test, query_path, args.chunk_size)
        write_ivecs_h5(neighbors, gt_path, args.chunk_size)

    print()
    print("Conversion completed.")
    print(f"Base:        {base_path}")
    print(f"Query:       {query_path}")
    print(f"Groundtruth: {gt_path}")


if __name__ == "__main__":
    main()
