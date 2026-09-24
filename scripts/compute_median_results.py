#!/usr/bin/env python3
"""Median-over-repeats aggregation for 03 system-fair results."""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "legacy" / "03_system_fair")]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import median_results  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results/archive/legacy_layout_20260918/disk_environment")
    ap.add_argument("--suite", default="03_system_fair")
    args = ap.parse_args()
    base = Path(args.out_root) / args.suite / args.dataset / "csv"
    median_results(base / "system_fair_merged.csv", base / "system_fair_median.csv")
    print(f"wrote {base / 'system_fair_median.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
