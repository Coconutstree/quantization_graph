#!/usr/bin/env python3
"""Re-align SAQ_B4 rows in a 01 summary after the inner-product columns were
added to the canonical schema.

SAQ's external binary still appends the old 34-column row:
    [0..25] identical, 26 mean_rel, 27 p95_rel, 28 mean_abs, 29 threads,
    30 top10_overlap, 31 pairwise_flip_rate, 32 repeat_id, 33 git_commit.
The canonical header now inserts mean_ip_relative_error / p95_ip_relative_error
between p95_rel (27) and mean_abs (28), so the SAQ row must carry two empty IP
columns at positions 28-29.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def fix_row(row: list[str]) -> list[str]:
    # Reconstruct the 34-field SAQ row: fields 0..27, then the old fields 28..33
    # that currently sit at 28..33 (before the two trailing padding empties).
    if len(row) < 34:
        return row
    original34 = row[:28] + row[28:34]
    return original34[:28] + ["", ""] + original34[28:34]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    rows = list(csv.reader(args.summary.open()))
    if not rows:
        return 0
    header = rows[0]
    out = [header]
    changed = 0
    for row in rows[1:]:
        if len(row) > 1 and row[1] == "SAQ_B4":
            row = fix_row(row)
            changed += 1
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        out.append(row)
    with args.summary.open("w", newline="") as f:
        csv.writer(f).writerows(out)
    print(f"realigned {changed} SAQ rows in {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
