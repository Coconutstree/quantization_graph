#!/usr/bin/env python3
"""Export the 03 system-fair comparison as paper tables (CSV/Markdown/LaTeX).

For one or more datasets (default: dbpedia,gist,agnews), reads the raw
system-fair CSV, recomputes merged/median rows, and reports per method the
QPS / p95 latency at fixed Recall@10 targets (0.90, 0.93, 0.95) plus build
time, index size, peak RSS and the selected config. Methods that cannot reach
a target are reported with their maximum recall instead of a made-up
interpolated number. A combined table across all requested datasets is also
written.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import types
from pathlib import Path

import numpy as np

if "systemfair" not in sys.modules:
    _pkg = types.ModuleType("systemfair")
    _pkg.__path__ = [
        str(Path(__file__).resolve().parents[2] / "legacy" / "03_system_fair")
    ]
    sys.modules["systemfair"] = _pkg

from systemfair.pareto_builder import median_results, merge_raw  # noqa: E402


TARGETS = [0.90, 0.93, 0.95]


def tk(t: float) -> str:
    return f"{t:.2f}"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as f:
        return list(csv.DictReader(f))


def param_num(label: str) -> float:
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", str(label))
    return float(m.group(0)) if m else 0.0


def build_table_rows(
    dataset: str, out_root: Path, suite: str
) -> list[dict[str, str]]:
    base = out_root / suite / dataset
    csv_dir = base / "csv"
    raw_path = csv_dir / "system_fair_raw.csv"
    if not raw_path.exists():
        raise FileNotFoundError(f"missing {raw_path}")
    merged_path = csv_dir / "system_fair_merged.csv"
    median_path = csv_dir / "system_fair_median.csv"
    merge_raw(raw_path, merged_path)
    median_results(merged_path, median_path)
    rows = read_csv(median_path)
    if not rows:
        raise SystemExit(f"no median rows in {median_path}")

    by_method: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by_method.setdefault(r["method"], []).append(r)

    table_rows = []
    for method in sorted(by_method):
        pts = sorted(by_method[method], key=lambda r: float(r["recall"]))
        rec = np.asarray([float(r["recall"]) for r in pts])
        qps = np.asarray([float(r["qps"]) for r in pts])
        p95 = np.asarray([float(r.get("latency_p95_us", 0) or 0) for r in pts])
        row = {
            "method": method,
            "config": pts[-1].get("config_id", ""),
            "recall_range": f"{rec[0]:.3f}..{rec[-1]:.3f}",
            "max_recall": f"{rec[-1]:.4f}",
        }
        for t in TARGETS:
            if rec[-1] < t - 1e-9:
                row[f"qps@{tk(t)}"] = "—"
                row[f"p95us@{tk(t)}"] = "—"
            else:
                row[f"qps@{tk(t)}"] = f"{np.interp(t, rec, qps):.1f}"
                row[f"p95us@{tk(t)}"] = f"{np.interp(t, rec, p95):.1f}"
        row.update(
            {
                "build_time_ms": f"{float(pts[-1].get('build_time_ms', 0) or 0):.0f}",
                "index_size_mb": f"{float(pts[-1].get('index_size_mb', 0) or 0):.1f}",
                "peak_rss_mb": f"{float(pts[-1].get('peak_rss_mb', 0) or 0):.1f}",
            }
        )
        table_rows.append(row)

    return table_rows


def write_per_dataset_outputs(
    dataset: str,
    table_rows: list[dict[str, str]],
    out_root: Path,
    suite: str,
) -> None:
    csv_dir = out_root / suite / dataset / "csv"
    csv_path = csv_dir / "system_fair_paper_table.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table_rows[0].keys()))
        w.writeheader()
        w.writerows(table_rows)

    md = ["| method | config | recall range | QPS@0.90 | QPS@0.93 | QPS@0.95 | p95us@0.95 | build_ms | idx MB | peak RSS MB |"]
    md += ["|---|---|---|---|---|---|---|---|---|---|"]
    for r in table_rows:
        md.append(
            f"| {r['method']} | {r['config']} | {r['recall_range']} | "
            f"{r['qps@0.90']} | {r['qps@0.93']} | {r['qps@0.95']} | "
            f"{r['p95us@0.95']} | {r['build_time_ms']} | {r['index_size_mb']} | "
            f"{r['peak_rss_mb']} |"
        )
    (csv_dir / "system_fair_paper_table.md").write_text("\n".join(md) + "\n")
    tex_path = csv_dir / "system_fair_paper_table.tex"
    tex_path.write_text("\n".join(tex_lines(dataset, table_rows)) + "\n")
    print(f"wrote {csv_path}")


def tex_lines(dataset: str, table_rows: list[dict[str, str]]) -> list[str]:
    tex = [
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        f"\\caption{{End-to-end system comparison on {dataset} (Recall@10, "
        "k=10, 64 threads). \\textemdash{}: method cannot reach that recall; "
        "its max recall is shown in parentheses.}",
        "\\label{tab:system_fair}",
        "\\begin{tabular}{lcccccclrr}",
        "\\toprule",
        "Method & Config & R@10 range & QPS@.90 & QPS@.93 & QPS@.95 & p95us@.95 & Build(ms) & Idx(MB) & RSS(MB) \\\\",
        "\\midrule",
    ]
    for r in table_rows:
        tex.append(
            f"{r['method']} & {r['config']} & {r['recall_range']} "
            f"({r['max_recall']}) & {r['qps@0.90']} & {r['qps@0.93']} & "
            f"{r['qps@0.95']} & {r['p95us@0.95']} & {r['build_time_ms']} & "
            f"{r['index_size_mb']} & {r['peak_rss_mb']} \\\\"
        )
    tex += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return tex


def write_combined_outputs(
    datasets: list[str],
    table_rows_by_dataset: dict[str, list[dict[str, str]]],
    out_root: Path,
    suite: str,
    paper_dir: Path,
) -> None:
    combined = []
    for dataset in datasets:
        for r in table_rows_by_dataset[dataset]:
            row = dict(r)
            row["dataset"] = dataset
            combined.append(row)

    combined_dir = out_root / "paper_tables"
    combined_dir.mkdir(parents=True, exist_ok=True)
    csv_path = combined_dir / "03_system_fair_table.csv"
    fieldnames = ["dataset"] + list(combined[0].keys() - {"dataset"})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(combined)

    md = [
        "| dataset | method | config | recall range | QPS@0.90 | QPS@0.93 | "
        "QPS@0.95 | p95us@0.95 | build_ms | idx MB | peak RSS MB |"
    ]
    md += ["|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in combined:
        md.append(
            f"| {r['dataset']} | {r['method']} | {r['config']} | "
            f"{r['recall_range']} | {r['qps@0.90']} | {r['qps@0.93']} | "
            f"{r['qps@0.95']} | {r['p95us@0.95']} | {r['build_time_ms']} | "
            f"{r['index_size_mb']} | {r['peak_rss_mb']} |"
        )
    (combined_dir / "03_system_fair_table.md").write_text("\n".join(md) + "\n")

    tex = [
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\caption{End-to-end system comparison on dbpedia, gist and agnews "
        "(Recall@10, k=10, 64 threads). \\textemdash{}: method cannot reach "
        "that recall; its max recall is shown in parentheses.}",
        "\\label{tab:system_fair_all}",
        "\\begin{tabular}{lllccccccc}",
        "\\toprule",
        "Dataset & Method & Config & R@10 range & QPS@.90 & QPS@.93 & "
        "QPS@.95 & Build(ms) & Idx(MB) & RSS(MB) \\\\",
        "\\midrule",
    ]
    for r in combined:
        tex.append(
            f"{r['dataset']} & {r['method']} & {r['config']} & "
            f"{r['recall_range']} ({r['max_recall']}) & {r['qps@0.90']} & "
            f"{r['qps@0.93']} & {r['qps@0.95']} & {r['build_time_ms']} & "
            f"{r['index_size_mb']} & {r['peak_rss_mb']} \\\\"
        )
    tex += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "system_fair_table_all.tex").write_text("\n".join(tex))
    print(f"wrote {csv_path}")
    print(f"wrote {paper_dir / 'system_fair_table_all.tex'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", help="single dataset (legacy)")
    ap.add_argument(
        "--datasets",
        default="",
        help="comma-separated datasets (default: dbpedia,gist,agnews)",
    )
    ap.add_argument("--out-root", default="results/archive/legacy_layout_20260918/disk_environment")
    ap.add_argument("--suite", default="03_system_fair")
    ap.add_argument("--paper-dir", default="paper")
    args = ap.parse_args()

    out_root = Path(args.out_root)
    suite = args.suite
    paper_dir = Path(args.paper_dir)
    datasets = (
        [args.dataset]
        if args.dataset
        else [d.strip() for d in (args.datasets or "dbpedia,gist,agnews").split(",") if d.strip()]
    )

    table_rows_by_dataset: dict[str, list[dict[str, str]]] = {}
    for dataset in datasets:
        try:
            rows = build_table_rows(dataset, out_root, suite)
        except FileNotFoundError as exc:
            print(f"skip {dataset}: {exc}")
            continue
        table_rows_by_dataset[dataset] = rows
        write_per_dataset_outputs(dataset, rows, out_root, suite)
        for r in rows:
            print(
                f"  {dataset:8s} {r['method']:12s} {r['config']:16s} "
                f"qps@.95={r['qps@0.95']:>8s} build={r['build_time_ms']:>9s} "
                f"idx={r['index_size_mb']:>8s}MB"
            )

    if len(table_rows_by_dataset) > 1:
        write_combined_outputs(
            [d for d in datasets if d in table_rows_by_dataset],
            table_rows_by_dataset,
            out_root,
            suite,
            paper_dir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
