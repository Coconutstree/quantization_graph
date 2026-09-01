#!/usr/bin/env python3
"""Merge 03 system-fair raw logs into system_fair_merged.csv."""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "experiments" / "03_system_fair")]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import merge_raw  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results/memory_environment")
    ap.add_argument("--suite", default="03_system_fair")
    args = ap.parse_args()
    base = Path(args.out_root) / args.suite / args.dataset / "csv"
    raw = base / "system_fair_raw.csv"
    if not raw.exists():
        raise SystemExit(f"missing {raw}")
    merged = base / "system_fair_merged.csv"
    merge_raw(raw, merged)
    print(f"wrote {merged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
