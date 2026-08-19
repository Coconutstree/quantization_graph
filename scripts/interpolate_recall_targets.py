#!/usr/bin/env python3
"""Interpolate 03 system-fair curves at fixed recall targets."""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "experiments" / "03_system_fair")]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import interpolate_targets  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results")
    ap.add_argument("--suite", default="03_system_fair")
    ap.add_argument("--targets", default="0.90,0.95,0.97")
    args = ap.parse_args()
    base = Path(args.out_root) / args.suite / args.dataset / "csv"
    interpolate_targets(
        base / "system_fair_median.csv",
        base / "system_fair_interpolated.csv",
        [float(x) for x in args.targets.split(",")],
    )
    print(f"wrote {base / 'system_fair_interpolated.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
