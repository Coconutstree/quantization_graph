#!/usr/bin/env python3
"""Aggregate the controlled GIST1M query-codec search sweeps.

The two inputs must contain the same deterministic search statistics and may
differ only in timings.  The output keeps both the mean and the observed range
for timing fields; with two runs, the range is evidence of run-order noise, not
a confidence interval.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


CODECS = ("full", "b1", "int4", "int8")
EFS = (20, 40, 60, 80, 100, 200, 300, 400, 500)
DETERMINISTIC_FIELDS = (
    "recall",
    "visited_nodes",
    "distance_calls",
    "paper_checked",
    "paper_would_prune",
    "paper_msb_kernel_calls",
    "paper_remaining_kernel_calls",
    "payload_bytes_read",
    "avg_bits_read",
)
TIMING_FIELDS = (
    "qps",
    "latency_mean_us",
    "latency_p95_us",
    "prepare_us",
    "traverse_us",
    "rerank_us",
    "neighbor_fetch_us",
    "visited_mark_us",
    "paper_batch_us",
    "flush_us",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="LABEL=CSV",
        help="Labeled raw benchmark CSV; specify at least twice",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def load_run(spec: str) -> tuple[str, Path, list[dict[str, str]]]:
    if "=" not in spec:
        raise ValueError(f"invalid --run {spec!r}; expected LABEL=CSV")
    label, raw_path = spec.split("=", 1)
    path = Path(raw_path)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"empty input: {path}")
    return label, path, rows


def mean(rows: list[dict[str, str]], field: str) -> float:
    return statistics.fmean(float(row[field]) for row in rows)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    if len(args.run) < 2:
        raise ValueError("at least two --run inputs are required")

    loaded = [load_run(spec) for spec in args.run]
    expected_keys = {(codec, ef) for codec in CODECS for ef in EFS}
    by_key: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
    run_rows: list[dict[str, object]] = []
    source_paths: dict[str, str] = {}

    for label, path, rows in loaded:
        source_paths[label] = str(path)
        keys = {
            (row["query_coarse_codec"], int(float(row["search_param_value"])))
            for row in rows
        }
        if keys != expected_keys or len(rows) != len(expected_keys):
            missing = sorted(expected_keys - keys)
            extra = sorted(keys - expected_keys)
            raise ValueError(
                f"{label}: expected {len(expected_keys)} unique rows; "
                f"got {len(rows)} rows, missing={missing}, extra={extra}"
            )
        for row in rows:
            codec = row["query_coarse_codec"]
            ef = int(float(row["search_param_value"]))
            by_key[(codec, ef)].append(row)
            run_rows.append(
                {
                    "run": label,
                    "codec": codec,
                    "ef_search": ef,
                    "recall_at_10": float(row["recall"]),
                    "qps": float(row["qps"]),
                    "latency_mean_us": float(row["latency_mean_us"]),
                    "gate_us": float(row["paper_batch_us"]),
                    "remaining_us": float(row["flush_us"]),
                    "coarse_kernel_us": float(row["paper_batch_us"])
                    + float(row["flush_us"]),
                    "gate_calls": float(row["paper_msb_kernel_calls"]),
                    "remaining_4bit_calls": float(
                        row["paper_remaining_kernel_calls"]
                    ),
                }
            )

    mismatches: list[str] = []
    aggregate: list[dict[str, object]] = []
    for codec in CODECS:
        for ef in EFS:
            rows = by_key[(codec, ef)]
            for field in DETERMINISTIC_FIELDS:
                values = [float(row[field]) for row in rows]
                if max(values) - min(values) > 1e-9:
                    mismatches.append(
                        f"{codec}/ef={ef}/{field}: "
                        + ", ".join(f"{value:.12g}" for value in values)
                    )

            qps = [float(row["qps"]) for row in rows]
            latency = [float(row["latency_mean_us"]) for row in rows]
            reference = rows[0]
            gate_calls = float(reference["paper_msb_kernel_calls"])
            remaining_calls = float(reference["paper_remaining_kernel_calls"])
            checked = float(reference["paper_checked"])
            pruned = float(reference["paper_would_prune"])
            gate_us = mean(rows, "paper_batch_us")
            flush_us = mean(rows, "flush_us")
            aggregate.append(
                {
                    "codec": codec,
                    "ef_search": ef,
                    "recall_at_10": float(reference["recall"]),
                    "qps_mean": statistics.fmean(qps),
                    "qps_min": min(qps),
                    "qps_max": max(qps),
                    "qps_range_pct": (max(qps) - min(qps))
                    / statistics.fmean(qps),
                    "latency_mean_us": statistics.fmean(latency),
                    "latency_min_us": min(latency),
                    "latency_max_us": max(latency),
                    "prepare_us_mean": mean(rows, "prepare_us"),
                    "traverse_us_mean": mean(rows, "traverse_us"),
                    "rerank_us_mean": mean(rows, "rerank_us"),
                    "gate_us_mean": gate_us,
                    "remaining_us_mean": flush_us,
                    "coarse_kernel_us_mean": gate_us + flush_us,
                    "gate_calls": gate_calls,
                    "remaining_4bit_calls": remaining_calls,
                    "total_distance_stages": gate_calls + remaining_calls,
                    "prune_rate": pruned / checked,
                    "gate_ns_per_candidate": 1000.0 * gate_us / gate_calls,
                    "remaining_ns_per_call": 1000.0 * flush_us / remaining_calls,
                    "visited_nodes": float(reference["visited_nodes"]),
                }
            )

    if mismatches:
        raise AssertionError(
            "deterministic metrics differ between runs:\n" + "\n".join(mismatches)
        )

    by_aggregate_key = {
        (str(row["codec"]), int(row["ef_search"])): row for row in aggregate
    }
    for row in aggregate:
        baseline = by_aggregate_key[("full", int(row["ef_search"]))]
        row["qps_vs_full"] = float(row["qps_mean"]) / float(
            baseline["qps_mean"]
        )
        row["remaining_calls_vs_full"] = float(
            row["remaining_4bit_calls"]
        ) / float(baseline["remaining_4bit_calls"])

    target_rows: list[dict[str, object]] = []
    for target in (0.95, 0.978, 0.985):
        for codec in CODECS:
            candidates = [
                row
                for row in aggregate
                if row["codec"] == codec and float(row["recall_at_10"]) >= target
            ]
            chosen = max(candidates, key=lambda row: float(row["qps_mean"]))
            target_rows.append(
                {
                    "target_recall": target,
                    "codec": codec,
                    "selected_ef": chosen["ef_search"],
                    "achieved_recall": chosen["recall_at_10"],
                    "qps_mean": chosen["qps_mean"],
                    "qps_min": chosen["qps_min"],
                    "qps_max": chosen["qps_max"],
                    "remaining_4bit_calls": chosen["remaining_4bit_calls"],
                }
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "run_measurements.csv", run_rows)
    write_csv(args.out_dir / "aggregate.csv", aggregate)
    write_csv(args.out_dir / "target_recall.csv", target_rows)

    max_spread = max(aggregate, key=lambda row: float(row["qps_range_pct"]))
    summary = {
        "status": "pass",
        "runs": source_paths,
        "expected_codecs": list(CODECS),
        "expected_efs": list(EFS),
        "row_count_per_run": len(expected_keys),
        "deterministic_fields_checked": list(DETERMINISTIC_FIELDS),
        "deterministic_mismatch_count": 0,
        "timing_aggregation": "arithmetic mean with observed min/max from two order-reversed runs",
        "max_qps_range_pct": max_spread["qps_range_pct"],
        "max_qps_range_case": {
            "codec": max_spread["codec"],
            "ef_search": max_spread["ef_search"],
            "qps_min": max_spread["qps_min"],
            "qps_max": max_spread["qps_max"],
        },
    }
    (args.out_dir / "validation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
