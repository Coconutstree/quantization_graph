"""03 system-fair runner.

Usage:
  python legacy/03_system_fair/run_system_fair.py \
      --dataset dbpedia [--systems Ours,SymphonyQG,NGT-QG,OG-LVQ,Glass-NSG] \
      [--validate] [--run] [--repeats 5] [--threads 64]

This is the Python equivalent of the ``run_system_fair.cpp`` entry point in
docs/plans/BASELINE_EXPERIMENT_PLAN_MS_V2.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules["systemfair"] = _pkg

from systemfair.adapters.glass_nsg_adapter import GlassNSGAdapter  # noqa: E402
from systemfair.adapters.ngt_qg_adapter import NGTQGAdapter  # noqa: E402
from systemfair.adapters.og_lvq_adapter import OGLVQAdapter  # noqa: E402
from systemfair.adapters.ours_adapter import OursAdapter  # noqa: E402
from systemfair.adapters.symphonyqg_adapter import SymphonyQGAdapter  # noqa: E402
from systemfair.common import (  # noqa: E402
    ROOT,
    RunContext,
    ensure_dir,
    fvec_count,
    hardware_info,
    write_json,
)
from systemfair.system_adapter import MANIFEST_COLUMNS, SystemAdapter  # noqa: E402
from systemfair.validation_tuner import run_validation  # noqa: E402


ADAPTERS = {
    "Ours": OursAdapter,
    "SymphonyQG": SymphonyQGAdapter,
    "NGT-QG": NGTQGAdapter,
    "OG-LVQ": OGLVQAdapter,
    "Glass-NSG": GlassNSGAdapter,
}


def make_ctx(args: argparse.Namespace) -> RunContext:
    return RunContext(
        dataset=args.dataset,
        data_root=ROOT / args.data_root,
        out_root=ROOT / args.out_root,
        threads=args.threads,
        repeats=args.repeats,
        val_queries=args.val_queries,
    )


def write_tuning_budget(ctx: RunContext, adapters: list[SystemAdapter]) -> None:
    existing = {}
    budget_path = ctx.manifest_dir / "03_system_fair_tuning_budget.json"
    if budget_path.exists():
        try:
            existing = json.loads(budget_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    systems = dict(existing.get("systems", {}))
    for a in adapters:
        systems[a.name] = {
            "candidate_configs": [
                {"config_id": c.get("config_id"), **c} for c in a.candidate_configs(ctx)
            ],
            "implementation": a.implementation,
        }
    budget = {
        "suite": ctx.suite,
        "dataset": ctx.dataset,
        "recall_target": existing.get("recall_target", 0.95),
        "k": existing.get("k", ctx.k),
        "validation_queries": existing.get("validation_queries", ctx.val_queries),
        "test_queries": "rest",
        "repeats": existing.get("repeats", ctx.repeats),
        "threads": existing.get("threads", ctx.threads),
        "seed": existing.get("seed", ctx.seed),
        "nominal_bpd": existing.get("nominal_bpd", 4),
        "note": existing.get(
            "note", "unified 4-bit quantization comparison; single repeat per config"
        ),
        "systems": systems,
    }
    write_json(budget_path, budget)


def init_manifest(ctx: RunContext, adapters: list[SystemAdapter]) -> Path:
    ensure_dir(ctx.manifest_dir)
    manifest = ctx.manifest_dir / "03_system_fair_manifest.csv"
    if not manifest.exists():
        with manifest.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
            writer.writeheader()
    write_json(ctx.manifest_dir / f"{ctx.dataset}_hardware.json", hardware_info())
    return manifest


def _existing_keys(csv_path: Path) -> set[tuple]:
    if not csv_path.exists():
        return set()
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        return {
            (r.get("method"), r.get("config_id"), r.get("search_param"), r.get("repeat_id"))
            for r in reader
        }


def append_manifest_row(manifest: Path, row: dict[str, Any]) -> None:
    key = (row.get("method"), row.get("config_id"), row.get("search_param"), row.get("repeat_id"))
    if key in _existing_keys(manifest):
        return
    with manifest.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writerow(row)


def raw_csv_path(ctx: RunContext) -> Path:
    return ctx.csv_dir / "system_fair_raw.csv"


def _replace_method_rows(
    csv_path: Path,
    rows: list[dict[str, Any]],
    method: str,
) -> None:
    """Atomically replace one system's rows while preserving all baselines."""
    existing: list[dict[str, Any]] = []
    if csv_path.exists():
        with csv_path.open() as f:
            existing = [r for r in csv.DictReader(f) if r.get("method") != method]
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(rows)
    tmp.replace(csv_path)


def write_raw_rows(
    ctx: RunContext,
    rows: list[dict[str, Any]],
    replace_method: str | None = None,
) -> None:
    ensure_dir(ctx.csv_dir)
    if replace_method is not None:
        _replace_method_rows(raw_csv_path(ctx), rows, replace_method)
        return
    existing = _existing_keys(raw_csv_path(ctx))
    rows = [
        r
        for r in rows
        if (r.get("method"), r.get("config_id"), r.get("search_param"), r.get("repeat_id"))
        not in existing
    ]
    new_file = not raw_csv_path(ctx).exists()
    with raw_csv_path(ctx).open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--out-root", default="results/disk_environment")
    ap.add_argument(
        "--systems",
        default=",".join(ADAPTERS),
        help="comma-separated systems",
    )
    ap.add_argument("--validate", action="store_true", help="run validation tuning")
    ap.add_argument("--run", action="store_true", help="run full search sweeps")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--val-queries", type=int, default=1000)
    ap.add_argument(
        "--full-test-queries",
        action="store_true",
        help="use ALL dataset queries for the final sweep (aligns query count "
        "with experiment 02); validation still uses the first N queries",
    )
    ap.add_argument("--max-configs", type=int, default=0)
    ap.add_argument(
        "--overwrite-systems",
        default="",
        help="comma-separated systems whose existing 03 rows are atomically replaced",
    )
    args = ap.parse_args()

    ctx = make_ctx(args)
    ctx.full_test_queries = args.full_test_queries
    names = [n.strip() for n in args.systems.split(",") if n.strip()]
    adapters = [ADAPTERS[n]() for n in names]
    overwrite = {n.strip() for n in args.overwrite_systems.split(",") if n.strip()}
    unknown_overwrite = overwrite - set(names)
    if unknown_overwrite:
        ap.error(
            "--overwrite-systems must be a subset of --systems: "
            + ",".join(sorted(unknown_overwrite))
        )
    if not adapters:
        ap.error("no systems selected")

    write_tuning_budget(ctx, adapters)
    manifest = init_manifest(ctx, adapters)

    if args.validate:
        print(f"[{time.strftime('%H:%M:%S')}] validation tuning for {ctx.dataset} ...")
        selected = run_validation(
            ctx,
            adapters,
            recall_target=0.95,
            max_configs=args.max_configs or None,
        )
        for name, config in selected.items():
            print(f"  {name}: selected {config.get('config_id')}")

    if args.run:
        print(f"[{time.strftime('%H:%M:%S')}] full sweeps for {ctx.dataset} ...")
        ctx.prepare_query_splits()
        if fvec_count(ctx.test_query_path) == 0:
            raise SystemExit(
                f"test query set is empty (val_queries={ctx.val_queries}); "
                "reduce --val-queries below the dataset query count"
            )
        for adapter in adapters:
            sel_path = ctx.selected_config_json(adapter.name)
            if not sel_path.exists():
                print(f"  {adapter.name}: no selected config; running validation first")
                run_validation(ctx, [adapter], recall_target=0.95)
            selected = json.loads(sel_path.read_text())["selected_config"]
            print(f"  {adapter.name}: config={selected.get('config_id')} repeats={ctx.repeats}")
            for repeat_id in range(ctx.repeats):
                ctx.phase = "test"
                t0 = time.time()
                try:
                    rows = adapter.search_sweep(ctx, selected, repeat_id)
                except Exception as exc:
                    print(f"    repeat {repeat_id} FAILED for {adapter.name}: {exc}")
                    continue
                replace_method = adapter.name if adapter.name in overwrite else None
                write_raw_rows(ctx, rows, replace_method)
                if replace_method is not None:
                    _replace_method_rows(manifest, rows, replace_method)
                else:
                    for row in rows:
                        append_manifest_row(manifest, row)
                print(
                    f"    repeat {repeat_id}: {len(rows)} rows "
                    f"({time.time()-t0:.1f}s, recall {rows[0]['recall']:.3f}..{rows[-1]['recall']:.3f})"
                )
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
