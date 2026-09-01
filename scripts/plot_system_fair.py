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
import matplotlib.ticker as mticker  # noqa: E402

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 7,
    "figure.facecolor": "#FCFCFD",
    "axes.facecolor": "#FCFCFD",
    "savefig.facecolor": "#FCFCFD",
    "text.color": "#202124",
    "axes.labelcolor": "#202124",
    "axes.edgecolor": "#7A7F87",
    "xtick.color": "#62666D",
    "ytick.color": "#62666D",
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
})

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

METHOD_COLORS = {
    "Ours": "#C2417A",
    "SymphonyQG": "#0F766E",
    "OG-LVQ": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
}
METHOD_MARKERS = {
    "Ours": "o",
    "SymphonyQG": "x",
    "OG-LVQ": "+",
    "Glass-NSG": "v",
}
METHOD_LINESTYLES = {
    "Ours": "-",
    "SymphonyQG": "--",
    "OG-LVQ": "-.",
    "Glass-NSG": ":",
}
GRID = "#DDE1E6"


def save_outputs(fig, fig_dir: Path, png_name: str) -> None:
    stem = Path(png_name).stem
    fig.savefig(fig_dir / f"{stem}.svg", bbox_inches="tight")
    fig.savefig(fig_dir / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(fig_dir / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(fig_dir / f"{stem}.tiff", dpi=600, bbox_inches="tight")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out-root", default="results/memory_environment")
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
    base = Path(args.out_root) / args.suite / args.dataset
    csv_dir = base / "csv"
    fig_dir = Path(args.out_dir) if args.out_dir else base / "figures"
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
    fallback_colors = plt.cm.tab10.colors[: len(methods)]
    style = {
        m: METHOD_COLORS.get(m, fallback_colors[i])
        for i, m in enumerate(methods)
    }

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
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        for m in methods:
            pts = [r for r in rows if r["method"] == m]
            pts.sort(key=lambda r: _param_num(r["search_param"]))
            x = [float(r[xkey]) for r in pts]
            y = [float(r[ykey]) for r in pts]
            ax.plot(
                x,
                y,
                marker=METHOD_MARKERS.get(m, "o"),
                markersize=3.8 if m == "Ours" else 2.8,
                linewidth=2.0 if m == "Ours" else 1.0,
                linestyle=METHOD_LINESTYLES.get(m, "-"),
                alpha=1.0 if m == "Ours" else 0.8,
                color=style[m],
                label=m,
                zorder=5 if m == "Ours" else 2,
            )
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        if title:
            ax.set_title(title)
        if logx:
            if any(value <= 0 for value in x):
                raise ValueError(f"log-x values must be positive for {fname}")
            ax.set_xscale("log")
        if logy:
            if any(value <= 0 for value in y):
                raise ValueError(f"log-y values must be positive for {fname}")
            ax.set_yscale("log")
            # Avoid mathtext exponent glyphs shrinking below the 5 pt PDF
            # floor at the final 183 mm figure width.
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y:g}"))
            ax.yaxis.set_minor_formatter(mticker.NullFormatter())
        if xlim:
            ax.set_xlim(*xlim)
        for r in ref_recalls or []:
            ax.axvline(r, color="grey", linestyle=":", linewidth=1.0, alpha=0.8)
            ax.text(
                r,
                ax.get_ylim()[1],
                f" R@10={r:.2f}",
                ha="left",
                va="top",
                fontsize=8,
                color="grey",
            )
        ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
        if logy:
            legend_loc = "lower left" if ykey == "qps" else "upper left"
            ax.legend(loc=legend_loc, bbox_to_anchor=(0.01, 0.99), borderaxespad=0.0)
        else:
            ax.legend(loc="upper right")
        fig.tight_layout()
        save_outputs(fig, fig_dir, fname)
        plt.close(fig)

    draw(
        "recall", "qps", "Recall@10", "QPS", "system_qps_recall.png",
        "Recall@10 vs QPS · Ours uses DB1 × INT8 query",
    )
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
        "Recall@10 vs P95 latency · Ours uses DB1 × INT8 query",
    )
    draw(
        "recall",
        "qps",
        "Recall@10",
        "QPS",
        "system_qps_recall_logy_highrecall.png",
        "High-recall QPS · Ours uses DB1 × INT8 query",
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
        "High-recall P95 latency · Ours uses DB1 × INT8 query",
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
    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    for m in methods:
        pt = next((r for r in at95 if r["method"] == m), None)
        if pt is None:
            continue
        ax.scatter(
            float(pt["index_size_mb"]),
            float(pt["qps"]),
            color=style[m],
            s=90 if m == "Ours" else 60,
            label=f"{m} (qps={float(pt['qps']):.0f})",
        )
    ax.set_xlabel("Index size (MB) at Recall@10=0.95")
    ax.set_ylabel("QPS at Recall@10=0.95")
    ax.grid(axis="both", color=GRID, linewidth=0.7, zorder=0)
    ax.legend()
    fig.tight_layout()
    save_outputs(fig, fig_dir, "system_qps_memory_at_95recall.png")
    plt.close(fig)

    print(f"figures written to {fig_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
