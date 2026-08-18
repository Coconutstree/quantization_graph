"""Pareto / result post-processing for the 03 system-fair experiment.

Implements the plan's merge -> median -> interpolate pipeline on top of
``system_fair_raw.csv`` (Python equivalent of ``pareto_builder.{h,cpp}``).
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any

import numpy as np


MERGE_COLUMNS = [
    "suite",
    "dataset",
    "method",
    "config_id",
    "search_param",
    "repeat_id",
    "recall",
    "qps",
    "latency_mean_us",
    "latency_p50_us",
    "latency_p95_us",
    "build_time_ms",
    "index_size_mb",
    "peak_rss_mb",
    "query_count",
    "k",
    "metric",
    "threads",
    "git_commit",
    "implementation",
    "raw_log",
    "notes",
]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _param_num(label: str) -> float:
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", str(label))
    return float(m.group(0)) if m else 0.0


def merge_raw(raw_csv: Path, merged_csv: Path) -> None:
    rows = read_csv(raw_csv)
    # drop duplicate raw_log entries and sort for readability
    seen = set()
    out: list[dict[str, Any]] = []
    for row in sorted(
        rows,
        key=lambda r: (
            r.get("method", ""),
            r.get("config_id", ""),
            _param_num(r.get("search_param", "0")),
            int(r.get("repeat_id", 0) or 0),
        ),
    ):
        key = (row.get("method"), row.get("config_id"), row.get("search_param"), row.get("repeat_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    write_csv(merged_csv, out, MERGE_COLUMNS)


def median_results(merged_csv: Path, median_csv: Path) -> None:
    rows = read_csv(merged_csv)
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = (row.get("method"), row.get("config_id"), row.get("search_param"))
        groups.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for key, group in sorted(
        groups.items(),
        key=lambda kv: (kv[0][0], kv[0][1], _param_num(kv[0][2])),
    ):
        method, config_id, search_param = key
        template = dict(group[0])
        for col in (
            "recall",
            "qps",
            "latency_mean_us",
            "latency_p50_us",
            "latency_p95_us",
            "peak_rss_mb",
        ):
            vals = [float(r.get(col, 0) or 0) for r in group]
            template[col] = float(np.median(vals))
        template["repeat_id"] = "median"
        template["n_repeats"] = len(group)
        out.append(template)
    columns = MERGE_COLUMNS + ["n_repeats"]
    write_csv(median_csv, out, columns)


def interpolate_targets(
    median_csv: Path,
    interp_csv: Path,
    targets: list[float] | None = None,
) -> None:
    if targets is None:
        targets = [0.90, 0.95, 0.97]
    rows = read_csv(median_csv)
    by_method: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_method.setdefault(row["method"], []).append(row)
    out: list[dict[str, Any]] = []
    for method, group in by_method.items():
        group.sort(key=lambda r: float(r["recall"]))
        for target in targets:
            row = interpolate_point(group, target)
            if row is None:
                continue
            row["method"] = method
            row["recall_target"] = target
            out.append(row)
    write_csv(interp_csv, out, list(out[0].keys()) if out else [])


def interpolate_point(group: list[dict[str, Any]], target: float) -> dict[str, Any] | None:
    recalls = np.asarray([float(r["recall"]) for r in group])
    if recalls.size == 0 or recalls[-1] < target - 1e-9:
        return None  # system cannot reach this recall
    if recalls[0] >= target:
        best = group[0]
        return {**best, "interp": "best_available"}
    qps = np.asarray([float(r["qps"]) for r in group])
    lat = np.asarray([float(r["latency_mean_us"]) for r in group])
    lat95 = np.asarray([float(r["latency_p95_us"]) for r in group])
    # linear interpolation on the recall vs qps curve
    q = np.interp(target, recalls, qps)
    l = np.interp(target, recalls, lat)
    l95 = np.interp(target, recalls, lat95)
    base = dict(group[0])
    base.update(
        {
            "recall": round(target, 4),
            "qps": round(float(q), 4),
            "latency_mean_us": round(float(l), 4),
            "latency_p95_us": round(float(l95), 4),
            "interp": "linear",
        }
    )
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results")
    ap.add_argument("--suite", default="03_system_fair")
    ap.add_argument("--targets", default="0.90,0.95,0.97")
    args = ap.parse_args()
    base = Path(args.out_root) / args.dataset / "csv" / args.suite
    raw = base / "system_fair_raw.csv"
    if not raw.exists():
        raise SystemExit(f"missing {raw}")
    merged = base / "system_fair_merged.csv"
    median = base / "system_fair_median.csv"
    interp = base / "system_fair_interpolated.csv"
    merge_raw(raw, merged)
    median_results(merged, median)
    interpolate_targets(
        median, interp, [float(x) for x in args.targets.split(",")]
    )
    print(f"wrote {merged}, {median}, {interp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
