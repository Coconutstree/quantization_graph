#!/usr/bin/env python3
"""Convert SAQ test_qps CSV to the 01 Recall-QPS schema."""

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
    "k",
    "nominal_bpd",
    "actual_bytes_per_vector",
    "index_size_mb",
    "peak_rss_mb",
    "build_time_ms",
    "train_time_ms",
    "encode_time_ms",
    "search_param_name",
    "search_param_value",
    "recall",
    "qps",
    "latency_mean_us",
    "latency_p50_us",
    "latency_p95_us",
    "distance_calls",
    "threads",
    "repeat_id",
    "git_commit",
    "native_ratio",
    "native_bw_mbps",
    "native_compute_kopps",
    "native_csv",
]


def find_native_csv(args: argparse.Namespace) -> Path:
    if args.native_csv:
        return Path(args.native_csv)
    pattern = (
        f"qps_{args.dataset}_ivf{args.K}_*b{args.B:g}*"
        f"_th{args.fix_thread}_np{args.fix_nprobe}"
        f"_sm{args.searcher_vars_bound_m:g}.csv"
    )
    candidates = [Path(p) for p in glob.glob(str(Path(args.native_root) / pattern))]
    if not candidates:
        raise FileNotFoundError(f"no SAQ QPS CSV found under {args.native_root} with {pattern}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def ms_to_us(value: str) -> str:
    if value == "":
        return ""
    return str(float(value) * 1000.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--native-root", default="baselines/saq/results/saq")
    parser.add_argument("--native-csv")
    parser.add_argument("--out", required=True)
    parser.add_argument("--method", default="SAQ_B4")
    parser.add_argument("--metric", default="L2")
    parser.add_argument("--k", default="100")
    parser.add_argument("--B", type=float, default=4.0)
    parser.add_argument("--K", type=int, default=4096)
    parser.add_argument("--fix-thread", default="1")
    parser.add_argument("--fix-nprobe", default="0")
    parser.add_argument("--searcher-vars-bound-m", type=float, default=4.0)
    parser.add_argument("--actual-bytes-per-vector", default="")
    parser.add_argument("--index-size-mb", default="")
    parser.add_argument("--peak-rss-mb", default="")
    parser.add_argument("--build-time-ms", default="")
    parser.add_argument("--train-time-ms", default="")
    parser.add_argument("--encode-time-ms", default="")
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
                    "k": args.k,
                    "nominal_bpd": f"{args.B:g}",
                    "actual_bytes_per_vector": args.actual_bytes_per_vector,
                    "index_size_mb": args.index_size_mb,
                    "peak_rss_mb": args.peak_rss_mb,
                    "build_time_ms": args.build_time_ms,
                    "train_time_ms": args.train_time_ms,
                    "encode_time_ms": args.encode_time_ms,
                    "search_param_name": "nprobe",
                    "search_param_value": row.get("nprobe", ""),
                    "recall": row.get("recall", ""),
                    "qps": row.get("QPS", ""),
                    "latency_mean_us": ms_to_us(row.get("avg_tm_ms", "")),
                    "latency_p50_us": "",
                    "latency_p95_us": "",
                    "distance_calls": "",
                    "threads": row.get("num_threads", args.fix_thread),
                    "repeat_id": args.repeat_id,
                    "git_commit": args.git_commit,
                    "native_ratio": row.get("ratio", ""),
                    "native_bw_mbps": row.get("bw_mbps", ""),
                    "native_compute_kopps": row.get("compute_kopps", ""),
                    "native_csv": str(native_csv),
                }
            )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
