"""Export completed methods from one pending-acceptance run, without rerunning queries."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def export(root):
    if (root / "EXPORTS_SUPERSEDED_BY.txt").exists():
        return
    run = json.loads((root / "run.json").read_text())
    rows, sources, errors = [], {}, {}
    for dataset in run["datasets"]:
        for method in run["methods"]:
            path = root / dataset / method / "result.json"
            if not path.exists():
                continue
            try:
                raw = path.read_bytes()
                artifact = json.loads(raw)
                summary = artifact["summary_rows"]
                if method == "Ours-Disk":
                    summary = [r for r in summary if int(r["search_width"]) >= 10]
                assert artifact["status"] == "done"
                assert artifact["dataset"] == dataset and artifact["method"] == method
                assert artifact["phase"] == run["split"]
                assert artifact["workers"] == run["workers"]
                assert artifact["cache_mode"] == run["cache_mode"]
                assert artifact["repeat_id"] == 0
                expected = [w for w in run["widths"] if method not in ("Ours-Disk", "DiskANN-PQ-Disk") or w >= 10]
                assert len(summary) == len(expected)
                assert sorted(int(r["search_width"]) for r in summary) == sorted(expected)
                for row in summary:
                    assert 0 <= float(row["recall"]) <= 1
                    assert math.isfinite(float(row["qps"])) and float(row["qps"]) > 0
                metadata = {k: v for k, v in artifact.items()
                            if not isinstance(v, (dict, list))}
                digest = hashlib.sha256(raw).hexdigest()
                sources[str(path.relative_to(root))] = digest
                for row in summary:
                    rows.append({**metadata, **row, "acceptance": "pending",
                                 "formal_ready": False, "export_run": root.name,
                                 "source_artifact": str(path.relative_to(root)),
                                 "source_sha256": digest})
            except (ValueError, KeyError, AssertionError, TypeError) as error:
                errors[str(path.relative_to(root))] = repr(error)
    out = root / "exports"
    out.mkdir(exist_ok=True)
    state = {"sources": sources, "errors": errors,
             "completed_methods": len(sources), "expected_methods": len(run["datasets"]) * len(run["methods"]),
             "rows": len(rows), "acceptance": "pending"}
    previous = out / "export_status.json"
    if previous.exists() and json.loads(previous.read_text()) == state:
        return
    if rows:
        fields = sorted(set().union(*(r.keys() for r in rows)))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        atomic_text(out / "pending_test_rows.csv", buffer.getvalue())
        plot(out, rows, run)
    atomic_text(out / "README.md", "# Pending acceptance performance figures\n\n"
                "Question: how do measured throughput, tail latency and physical read volume vary with recall?\n"
                "Design: dominant Recall-QPS panel plus p95 latency and bytes-read panels; Python/matplotlib.\n"
                "183 x 145 mm; editable SVG/PDF, 600 dpi PNG; minimum configured text size 7 pt.\n"
                "Source: pending_test_rows.csv and hashed native result.json artifacts in export_status.json.\n"
                "One test repeat (repeat_id=0), w32, C0. All measured widths retained; markers only, no interpolation,\n"
                "no averaging across widths, no error bars, no statistical tests or superiority claims.\n"
                "Each point summarizes the native query_count test queries; QPS uses native batch timing,\n"
                "not reciprocal mean latency. Latency converted us to ms; read volume bytes to MiB.\n"
                "Only complete, configuration-checked method artifacts included. Missing methods are absent, not zero.\n"
                "Rejected or incomplete artifacts are listed in export_status.json. Memory accounting and\n"
                "DiskANN widths 1-9 are unsupported (L < top-k=10), not measured or imputed; see width_support.json.\n"
                "implementation parity acceptance remain unresolved; these are NOT accepted formal results.\n")
    atomic_text(previous, json.dumps(state, indent=2) + "\n")
    print(json.dumps(state), flush=True)


def plot(out, rows, run):
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"], "font.size": 7,
                         "axes.labelsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
                         "legend.fontsize": 7, "svg.fonttype": "none", "pdf.fonttype": 42,
                         "axes.spines.top": False, "axes.spines.right": False})
    colors = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#555555"]
    markers = ["o", "s", "^", "D", "x"]
    for dataset in run["datasets"]:
        subset = [r for r in rows if r["dataset"] == dataset]
        if not subset:
            continue
        fig = plt.figure(figsize=(183 / 25.4, 145 / 25.4))
        grid = fig.add_gridspec(2, 2, height_ratios=[1.35, 1])
        axes = [fig.add_subplot(grid[0, :]), fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 1])]
        metrics = [("qps", 1, "Throughput (queries/s)"),
                   ("latency_p95_us", 1000, "p95 latency (ms)"),
                   ("bytes_read_per_query", 1048576, "Read volume (MiB/query)")]
        for method, color, marker in zip(run["methods"], colors, markers):
            points = sorted((r for r in subset if r["method"] == method), key=lambda r: r["search_width"])
            if not points:
                continue
            for ax, (metric, divisor, label) in zip(axes, metrics):
                ax.plot([r["recall"] for r in points], [r[metric] / divisor for r in points],
                        linestyle="none", marker=marker, markersize=3, color=color, label=method)
        for letter, ax, (_, _, label) in zip("abc", axes, metrics):
            ax.set(xlabel="Recall", ylabel=label, xlim=(-0.02, 1.02))
            ax.set_ylim(bottom=0)
            ax.text(-0.12, 1.04, letter, transform=ax.transAxes, weight="bold", fontsize=8)
        fig.suptitle(f"{dataset.upper()} | Pending acceptance | Single test, w32, C0", fontsize=8, y=0.98)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.935), ncol=3, frameon=False)
        fig.subplots_adjust(left=0.12, right=0.97, top=0.81, bottom=0.10, hspace=0.65, wspace=0.45)
        fig.savefig(out / f".{dataset}_performance.tmp.svg")
        fig.savefig(out / f".{dataset}_performance.tmp.pdf")
        fig.savefig(out / f".{dataset}_performance.tmp.png", dpi=600)
        for extension in ("svg", "pdf", "png"):
            (out / f".{dataset}_performance.tmp.{extension}").replace(out / f"{dataset}_performance.{extension}")
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--watch-pid", type=int)
    args = parser.parse_args()
    while True:
        export(args.root.resolve())
        if not args.watch_pid:
            break
        try:
            os.kill(args.watch_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
