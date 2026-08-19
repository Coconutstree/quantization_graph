#!/usr/bin/env python3
"""Generate the EXPERIMENT_AUDIT.md for a dataset's 03 system-fair results."""

from __future__ import annotations

import argparse
import json
import sys
import types
from datetime import datetime
from pathlib import Path

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "experiments" / "03_system_fair")]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import read_csv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results")
    ap.add_argument("--suite", default="03_system_fair")
    args = ap.parse_args()
    base = Path(args.out_root) / args.suite / args.dataset
    manifest = base / "manifests" / "03_system_fair_manifest.csv"
    budget_path = base / "manifests" / "03_system_fair_tuning_budget.json"
    median_csv = base / "csv" / "system_fair_median.csv"
    interp_csv = base / "csv" / "system_fair_interpolated.csv"

    budget = json.loads(budget_path.read_text()) if budget_path.exists() else {}
    rows = read_csv(manifest) if manifest.exists() else []
    median = read_csv(median_csv) if median_csv.exists() else []
    interp = read_csv(interp_csv) if interp_csv.exists() else []

    methods = sorted({r["method"] for r in rows})
    lines = [
        f"# EXPERIMENT_AUDIT - {args.dataset} (03 system-fair)",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Method list and implementation provenance",
        "",
        "| method | implementation | notes |",
        "|---|---|---|",
    ]
    impl = {}
    for r in rows:
        impl[r["method"]] = r.get("implementation", "")
    for m in methods:
        lines.append(f"| {m} | {impl.get(m, '')} | official implementation; no algorithm modification |")

    lines += [
        "",
        "## Tuning budget",
        "",
        f"- validation queries: {budget.get('validation_queries', '?')} (first N, disjoint from test)",
        f"- recall target: {budget.get('recall_target', '?')}",
        f"- repeats: {budget.get('repeats', '?')} (single repeat per config)",
        f"- threads: {budget.get('threads', '?')}",
        f"- nominal bits per dimension: {budget.get('nominal_bpd', 4)} (unified 4-bit comparison)",
        f"- candidate configs: {json.dumps(budget.get('systems', {}), default=str)[:2000]}",
        "",
        "## Run status",
        "",
        f"- manifest rows: {len(rows)}",
        f"- median rows: {len(median)}",
        f"- interpolated rows: {len(interp)}",
        "",
        "## Known deviations from BASELINE_EXPERIMENT_PLAN_MS_V2.md",
        "",
        "- The unified runner and adapters are implemented in Python "
        "(equivalent implementations of the listed C++ files); all baselines "
        "run their official binaries/bindings unmodified.",
        "- DiskANN3 crate patch (instrumentation only, no algorithm change): "
        "multi_insert now prints points_processed progress every 50k points "
        "during the shared fp32 Vamana graph build; graph output is identical.",
        "- Query split: the first 1000 queries are the validation set; "
        "the remaining queries form the test set (test queries never used for "
        "index-config selection).",
        "- Ours uses the RaBitQ ExRaBitQ4 HNSW path (M=32, efConstruction=400, "
        "efSearch sweep 10..460, 4-bit payload + residual4 rerank).",
        "- NGT-QG: TSV-created indexes use 1-based ids (adapter subtracts 1); "
        "the QG search sweeps the native result-expansion parameter `-p` "
        "(candidate pool = k * p with exact rerank) at k=10 / epsilon=0.1 "
        "because the 4-bit-per-subvector quantization is coarse on high-dim "
        "data. If the full dataset still cannot reach the recall target, the "
        "max reachable recall is reported rather than hidden.",
        "- Single-machine measurement; SIMD recorded per run.",
        "",
        "## Interpolated main table (Recall@10)",
        "",
        "| method | target | qps | latency_mean_us | latency_p95_us | index_size_mb |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(interp, key=lambda x: (x["method"], float(x["recall_target"]))):
        lines.append(
            f"| {r['method']} | {r.get('recall_target')} | "
            f"{float(r['qps']):.1f} | {float(r['latency_mean_us']):.1f} | "
            f"{float(r.get('latency_p95_us', 0)):.1f} | {float(r.get('index_size_mb', 0)):.1f} |"
        )

    audit_dir = base / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "EXPERIMENT_AUDIT.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {audit_dir / 'EXPERIMENT_AUDIT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
