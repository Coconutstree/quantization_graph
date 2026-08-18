#!/usr/bin/env python3
"""Generate the 03 system-fair figures."""

from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib_config")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "experiments" / "03_system_fair")]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import (  # noqa: E402
    interpolate_targets,
    median_results,
    merge_raw,
    read_csv,
)

import re  # noqa: E402


def _param_num(label: str) -> float:
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", str(label))
    return float(m.group(0)) if m else 0.0


PLANNED = {
    "system_qps_recall.png",
    "system_p95latency_recall.png",
    "system_qps_memory_at_95recall.png",
    "system_qps_recall_logy_highrecall.png",
    "system_p95latency_recall_logy_highrecall.png",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results")
    ap.add_argument("--suite", default="03_system_fair")
    ap.add_argument(
        "--out-dir",
        default="",
        help="write figures here instead of results/<dataset>/figures/<suite>",
    )
    ap.add_argument(
        "--exclude-methods",
        default="",
        help="comma-separated methods to exclude from the figures",
    )
    args = ap.parse_args()
    base = Path(args.out_root) / args.dataset
    csv_dir = base / "csv" / args.suite
    fig_dir = Path(args.out_dir) if args.out_dir else base / "figures" / args.suite
    fig_dir.mkdir(parents=True, exist_ok=True)
    exclude = {m.strip() for m in args.exclude_methods.split(",") if m.strip()}

    # Always recompute from the authoritative raw CSV: rows may have been
    # appended later (e.g. an Ours-only rerun), so a stale median would
    # silently drop methods from the figures.
    merge_raw(csv_dir / "system_fair_raw.csv", csv_dir / "system_fair_merged.csv")
    median_results(csv_dir / "system_fair_merged.csv", csv_dir / "system_fair_median.csv")
    if args.out_dir:
        # custom output: compute interpolation in a scratch file only
        _interp_tmp = fig_dir / "_interp_tmp.csv"
        interpolate_targets(csv_dir / "system_fair_median.csv", _interp_tmp)
        interp_rows = read_csv(_interp_tmp)
        _interp_tmp.unlink(missing_ok=True)
    else:
        interpolate_targets(
            csv_dir / "system_fair_median.csv",
            csv_dir / "system_fair_interpolated.csv",
        )
        interp_rows = read_csv(csv_dir / "system_fair_interpolated.csv")
    rows = read_csv(csv_dir / "system_fair_median.csv")
    if not rows:
        raise SystemExit("no median rows to plot")
    if exclude:
        rows = [r for r in rows if r["method"] not in exclude]
        interp_rows = [r for r in interp_rows if r["method"] not in exclude]
    if not rows:
        raise SystemExit("no median rows left after exclusion")

    methods = sorted({r["method"] for r in rows})
    colors = plt.cm.tab10.colors[: len(methods)]
    style = {m: colors[i] for i, m in enumerate(methods)}

    def draw(
        xkey,
        ykey,
        xlabel,
        ylabel,
        fname,
        title=None,
        logx=False,
        logy=False,
        xlim=None,
        ref_recalls=None,
    ):
        if fname not in PLANNED:
            return
        plt.figure(figsize=(8, 6))
        for m in methods:
            pts = [r for r in rows if r["method"] == m]
            pts.sort(key=lambda r: _param_num(r["search_param"]))
            x = [float(r[xkey]) for r in pts]
            y = [float(r[ykey]) for r in pts]
            plt.plot(x, y, "-o", ms=3, color=style[m], label=m)
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        if title:
            plt.title(title)
        if logx:
            plt.xscale("log")
        if logy:
            plt.yscale("log")
        if xlim:
            plt.xlim(*xlim)
        for r in ref_recalls or []:
            plt.axvline(r, color="grey", linestyle=":", linewidth=1.0, alpha=0.8)
            plt.text(
                r,
                plt.ylim()[1],
                f" R@10={r:.2f}",
                ha="left",
                va="top",
                fontsize=8,
                color="grey",
            )
        plt.grid(alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(fig_dir / fname, dpi=150)
        plt.close()

    draw("recall", "qps", "Recall@10", "QPS", "system_qps_recall.png", "System QPS-Recall")
    draw(
        "recall",
        "index_size_mb",
        "Recall@10",
        "Index size (MB)",
        "system_memory_recall.png",
        "System Memory-Recall",
    )
    draw(
        "recall",
        "latency_p95_us",
        "Recall@10",
        "P95 latency (us)",
        "system_p95latency_recall.png",
        "System P95-Latency",
    )
    draw(
        "recall",
        "qps",
        "Recall@10",
        "QPS",
        "system_qps_recall_logy_highrecall.png",
        "System QPS-Recall (high-recall zoom, log scale)",
        logy=True,
        xlim=(0.80, 1.0),
        ref_recalls=[0.90, 0.95],
    )
    draw(
        "recall",
        "latency_p95_us",
        "Recall@10",
        "P95 latency (us)",
        "system_p95latency_recall_logy_highrecall.png",
        "System P95-Latency (high-recall zoom, log scale)",
        logy=True,
        xlim=(0.80, 1.0),
        ref_recalls=[0.90, 0.95],
    )
    draw(
        "recall",
        "build_time_ms",
        "Recall@10",
        "Build time (ms)",
        "system_buildtime_ms.png",
        "System Build Time",
        logx=True,
    )
    draw(
        "recall",
        "index_size_mb",
        "Recall@10",
        "Index size (MB)",
        "system_index_size.png",
        "System Index Size",
    )

    # QPS at 95% recall vs index size
    at95 = [r for r in interp_rows if abs(float(r.get("recall_target", 0)) - 0.95) < 1e-9]
    plt.figure(figsize=(8, 6))
    for m in methods:
        pt = next((r for r in at95 if r["method"] == m), None)
        if pt is None:
            continue
        plt.scatter(
            float(pt["index_size_mb"]),
            float(pt["qps"]),
            color=style[m],
            s=80,
            label=f"{m} (qps={float(pt['qps']):.0f})",
        )
    plt.xlabel("Index size (MB) at Recall@10=0.95")
    plt.ylabel("QPS at Recall@10=0.95")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(fig_dir / "system_qps_memory_at_95recall.png", dpi=150)
    plt.close()

    print(f"figures written to {fig_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
