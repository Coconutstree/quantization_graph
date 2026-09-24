"""Validation-based index-configuration tuner (Track A tuning budget)."""

from __future__ import annotations

import csv
import json
from typing import Any

from systemfair.common import RunContext, ensure_dir
from systemfair.system_adapter import SystemAdapter


TUNING_COLUMNS = [
    "method",
    "config_id",
    "search_param",
    "recall",
    "qps",
    "latency_mean_us",
    "latency_p95_us",
]


def run_validation(
    ctx: RunContext,
    adapters: list[SystemAdapter],
    recall_target: float = 0.95,
    max_configs: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Build every candidate config, sweep it on the validation queries, and
    select the config with the best QPS at recall >= recall_target.

    Returns {method: selected_config}.
    """
    ctx.prepare_query_splits()
    selected: dict[str, dict[str, Any]] = {}
    for adapter in adapters:
        candidates = adapter.candidate_configs(ctx)
        if max_configs:
            candidates = candidates[:max_configs]
        best: dict[str, Any] | None = None
        best_qps_at_target = -1.0
        best_recall = -1.0
        best_recall_config: dict[str, Any] | None = None
        reached_target = False
        trials_path = ctx.csv_dir.joinpath("tuning").joinpath(
            f"{adapter.name}_validation_trials.csv"
        )
        ensure_dir(trials_path.parent)
        with trials_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TUNING_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for config in candidates:
                try:
                    ctx.phase = "val"
                    rows = adapter.search_sweep(ctx, config, repeat_id=0)
                    ctx.phase = "test"
                except Exception as exc:  # keep the pipeline alive
                    ctx.phase = "test"
                    rows = []
                    print(f"[validation] {adapter.name} {config.get('config_id')} FAILED: {exc}")
                    writer.writerow(
                        {
                            "method": adapter.name,
                            "config_id": config.get("config_id"),
                            "search_param": "FAILED",
                            "recall": 0.0,
                            "qps": 0.0,
                            "latency_mean_us": 0.0,
                            "latency_p95_us": 0.0,
                            "error": str(exc)[-500:],
                        }
                    )
                for row in rows:
                    writer.writerow({k: row.get(k, "") for k in TUNING_COLUMNS})
                    recall = float(row.get("recall", 0.0))
                    qps = float(row.get("qps", 0.0))
                    if recall >= recall_target and qps > best_qps_at_target:
                        best_qps_at_target = qps
                        best = config
                        reached_target = True
                    if recall > best_recall:
                        best_recall = recall
                        best_recall_config = config
        if not reached_target:
            # No candidate reached the recall target: pick the candidate with
            # the highest validation recall (do not keep the first candidate).
            best = best_recall_config
        if best is None and candidates:
            best = candidates[0]
        if not reached_target and best is not None:
            best = dict(best)
            best.setdefault("selection_note", f"max_validation_recall={best_recall:.4f}")
        ctx.selected_config_json(adapter.name).write_text(
            json.dumps(
                {
                    "method": adapter.name,
                    "selected_config": best,
                    "status": "ok" if best is not None else "failed",
                    "recall_target": recall_target,
                    "best_recall_at_validation": best_recall,
                    "best_qps_at_target": best_qps_at_target,
                    "candidates_tried": len(candidates),
                },
                indent=2,
            )
        )
        selected[adapter.name] = best or {}
    return selected
