"""NGT-QG system adapter (official Yahoo Japan NGT qbg CLI)."""

from __future__ import annotations

import json
from typing import Any

from systemfair.common import (
    MINICONDA_PYTHON,
    ROOT,
    RunContext,
    fvec_count,
    fvec_dim,
    last_json_line,
    run_cmd,
)
from systemfair.system_adapter import SystemAdapter


SCRIPT = ROOT / "legacy" / "03_system_fair" / "adapters" / "_scripts" / "ngt_run.py"


class NGTQGAdapter(SystemAdapter):
    name = "NGT-QG"
    implementation = "official Yahoo Japan NGT 2.7.4 qbg CLI (ANNG + QG)"

    def candidate_configs(self, ctx: RunContext) -> list[dict[str, Any]]:
        # NGT-QG builds on 990k x 1536 take several hours each; keep a single
        # candidate (E32) so validation stays tractable (documented in audit).
        return [
            {
                "config_id": "ANNG_E32_QG_E32_Q32",
                "anng_edges": 32,
                "qg_edges": 32,
                "subdim": 32,
            }
        ]

    @staticmethod
    def _p_grid() -> str:
        # result-expansion sweep (internal pool = k * p, exact rerank on pool)
        return "1:50:5,50:500:50,500:5000:500,5000:50000:5000"

    def build(self, ctx: RunContext, config: dict[str, Any]) -> dict[str, Any]:
        existing = self.build_done(ctx, config)
        if existing is not None and existing.get("status") == "ok":
            return existing
        index_dir = ctx.index_system_dir(self.name) / self.config_id(config)
        tsv_cache = ctx.shared_split_dir / "ngt_tsv"
        dim = fvec_dim(ctx.base_path)
        proc = run_cmd(
            [
                str(MINICONDA_PYTHON),
                str(SCRIPT),
                "--mode", "build",
                "--base", str(ctx.base_path),
                "--index", str(index_dir),
                "--tsv-cache", str(tsv_cache),
                "--anng-edges", str(config["anng_edges"]),
                "--qg-edges", str(config["qg_edges"]),
                "--subdim", str(config["subdim"]),
                "--threads", str(ctx.threads),
                "--dim", str(dim),
            ],
            timeout=64800,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"NGT-QG build failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        stats = json.loads(last_json_line(proc.stdout or ""))
        stats.update(
            {
                "status": "ok",
                "config_id": self.config_id(config),
                "index_path": str(index_dir),
            }
        )
        self.record_build(ctx, config, stats)
        return stats

    def search_sweep(
        self,
        ctx: RunContext,
        config: dict[str, Any],
        repeat_id: int,
    ) -> list[dict[str, Any]]:
        self.build(ctx, config)
        ctx.prepare_query_splits()
        index_dir = ctx.index_system_dir(self.name) / self.config_id(config)
        tsv_cache = ctx.shared_split_dir / "ngt_tsv"
        proc = run_cmd(
            [
                str(MINICONDA_PYTHON),
                str(SCRIPT),
                "--mode", "search",
                "--query", str(ctx.test_query_path),
                "--gt", str(ctx.test_gt_path),
                "--index", str(index_dir),
                "--tsv-cache", str(tsv_cache),
                "--k", str(ctx.k),
                "--p-ranges", self._p_grid(),
            ],
            timeout=64800,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"NGT-QG search failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        build = self.build_done(ctx, config) or {}
        rows: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if not line.strip().startswith("{"):
                continue
            item = json.loads(line)
            search_param = {
                "name": "result_expansion_p",
                "value": item["result_expansion"],
                "label": f"p={item['result_expansion']:.4g}",
            }
            stats = {
                "recall": item["recall"],
                "qps": item["qps"],
                "latency_mean_us": item["latency_mean_us"],
                "latency_p50_us": item["latency_p50_us"],
                "latency_p95_us": item["latency_p95_us"],
                "query_count": fvec_count(ctx.test_query_path),
                "build_time_ms": build.get("build_time_ms", 0.0),
                "index_size_mb": build.get("index_size_mb", 0.0),
                "peak_rss_mb": item.get("peak_rss_mb", 0.0),
            }
            raw_log = str(
                ctx.raw_log(
                    self.name,
                    self.config_id(config),
                    f"p={item['result_expansion']:.4g}",
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
                    native_extra=line,
                )
            )
        if not rows:
            raise RuntimeError(f"NGT-QG search produced no rows; tail:\n{(proc.stdout or '')[-3000:]}")
        return rows
