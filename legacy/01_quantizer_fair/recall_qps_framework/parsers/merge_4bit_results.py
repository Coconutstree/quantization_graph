#!/usr/bin/env python3
"""Merge 01 accuracy and Recall-QPS CSV files for paper plotting."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


FIELDNAMES = [
    "suite",
    "dataset",
    "method",
    "metric",
    "nominal_bpd",
    "actual_bytes_per_vector",
    "index_size_mb",
    "search_param_name",
    "search_param_value",
    "recall",
    "qps",
    "latency_mean_us",
    "latency_p50_us",
    "latency_p95_us",
    "mean_relative_error",
    "p95_relative_error",
    "mean_absolute_error",
    "top10_overlap",
    "pairwise_flip_rate",
    "fixed_candidate_recall",
    "train_time_ms",
    "encode_time_ms",
    "build_time_ms",
    "threads",
    "repeat_id",
    "git_commit",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def first_nonempty(rows: list[dict[str, str]], key: str) -> str:
    for row in rows:
        value = row.get(key, "")
        if value not in {"", None}:
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--accuracy-csv", required=True)
    parser.add_argument("--recall-qps-csv", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    accuracy_rows = read_csv(Path(args.accuracy_csv))
    recall_rows = read_csv(Path(args.recall_qps_csv))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in recall_rows:
            related_accuracy = [
                ar
                for ar in accuracy_rows
                if ar.get("native_search_param_value", ar.get("search_param_value", ""))
                == row.get("search_param_value", "")
            ]
            fallback_accuracy = related_accuracy or accuracy_rows
            writer.writerow(
                {
                    "suite": "01_quantizer_fair",
                    "dataset": args.dataset,
                    "method": args.method,
                    "metric": row.get("metric", first_nonempty(fallback_accuracy, "metric")),
                    "nominal_bpd": row.get(
                        "nominal_bpd", first_nonempty(fallback_accuracy, "nominal_bpd")
                    ),
                    "actual_bytes_per_vector": row.get(
                        "actual_bytes_per_vector",
                        first_nonempty(fallback_accuracy, "actual_bytes_per_vector"),
                    ),
                    "index_size_mb": row.get(
                        "index_size_mb", first_nonempty(fallback_accuracy, "index_size_mb")
                    ),
                    "search_param_name": row.get("search_param_name", ""),
                    "search_param_value": row.get("search_param_value", ""),
                    "recall": row.get("recall", ""),
                    "qps": row.get("qps", ""),
                    "latency_mean_us": row.get("latency_mean_us", ""),
                    "latency_p50_us": row.get("latency_p50_us", ""),
                    "latency_p95_us": row.get("latency_p95_us", ""),
                    "mean_relative_error": first_nonempty(
                        fallback_accuracy, "mean_relative_error"
                    ),
                    "p95_relative_error": first_nonempty(
                        fallback_accuracy, "p95_relative_error"
                    ),
                    "mean_absolute_error": first_nonempty(
                        fallback_accuracy, "mean_absolute_error"
                    ),
                    "top10_overlap": first_nonempty(fallback_accuracy, "top10_overlap"),
                    "pairwise_flip_rate": first_nonempty(
                        fallback_accuracy, "pairwise_flip_rate"
                    ),
                    "fixed_candidate_recall": first_nonempty(
                        fallback_accuracy, "fixed_candidate_recall"
                    ),
                    "train_time_ms": first_nonempty(fallback_accuracy, "train_time_ms"),
                    "encode_time_ms": first_nonempty(fallback_accuracy, "encode_time_ms"),
                    "build_time_ms": row.get("build_time_ms", ""),
                    "threads": row.get("threads", first_nonempty(fallback_accuracy, "threads")),
                    "repeat_id": row.get(
                        "repeat_id", first_nonempty(fallback_accuracy, "repeat_id")
                    ),
                    "git_commit": row.get(
                        "git_commit", first_nonempty(fallback_accuracy, "git_commit")
                    ),
                }
            )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
