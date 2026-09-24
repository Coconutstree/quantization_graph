#!/usr/bin/env python3
"""Convert SAQ test_relative_error CSV to the 01 accuracy schema."""

from __future__ import annotations

import argparse
import csv
import glob
from pathlib import Path


FIELDNAMES = [
    "suite",
    "dataset",
    "method",
    "metric",
    "nominal_bpd",
    "actual_bytes_per_vector",
    "index_size_mb",
    "train_time_ms",
    "encode_time_ms",
    "mean_relative_error",
    "p95_relative_error",
    "mean_ip_relative_error",
    "p95_ip_relative_error",
    "mean_absolute_error",
    "top10_overlap",
    "pairwise_flip_rate",
    "fixed_candidate_recall",
    "query_count",
    "candidate_size",
    "threads",
    "repeat_id",
    "git_commit",
    "native_search_param_name",
    "native_search_param_value",
    "native_csv",
]


def find_native_csv(args: argparse.Namespace) -> Path:
    if args.native_csv:
        return Path(args.native_csv)
    pattern = (
        f"{args.dataset}_ivf{args.K}_*b{args.B:g}*"
        f"_sm{args.searcher_vars_bound_m:g}.csv"
    )
    candidates = [
        Path(p)
        for p in glob.glob(str(Path(args.native_root) / pattern))
        if not Path(p).name.startswith("qps_")
    ]
    if not candidates:
        raise FileNotFoundError(
            f"no SAQ relative-error CSV found under {args.native_root} with {pattern}"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--native-root", default="baselines/saq/results/saq")
    parser.add_argument("--native-csv")
    parser.add_argument("--out", required=True)
    parser.add_argument("--method", default="SAQ_B4")
    parser.add_argument("--metric", default="L2")
    parser.add_argument("--B", type=float, default=4.0)
    parser.add_argument("--K", type=int, default=4096)
    parser.add_argument("--searcher-vars-bound-m", type=float, default=4.0)
    parser.add_argument("--actual-bytes-per-vector", default="")
    parser.add_argument("--index-size-mb", default="")
    parser.add_argument("--train-time-ms", default="")
    parser.add_argument("--encode-time-ms", default="")
    parser.add_argument("--query-count", default="")
    parser.add_argument("--candidate-size", default="")
    parser.add_argument("--threads", default="")
    parser.add_argument("--repeat-id", default="0")
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    native_csv = find_native_csv(args)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with native_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "suite": "01_quantizer_fair",
                    "dataset": args.dataset,
                    "method": args.method,
                    "metric": args.metric,
                    "nominal_bpd": f"{args.B:g}",
                    "actual_bytes_per_vector": args.actual_bytes_per_vector,
                    "index_size_mb": args.index_size_mb,
                    "train_time_ms": args.train_time_ms,
                    "encode_time_ms": args.encode_time_ms,
                    "mean_relative_error": row.get("err_tot_avg", ""),
                    "p95_relative_error": "",
                    # Official SAQ test_n only reports L2 error; IP columns stay
                    # empty until an official IP-error source exists.
                    "mean_ip_relative_error": "",
                    "p95_ip_relative_error": "",
                    "mean_absolute_error": "",
                    "top10_overlap": "",
                    "pairwise_flip_rate": "",
                    "fixed_candidate_recall": "",
                    "query_count": args.query_count,
                    "candidate_size": args.candidate_size,
                    "threads": args.threads,
                    "repeat_id": args.repeat_id,
                    "git_commit": args.git_commit,
                    "native_search_param_name": "nprobe",
                    "native_search_param_value": row.get("nprobe", ""),
                    "native_csv": str(native_csv),
                }
            )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
