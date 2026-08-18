"""OG-LVQ system adapter (official Intel SVS python bindings, Vamana + LVQ)."""

from __future__ import annotations

import json
from typing import Any

from systemfair.common import (
    MINICONDA_PYTHON,
    ROOT,
    RunContext,
    fvec_count,
    last_json_line,
    run_cmd,
)
from systemfair.system_adapter import SystemAdapter


SCRIPT = ROOT / "experiments" / "03_system_fair" / "adapters" / "_scripts" / "svs_run.py"


class OGLVQAdapter(SystemAdapter):
    name = "OG-LVQ"
    implementation = "official Intel SVS python bindings (Vamana + LVQ)"

    def candidate_configs(self, ctx: RunContext) -> list[dict[str, Any]]:
        # unified 4-bit comparison: single-level LVQ4 (4 bit/dim), two R options
        return [
            {
                "config_id": "LVQ4_R32_W400",
                "R": 32,
                "W": 400,
                "alpha": 1.2,
                "primary": 4,
                "residual": 0,
            },
            {
                "config_id": "LVQ4_R64_W400",
                "R": 64,
                "W": 400,
                "alpha": 1.2,
                "primary": 4,
                "residual": 0,
            },
        ]

    @staticmethod
    def _window_grid() -> list[int]:
        values = list(range(10, 31))
        values += list(range(40, 101, 10))
        values += list(range(140, 601, 40))
        return values

    def build(self, ctx: RunContext, config: dict[str, Any]) -> dict[str, Any]:
        existing = self.build_done(ctx, config)
        if existing is not None and existing.get("status") == "ok":
            return existing
        out = ctx.index_system_dir(self.name) / self.config_id(config)
        proc = run_cmd(
            [
                str(MINICONDA_PYTHON),
                str(SCRIPT),
                "--mode", "build",
                "--base", str(ctx.base_path),
                "--out", str(out),
                "--R", str(config["R"]),
                "--W", str(config["W"]),
                "--primary", str(config["primary"]),
                "--residual", str(config["residual"]),
                "--alpha", str(config["alpha"]),
                "--threads", str(ctx.threads),
            ],
            timeout=14400,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"OG-LVQ build failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        stats = json.loads(last_json_line(proc.stdout or ""))
        stats.update(
            {
                "status": "ok",
                "config_id": self.config_id(config),
                "index_path": str(out),
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
        out = ctx.index_system_dir(self.name) / self.config_id(config)
        proc = run_cmd(
            [
                str(MINICONDA_PYTHON),
                str(SCRIPT),
                "--mode", "search",
                "--query", str(ctx.test_query_path),
                "--gt", str(ctx.test_gt_path),
                "--out", str(out),
                "--threads", str(ctx.threads),
                "--k", str(ctx.k),
                "--window-list", ",".join(str(v) for v in self._window_grid()),
            ],
            timeout=14400,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"OG-LVQ search failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        build = self.build_done(ctx, config) or {}
        rows: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if not line.strip().startswith("{"):
                continue
            item = json.loads(line)
            search_param = {
                "name": "W",
                "value": item["window"],
                "label": f"W={item['window']}",
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
                    self.name, self.config_id(config), f"W={item['window']}", repeat_id
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
            raise RuntimeError(f"OG-LVQ search produced no rows; tail:\n{(proc.stdout or '')[-3000:]}")
        return rows
