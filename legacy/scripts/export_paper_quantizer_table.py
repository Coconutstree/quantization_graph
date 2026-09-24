#!/usr/bin/env python3
"""Export a paper-friendly quantizer comparison table from per-dataset summaries."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_DATASETS = ("dbpedia", "gist", "agnews")

OUT_COLUMNS = [
    "dataset",
    "method",
    "recall_at_10",
    "qps",
    "latency_mean_us",
    "latency_p50_us",
    "latency_p95_us",
    "top10_overlap",
    "pairwise_flip_rate_top100",
    "p95_relative_error",
    "mean_absolute_error",
    "nominal_bpd",
    "code_bytes_per_vector",
    "index_size_mb",
    "peak_rss_mb",
    "query_count",
    "candidate_size",
    "unique_candidate_count",
    "distance_calls",
    "train_time_ms",
    "encode_time_ms",
    "distance_time_ms",
    "candidate_strategy",
    "candidate_fingerprint",
    "fallback_random_count",
    "repeat_id",
    "source_summary_csv",
]


COLUMN_DESCRIPTIONS = {
    "dataset": "Dataset name.",
    "method": "Quantizer implementation being measured.",
    "recall_at_10": "Recall@10 inside the fixed candidate set: overlap between compressed-distance top10 and exact-L2 top10.",
    "qps": "Queries per second for compressed-distance evaluation over the fixed candidates only.",
    "latency_mean_us": "Mean per-query compressed-distance latency in microseconds.",
    "latency_p50_us": "P50 per-query compressed-distance latency in microseconds.",
    "latency_p95_us": "P95 per-query compressed-distance latency in microseconds.",
    "top10_overlap": "Set overlap of compressed top10 and exact top10; same definition as recall_at_10 here.",
    "pairwise_flip_rate_top100": "Fraction of pairwise order inversions among the exact top100 candidates; lower is better.",
    "p95_relative_error": "P95 of abs(compressed_l2 - exact_l2) / exact_l2. Prefer this over mean_relative_error.",
    "mean_absolute_error": "Mean absolute L2 distance error.",
    "nominal_bpd": "Nominal bits per dimension target.",
    "code_bytes_per_vector": "Compressed code bytes per vector reported by the implementation.",
    "index_size_mb": "Full quantizer index size in MiB (codes over the full base set + codebook/auxiliary + residual payload where applicable), computed from the method's own storage breakdown.",
    "peak_rss_mb": "Measured process peak RSS in MiB during the fixed-candidate run (VmHWM).",
    "query_count": "Number of query vectors evaluated.",
    "candidate_size": "Number of fixed candidate IDs per query.",
    "unique_candidate_count": "Number of unique base IDs appearing across all fixed candidates.",
    "distance_calls": "query_count * candidate_size.",
    "train_time_ms": "Quantizer training time in milliseconds.",
    "encode_time_ms": "Time to encode unique candidates in milliseconds.",
    "distance_time_ms": "Total compressed-distance evaluation time in milliseconds.",
    "candidate_strategy": "How the fixed candidate set was generated.",
    "candidate_fingerprint": "FNV-1a fingerprint of the candidate binary.",
    "fallback_random_count": "Number of fallback random candidates; should be 0 for the formal hard-negative run.",
    "repeat_id": "Repeat/run id.",
    "source_summary_csv": "Input summary CSV path used for this row.",
}


def read_candidate_meta(work_root: Path, dataset: str, candidate_size: str) -> dict[str, str]:
    meta_path = (
        work_root
        / "01_quantizer_fair"
        / dataset
        / f"fixed_candidates_k{candidate_size}.meta.json"
    )
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text())
    return {
        "candidate_strategy": str(meta.get("strategy", "")),
        "candidate_fingerprint": str(meta.get("fingerprint_fnv1a64", "")),
        "fallback_random_count": str(meta.get("fallback_random_count", "")),
    }


def normalize_row(row: dict[str, str], source: Path, work_root: Path) -> dict[str, str]:
    candidate_size = row.get("candidate_size", "")
    meta = read_candidate_meta(work_root, row.get("dataset", ""), candidate_size)
    out = {
        "dataset": row.get("dataset", ""),
        "method": row.get("method", ""),
        "recall_at_10": row.get("recall", ""),
        "qps": row.get("qps", ""),
        "latency_mean_us": row.get("latency_mean_us", ""),
        "latency_p50_us": row.get("latency_p50_us", ""),
        "latency_p95_us": row.get("latency_p95_us", ""),
        "top10_overlap": row.get("top10_overlap", ""),
        "pairwise_flip_rate_top100": row.get("pairwise_flip_rate_top100", ""),
        "p95_relative_error": row.get("p95_relative_error", ""),
        "mean_absolute_error": row.get("mean_absolute_error", ""),
        "nominal_bpd": row.get("nominal_bpd", ""),
        "code_bytes_per_vector": row.get("actual_bytes_per_vector", ""),
        "index_size_mb": row.get("index_size_mb", ""),
        "peak_rss_mb": row.get("peak_rss_mb", ""),
        "query_count": row.get("query_count", ""),
        "candidate_size": candidate_size,
        "unique_candidate_count": row.get("unique_candidate_count", ""),
        "distance_calls": row.get("distance_calls", ""),
        "train_time_ms": row.get("train_time_ms", ""),
        "encode_time_ms": row.get("encode_time_ms", ""),
        "distance_time_ms": row.get("distance_time_ms", ""),
        "candidate_strategy": meta.get("candidate_strategy", ""),
        "candidate_fingerprint": meta.get("candidate_fingerprint", ""),
        "fallback_random_count": meta.get("fallback_random_count", ""),
        "repeat_id": row.get("repeat_id", ""),
        "source_summary_csv": str(source),
    }
    return out


def write_data_dictionary(path: Path) -> None:
    lines = ["# 01 Quantizer Fair CSV Columns", ""]
    for column in OUT_COLUMNS:
        lines.append(f"- `{column}`: {COLUMN_DESCRIPTIONS[column]}")
    lines.append("")
    lines.append(
        "`mean_relative_error` is intentionally omitted from the paper table because "
        "near-zero exact distances make it unstable on GIST/AGNEWS. Use "
        "`p95_relative_error`, `mean_absolute_error`, and ranking metrics instead."
    )
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", default=list(DEFAULT_DATASETS))
    parser.add_argument("--out-root", default="results/archive/legacy_layout_20260918/disk_environment")
    parser.add_argument("--work-root", default="work")
    parser.add_argument(
        "--out",
        default="results/archive/legacy_layout_20260918/disk_environment/paper_tables/01_quantizer_fair_summary.csv",
    )
    parser.add_argument(
        "--data-dictionary",
        default="results/archive/legacy_layout_20260918/disk_environment/paper_tables/01_quantizer_fair_columns.md",
    )
    args = parser.parse_args()

    out_root = Path(args.out_root)
    work_root = Path(args.work_root)
    rows: list[dict[str, str]] = []
    for dataset in args.datasets:
        source = (
            out_root
            / "01_quantizer_fair"
            / dataset
            / "csv"
            / "faiss_quantizer_summary.csv"
        )
        if not source.exists() or source.stat().st_size == 0:
            print(f"skip missing/empty {source}")
            continue
        with source.open(newline="") as f:
            for row in csv.DictReader(f):
                rows.append(normalize_row(row, source, work_root))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    write_data_dictionary(Path(args.data_dictionary))
    print(f"wrote {out_path} rows={len(rows)}")
    print(f"wrote {args.data_dictionary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
