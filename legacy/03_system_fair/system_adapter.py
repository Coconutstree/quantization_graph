"""Adapter base class for the 03 system-fair experiment.

Each adapter wraps one full ANN system (its official implementation) and
implements:

* ``candidate_configs``   -- index configurations offered to the validation tuner
* ``build``               -- build the index and report build-time / size / RSS
* ``search_sweep``        -- run the full query sweep for one repeat and return
                             one result row per search parameter

The runner, validation tuner and Pareto builder only talk to this interface,
so adding a new system means adding one adapter file.
"""

from __future__ import annotations

import abc
import json
import time
from typing import Any

from systemfair.common import (
    RunContext,
    append_csv_row,
    base_row,
    ensure_dir,
    peak_rss_mb,
    read_json,
    write_json,
)


MANIFEST_COLUMNS = [
    "suite",
    "dataset",
    "method",
    "config_id",
    "search_param",
    "repeat_id",
    "recall",
    "qps",
    "latency_mean_us",
    "latency_p50_us",
    "latency_p95_us",
    "build_time_ms",
    "index_size_mb",
    "peak_rss_mb",
    "graph_degree",
    "build_ef",
    "nominal_bpd",
    "index_config_json",
    "search_config_json",
    "query_count",
    "k",
    "metric",
    "threads",
    "git_commit",
    "simd",
    "cpu_model",
    "implementation",
    "raw_log",
    "notes",
]


class SystemAdapter(abc.ABC):
    name: str = ""
    implementation: str = ""

    @abc.abstractmethod
    def candidate_configs(self, ctx: RunContext) -> list[dict[str, Any]]:
        """Index configurations offered to the validation tuner."""

    @abc.abstractmethod
    def build(self, ctx: RunContext, config: dict[str, Any]) -> dict[str, Any]:
        """Build an index for ``config``; return build stats dict."""

    def search_sweep(
        self,
        ctx: RunContext,
        config: dict[str, Any],
        repeat_id: int,
    ) -> list[dict[str, Any]]:
        """Return one result row per search parameter for one repeat."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # helpers shared by adapters
    # ------------------------------------------------------------------

    def config_id(self, config: dict[str, Any]) -> str:
        return config.get("config_id", "")

    def build_done(self, ctx: RunContext, config: dict[str, Any]) -> dict[str, Any] | None:
        path = ctx.build_record_path(self.name, self.config_id(config))
        if path.exists():
            return read_json(path)
        return None

    def record_build(
        self, ctx: RunContext, config: dict[str, Any], stats: dict[str, Any]
    ) -> None:
        path = ctx.build_record_path(self.name, self.config_id(config))
        write_json(path, stats)

    def emit_row(
        self,
        ctx: RunContext,
        config: dict[str, Any],
        search_param: dict[str, Any],
        repeat_id: int,
        stats: dict[str, Any],
        raw_log: str,
        notes: str = "",
        native_extra: str = "",
    ) -> dict[str, Any]:
        """Build one CSV row from adapter stats and persist a raw log."""
        row = base_row(ctx, self.name)
        row.update(
            {
                "config_id": self.config_id(config),
                "search_param": search_param.get("label", str(search_param.get("value", ""))),
                "repeat_id": repeat_id,
                "graph_degree": config.get("M", config.get("R", config.get("anng_edges", ""))),
                "build_ef": config.get(
                    "efConstruction",
                    config.get(
                        "L_build",
                        config.get(
                            "W",
                            config.get("EF", config.get("L", config.get("qg_edges", ""))),
                        ),
                    ),
                ),
                "nominal_bpd": 4,  # unified 4-bit comparison
                "index_config_json": json.dumps(config, sort_keys=True, separators=(",", ":")),
                "search_config_json": json.dumps(search_param, sort_keys=True, separators=(",", ":")),
                "implementation": self.implementation,
                "raw_log": str(ctx.method_log(self.name)),
                "notes": notes,
            }
        )
        row.update({k: v for k, v in stats.items() if v is not None})
        # persist the raw log (one JSON line with full stats)
        # Unified log layout: one file per method, appended per search-param
        # row, containing build stats + memory + per-param results. Validation
        # rows (phase=val) are recorded in the tuning CSV, not the raw log.
        if ctx.phase == "test":
            log_path = ctx.method_log(self.name)
            ensure_dir(log_path.parent)
            with log_path.open("a") as f:
                f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                if native_extra:
                    f.write(native_extra.rstrip("\n") + "\n")
        return row

    def append_manifest(
        self, ctx: RunContext, row: dict[str, Any], manifest_csv: Any
    ) -> None:
        append_csv_row(manifest_csv, row, MANIFEST_COLUMNS)

    def measure_peak(self, fn):
        """Run fn and report {peak_rss_mb, build_time_ms} around it."""
        start = time.perf_counter()
        result = fn()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        result["build_time_ms"] = elapsed_ms
        result["peak_rss_mb"] = peak_rss_mb()
        return result
