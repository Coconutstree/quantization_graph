"""Glass-NSG system adapter (official Zilliz pyglass, NSG + SQ4U)."""

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


SCRIPT = ROOT / "experiments" / "03_system_fair" / "adapters" / "_scripts" / "glass_run.py"


class GlassNSGAdapter(SystemAdapter):
    name = "Glass-NSG"
    implementation = "official pyglass 2.1.0 (NSG + SQ4U quantized search)"

    def candidate_configs(self, ctx: RunContext) -> list[dict[str, Any]]:
        return [
            {"config_id": f"R{r}_L{l}", "R": r, "L": l}
            for r, l in ((32, 50), (32, 100), (64, 100), (64, 400))
        ]

    @staticmethod
    def _ef_grid() -> list[int]:
        values = list(range(1, 31))
        values += list(range(40, 101, 10))
        values += list(range(140, 461, 40))
        values += [480]
        return values

    def build(self, ctx: RunContext, config: dict[str, Any]) -> dict[str, Any]:
        existing = self.build_done(ctx, config)
        if existing is not None and existing.get("status") == "ok":
            return existing
        graph_path = ctx.index_system_dir(self.name) / f"{self.config_id(config)}.graph"
        proc = run_cmd(
            [
                str(MINICONDA_PYTHON),
                str(SCRIPT),
                "--mode", "build",
                "--base", str(ctx.base_path),
                "--graph", str(graph_path),
                "--R", str(config["R"]),
                "--L", str(config["L"]),
                "--threads", str(ctx.threads),
            ],
            timeout=14400,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Glass build failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        stats = json.loads(last_json_line(proc.stdout or ""))
        stats.update(
            {
                "status": "ok",
                "config_id": self.config_id(config),
                "index_path": str(graph_path),
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
        graph_path = ctx.index_system_dir(self.name) / f"{self.config_id(config)}.graph"
        proc = run_cmd(
            [
                str(MINICONDA_PYTHON),
                str(SCRIPT),
                "--mode", "search",
                "--base", str(ctx.base_path),
                "--query", str(ctx.test_query_path),
                "--gt", str(ctx.test_gt_path),
                "--graph", str(graph_path),
                "--k", str(ctx.k),
                "--ef-list", ",".join(str(v) for v in self._ef_grid()),
                "--threads", str(ctx.threads),
            ],
            timeout=14400,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Glass search failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        build = self.build_done(ctx, config) or {}
        rows: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if not line.strip().startswith("{"):
                continue
            item = json.loads(line)
            search_param = {
                "name": "ef",
                "value": item["ef"],
                "label": f"ef={item['ef']}",
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
                ctx.raw_log(self.name, self.config_id(config), f"ef={item['ef']}", repeat_id)
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
            raise RuntimeError(f"Glass search produced no rows; tail:\n{(proc.stdout or '')[-3000:]}")
        return rows
