"""Ours adapter for the 03 system-fair runner.

The paper's unified method is Ours-DiskANN (ExRaBitQ4 4-bit symmetric Vamana
inside the DiskANN3 framework, paper-pruned search, residual4 rerank).
Experiment 02 reports Ours at M=32 (degree-matched with the R=32 shared
graph); experiment 03 reports Ours at M=64. This adapter feeds the M=64
rows (from the root Ours-DiskANN binary) into the 03 comparison.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from systemfair.common import ROOT, RunContext, fvec_count  # noqa: E402
from systemfair.system_adapter import SystemAdapter  # noqa: E402


def ours_rows_from_02_csv(
    dataset: str, out_root, degree: int
) -> list[dict[str, Any]]:
    """Read the formal INT8-query Ours rows for the given graph degree.

    Query-codec ablations share the same experiment-02 CSV.  The formal 03
    method consumes only ``query_coarse_codec=int8`` rows. When a run is
    repeated, the last row for each search-list size is authoritative.
    """
    csv_path = (
        Path(out_root) / "02_diskann_fair" / dataset / "csv" / "diskann_fair_raw.csv"
    )
    if not csv_path.exists():
        raise RuntimeError(f"02 Ours rows missing at {csv_path}")
    with csv_path.open() as f:
        candidates = [
            r
            for r in csv.DictReader(f)
            if r.get("method") == "Ours"
            and int(r.get("max_degree", 0) or 0) == degree
        ]
    rows = [
        r
        for r in candidates
        if r.get("query_coarse_codec", "").strip('"').lower() == "int8"
    ]
    if not rows:
        raise RuntimeError(
            f"no formal INT8-query Ours rows with max_degree={degree} in {csv_path}"
        )
    by_l: dict[int, dict[str, str]] = {}
    for row in rows:
        by_l[int(row.get("search_param_value", 0) or 0)] = row
    out = []
    for _, r in sorted(by_l.items()):
        out.append(
            {
                "l_search": int(r.get("search_param_value", 0)),
                "recall": float(r.get("recall", 0)),
                "qps": float(r.get("qps", 0)),
                "latency_mean_us": float(r.get("latency_mean_us", 0)),
                "latency_p95_us": float(r.get("latency_p95_us", 0)),
                "build_time_ms": float(r.get("build_time_ms", 0)),
                "graph_build_time_ms": float(r.get("graph_build_time_ms", 0)),
                "index_size_mb": float(r.get("index_size_mb", 0)),
                "peak_rss_mb": float(r.get("peak_rss_mb", 0)),
                "visited_nodes": r.get("visited_nodes", ""),
                "distance_calls": r.get("distance_calls", ""),
                "native_line": (
                    f"L_search={r.get('search_param_value')}\t"
                    f"recall@10={r.get('recall')}\tqps={r.get('qps')}"
                ),
            }
        )
    return out


def ensure_ours_run(
    dataset: str,
    degree: int,
    threads: int,
    centroid_count: int = 1,
    out_root: Path | str = ROOT / "results",
) -> None:
    """Run the Ours-DiskANN binary once for this dataset/degree if missing."""
    csv_path = (
        Path(out_root) / "02_diskann_fair" / dataset / "csv" / "diskann_fair_raw.csv"
    )
    if csv_path.exists():
        with csv_path.open() as f:
            has = any(
                r.get("method") == "Ours"
                and int(r.get("max_degree", 0) or 0) == degree
                and r.get("query_coarse_codec", "").strip('"').lower() == "int8"
                for r in csv.DictReader(f)
            )
        if has:
            return
    runner = ROOT / "Ours" / "experiments" / "run_ours.py"
    subprocess.run(
        [
            sys.executable,
            str(runner),
            "--dataset", dataset,
            "--M", str(degree),
            "--centroid-count", str(centroid_count),
            "--out-root", str(out_root),
            "--threads", str(threads),
        ],
        cwd=ROOT,
        check=True,
    )


class OursAdapter(SystemAdapter):
    name = "Ours"
    implementation = (
        "Ours-DiskANN (DiskANN3 + ExRaBitQ4 symmetric Vamana, paper-prune, "
        "DB=1-bit/INT8-query coarse gate, residual4 rerank); M=32 and M=64 "
        "configs, centroid_count K=1"
    )

    def candidate_configs(self, ctx: RunContext) -> list[dict[str, Any]]:
        return [
            {
                "config_id": "OursDiskANN_M64",
                "M": 64,
                "R": 64,
                "L_build": 400,
                "alpha": 1.2,
                "rerank_candidates": 100,
                "residual_bits": 4,
                "centroid_count": 1,
                "query_coarse_codec": "int8",
            }
        ]

    def build(self, ctx: RunContext, config: dict[str, Any]) -> dict[str, Any]:
        existing = self.build_done(ctx, config)
        if existing is not None and existing.get("status") == "ok":
            return existing
        degree = int(config["M"])
        ensure_ours_run(
            ctx.dataset,
            degree,
            ctx.threads,
            int(config.get("centroid_count", 1)),
            ctx.out_root,
        )
        raw = ours_rows_from_02_csv(ctx.dataset, ctx.out_root, degree)
        stats = {
            "status": "ok",
            "config_id": self.config_id(config),
            "build_time_ms": raw[0]["build_time_ms"],
            "graph_build_time_ms": raw[0]["graph_build_time_ms"],
            "index_size_mb": raw[0]["index_size_mb"],
            "peak_rss_mb": raw[0]["peak_rss_mb"],
        }
        self.record_build(ctx, config, stats)
        return stats

    def search_sweep(
        self,
        ctx: RunContext,
        config: dict[str, Any],
        repeat_id: int,
    ) -> list[dict[str, Any]]:
        # Search-only reruns may load an already frozen graph.  Keep the
        # original formal construction metadata from the 03 build record;
        # only the search metrics below come from the refreshed INT8-query run.
        build_stats = self.build(ctx, config)
        degree = int(config["M"])
        raw = ours_rows_from_02_csv(ctx.dataset, ctx.out_root, degree)
        rows = []
        for item in raw:
            stats = {
                "recall": item["recall"],
                "qps": item["qps"],
                "latency_mean_us": item["latency_mean_us"],
                "latency_p50_us": 0.0,
                "latency_p95_us": item["latency_p95_us"],
                "query_count": fvec_count(ctx.test_query_path),
                "build_time_ms": float(build_stats.get("build_time_ms", item["build_time_ms"])),
                "graph_build_time_ms": float(
                    build_stats.get("graph_build_time_ms", item["graph_build_time_ms"])
                ),
                "index_size_mb": float(build_stats.get("index_size_mb", item["index_size_mb"])),
                "visited_nodes": item["visited_nodes"],
                "distance_calls": item["distance_calls"],
                "peak_rss_mb": float(build_stats.get("peak_rss_mb", item["peak_rss_mb"])),
            }
            search_param = {
                "name": "L_search",
                "value": item["l_search"],
                "label": f"L_search={item['l_search']}",
            }
            raw_log = str(
                ctx.raw_log(
                    self.name,
                    self.config_id(config),
                    f"L_search={item['l_search']}",
                    repeat_id,
                )
            )
            rows.append(
                self.emit_row(
                    ctx,
                    config,
                    search_param,
                    repeat_id,
                    stats,
                    raw_log,
                    native_extra=item["native_line"],
                )
            )
        return rows
