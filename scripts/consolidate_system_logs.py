#!/usr/bin/env python3
"""Consolidate per-search-parameter raw logs into ONE unified log per method.

The unified log lives at
``results/03_system_fair/<dataset>/logs/<method>/<method>.log`` and contains a
header with build time / memory / config plus one block per search parameter.
The original per-parameter files are preserved under ``<method>/_per_param/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import types
from pathlib import Path

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "experiments" / "03_system_fair")]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import read_csv  # noqa: E402


def native_lines(per_param_file: Path, method_log: Path) -> list[str]:
    if not per_param_file.exists():
        return []
    if per_param_file.resolve() == method_log.resolve():
        # new unified format: raw_log points at the method log itself
        return []
    lines = per_param_file.read_text(errors="replace").splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("{") or s.startswith("}"):
            continue  # JSON block of the old per-param format
        if s.startswith('"') and s.endswith('"'):
            continue
        out.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results")
    ap.add_argument("--suite", default="03_system_fair")
    args = ap.parse_args()

    base = Path(args.out_root) / args.suite / args.dataset / "logs"
    raw_csv = Path(args.out_root) / args.suite / args.dataset / "csv" / "system_fair_raw.csv"
    if not raw_csv.exists():
        raise SystemExit(f"missing {raw_csv} (run phase must be complete)")
    rows = read_csv(raw_csv)

    by_method: dict[str, list[dict]] = {}
    for r in rows:
        by_method.setdefault(r["method"], []).append(r)

    header_fields = [
        "implementation",
        "build_time_ms",
        "graph_build_time_ms",
        "index_size_mb",
        "peak_rss_mb",
        "graph_degree",
        "build_ef",
        "nominal_bpd",
        "threads",
        "query_count",
        "k",
        "metric",
        "git_commit",
        "simd",
        "cpu_model",
    ]

    for method, method_rows in sorted(by_method.items()):
        method_dir = base / method
        method_dir.mkdir(parents=True, exist_ok=True)
        log_path = method_dir / f"{method}.log"
        new_format = any(
            Path(str(r.get("raw_log", ""))).resolve() == log_path.resolve()
            for r in method_rows
        )
        if new_format and log_path.exists() and log_path.stat().st_size < 5 * 1024 * 1024:
            print(f"{method}: already unified ({log_path.stat().st_size} bytes); skipped")
            continue
        template = method_rows[0]
        with log_path.open("w") as f:
            f.write(f"# method: {method}\n")
            for key in header_fields:
                val = template.get(key)
                if val not in (None, ""):
                    f.write(f"# {key}: {val}\n")
            f.write("\n")
            sort_key = lambda r: (  # noqa: E731
                r.get("config_id", ""),
                float(_param_num(r.get("search_param", "0"))),
                int(r.get("repeat_id", 0) or 0),
            )
            for r in sorted(method_rows, key=sort_key):
                f.write(
                    f"## config={r.get('config_id')} search_param={r.get('search_param')} "
                    f"repeat={r.get('repeat_id')}\n"
                )
                f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
                old_file = Path(str(r.get("raw_log", "")))
                for line in native_lines(old_file, log_path):
                    f.write("  " + line + "\n")
                f.write("\n")

        # preserve the per-parameter files under _per_param/
        per_param_dir = method_dir / "_per_param"
        per_param_dir.mkdir(exist_ok=True)
        moved = 0
        for old in method_dir.glob(f"{method}_*_repeat*.log"):
            shutil.move(str(old), str(per_param_dir / old.name))
            moved += 1
        print(f"{method}: {len(method_rows)} rows -> {log_path} (moved {moved} per-param files)")
    return 0


def _param_num(label: str) -> float:
    import re

    m = re.search(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", str(label))
    return float(m.group(0)) if m else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
