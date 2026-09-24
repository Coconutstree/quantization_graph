#!/usr/bin/env python3
"""Assemble the current 03 method set after full per-artifact admission.

Requires an explicit fresh destination run. Historical measurements are never
relabelled or exempted from reference, input, binary or resource validation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "disk_bench"))

from orchestrator import _pareto_front, _single_test_rows  # noqa: E402
from diskfair.layout import dataset_run_root  # noqa: E402
from diskfair.native_contract import (  # noqa: E402
    atomic_write_csv,
    flatten_artifact,
    SPECS_BY_KEY,
    LAYER_METHODS,
    load_port_registry,
    validate_artifact,
    validate_layer_completeness,
)
from diskfair.protocol import PROTOCOL_ID, sha256  # noqa: E402

METHODS = list(LAYER_METHODS["05c"])
REUSED = {"Starling-Disk", "SymphonyQG-DiskPort"}
WIDTHS = [10, 20, 40, 60, 100, 160, 240, 400, 580]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--pair-run-id", default=None,
                        help="run id holding the re-measured Ours-Disk and DiskANN-PQ-Disk artifacts; "
                             "used when the two changed methods had to run under a dedicated run id "
                             "because the run configuration (method set) of the canonical run id is frozen")
    parser.add_argument("--experiment", default="03_disk_system")
    parser.add_argument("--layer", default="05c")
    parser.add_argument("--budget-gib", type=float, default=4.0)
    args = parser.parse_args()

    run_id = args.run_id
    run_root = ROOT / "results" / "manifests" / run_id
    dest = dataset_run_root(run_root, args.layer, args.dataset)
    aggregate = dest / "tables" / "formal_test_rows.csv"
    if aggregate.exists():
        raise SystemExit("refusing to overwrite an existing aggregate; use a new run-id")
    pair_dest = (dataset_run_root(ROOT / "results" / "manifests" / args.pair_run_id,
                                  args.layer, args.dataset)
                 if args.pair_run_id else None)

    artifacts: list[tuple[Path, dict]] = []
    ports = load_port_registry(ROOT / "src" / "disk_bench" / "ports.local.json")
    disk_root = ROOT / "work" / "05_disk_system_fair" / "disk_root"
    for method in METHODS:
        name = f"{method}__hybrid_disk__B4__standard__w32__r0.json"
        path = dest / "raw" / method / "test" / name
        if method not in REUSED and pair_dest is not None:
            path = pair_dest / "raw" / method / "test" / name
        if not path.is_file():
            raise SystemExit(f"missing measured artifact: {path}")
        artifact = json.loads(path.read_text())
        spec = SPECS_BY_KEY[f"{args.layer}:{method}"]
        port = ports[f"{args.layer}:{method}"]
        expected = {
            "dataset": args.dataset,
            "phase": "test",
            "run_id": artifact.get("run_id"),
            "repeat_id": artifact.get("repeat_id"),
            "workers": artifact.get("workers"),
            "storage_mode": artifact.get("storage_mode"),
            "cache_mode": artifact.get("cache_mode"),
            "implementation_fingerprint": port["implementation_fingerprint"],
            "native_binary_sha256": port["binary_sha256"],
            "input_manifest_sha256": artifact.get("input_manifest_sha256"),
            "query_split_sha256": artifact.get("query_split_sha256"),
            "query_order_sha256": artifact.get("query_order_sha256"),
            "query_order_seed": artifact.get("query_order_seed"),
            "warmup_queries": artifact.get("warmup_queries"),
            "search_dram_budget_gib": args.budget_gib,
            "protocol_id": PROTOCOL_ID,
        }
        try:
            validate_artifact(path, spec=spec, expected=expected, disk_root=disk_root)
        except Exception as exc:
            raise SystemExit(f"source artifact failed admission: {path}: {exc}") from exc
        artifacts.append((path, artifact))

    rows = [row for path, artifact in artifacts for row in flatten_artifact(path, artifact)]
    validate_layer_completeness(
        rows,
        layer=args.layer,
        dataset=args.dataset,
        repeats=1,
        workers=(32,),
        budget_gib=args.budget_gib,
        cache_mode="standard",
        methods=METHODS,
        storage_modes=("hybrid_disk",),
    )
    for method in METHODS:
        widths = sorted(int(r["search_width"]) for r in rows if r["method"] == method)
        assert widths == WIDTHS, f"{method} widths {widths}"

    formal_rows = _single_test_rows(rows)
    groups: dict[tuple, list[dict]] = {}
    for row in formal_rows:
        key = (
            str(row["method"]), str(row.get("ablation") or ""), str(row["workers"]),
            str(row.get("storage_mode") or ""), str(row["search_dram_budget_gib"]),
            str(row["cache_mode"]),
        )
        groups.setdefault(key, []).append(row)
    frontier_rows: list[dict] = []
    for group in groups.values():
        frontier_rows.extend(_pareto_front(group))
    frontier_rows.sort(key=lambda row: (
        str(row["method"]), str(row.get("ablation") or ""), int(row["workers"]),
        str(row.get("storage_mode") or ""), float(row["recall"]),
    ))

    aggregate.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise SystemExit("no rows to write")
    atomic_write_csv(aggregate, rows)
    frontier = aggregate.with_name("formal_test_frontier.csv")
    atomic_write_csv(frontier, frontier_rows)
    reused = [
        {
            "method": path.parent.parent.name,
            "artifact": str(path.relative_to(ROOT)),
            "sha256": sha256(path),
            "note": ("binary sha256 and search configuration unchanged; the port records but never "
                     "reads --tuning-lock, so the Ours-driven lock rewrite cannot affect this row set"),
        }
        for path, _ in artifacts if path.parent.parent.name in REUSED
    ]
    manifest = {
        "schema_version": 2,
        "run_id": run_id,
        "layer": args.layer,
        "dataset": args.dataset,
        "protocol_id": PROTOCOL_ID,
        "formal_ready": True,
        "experiment": args.experiment,
        "search_dram_budget_gib": args.budget_gib,
        "memory_comparison": "shared_ram_budget_rss",
        "cache_mode": "standard",
        "storage_modes": ["hybrid_disk"],
        "raw_csv_sha256": sha256(aggregate),
        "statistic": "single_run",
        "rows": len(rows),
        "methods": METHODS,
        "workers": [32],
        "repeats": 1,
        "frontier_csv": str(frontier),
        "frontier_csv_sha256": sha256(frontier),
        "source_artifacts": [
            {"path": str(path), "sha256": sha256(path)} for path, _ in artifacts
        ],
        "reused_artifacts": reused,
        "assembly_note": "All source artifacts passed full admission; method set follows the current 03/05 contract.",
        **({"pair_run_id": args.pair_run_id} if args.pair_run_id else {}),
    }
    (aggregate.with_suffix(".manifest.json")).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {aggregate.relative_to(ROOT)} ({len(rows)} rows) and its manifest")
    print(f"reused methods: {sorted(REUSED)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
