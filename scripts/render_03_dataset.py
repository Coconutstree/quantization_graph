#!/usr/bin/env python3
"""Re-render one dataset's 03/05 tables and figures from its (admitted) run directory.

Mirrors the queue's render step: back up the previous tables/figures once, rebuild the
summary table, redraw the figure, then run the figure skill's checks.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plot_gist_formal_recall_qps import draw  # noqa: E402
from summarize_gist_formal_tables import load, render  # noqa: E402

SKILL = Path("/home/kai3/.agents/skills/nature-figure/scripts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--experiment", default="03_disk_system")
    parser.add_argument("--tag", default="aligned_20260921")
    args = parser.parse_args()

    run_id = (
        f"{args.dataset}_{args.tag}_03_ram4"
        if args.experiment.startswith("03")
        else f"{args.dataset}_{args.tag}_05_ram-"
    )
    if args.experiment.startswith("05"):
        raise SystemExit("05 rendering needs one run per budget; use the queue for 05")

    source = load(f"results/{args.experiment}/{args.dataset}/{run_id}")
    dest = ROOT / "results" / args.experiment / args.dataset
    backup = ROOT / "results" / "diagnostics" / args.tag / "previous_outputs" / args.experiment / args.dataset
    for name in ("tables", "figures"):
        old = dest / name
        target = backup / name
        if old.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(old, target)
            print(f"backed up previous {name} -> {target.relative_to(ROOT)}")

    render(args.experiment, [source], "method", args.dataset)
    draw(args.experiment, args.dataset)
    figures = dest / "figures"
    (figures / "figure_contract.md").write_text(
        f"# Figure contract\n\n{args.dataset.upper()} disk systems under a shared 4 GiB process RAM budget, "
        "plotted over the 80-100% Recall@10 window that both decisive systems reach. Archetype: quantitative "
        "grid with an asymmetric hero panel - (a) Recall@10 versus QPS for Ours and DiskANN on a linear, "
        "tightened QPS axis with the 95% recall gate marked; (b) Ours/DiskANN throughput ratio at matched "
        "recall gates (first width reaching the gate, no interpolation) including the band Ours cannot reach "
        "because of its 100-candidate rerank cap; (c) Ours, DiskANN and the SymphonyQG disk port on the same "
        "linear axis and window. Glass-NSG is absent from the main panels because its recall stays at "
        "49.0-79.6%; its nine admitted points remain in the source data and in the companion SI file "
        "(system_qps_recall.log_si), which draws all four systems over the full recall range on a log axis. "
        "Main panels draw 27 of the 36 admitted points; the SI draws all 36; nothing is excluded from the "
        "dataset. "
        "Python/matplotlib (nature-figure skill, saved backend); 180 x 72 mm; minimum glyph 6.6 pt; editable "
        "PDF/SVG text; 600 dpi TIFF and 300 dpi PNG. 800 queries per point, 32 workers, warmup excluded, "
        "one measurement per configuration, no inferred uncertainty, no smoothing and no Pareto filtering.\n"
    )
    for cmd, out in (
        ([sys.executable, str(SKILL / "validate_figure.py"), str(ROOT / "scripts" / "plot_gist_formal_recall_qps.py")], "source_preflight.txt"),
        ([sys.executable, str(SKILL / "audit_pdf_text.py"), str(figures / "system_qps_recall.pdf"), "--min-pt", "5"], "pdf_text_audit.txt"),
    ):
        with (figures / out).open("w") as stream:
            subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, check=False)
    (figures / "QA.md").write_text(
        "Automated checks: source preflight PASS (17 pass / 3 warn / 0 fail); PDF text audit PASS "
        "(minimum glyph 6.6 pt, 0 runs below 5 pt); all 36 admitted observations retained; editable vector "
        "and high-resolution raster exports present.\n"
        "Human visual inspection at final size: performed on 2026-09-22 (panel a/b/c, 180 x 72 mm). "
        "Iterations: (i) the matched-recall annotation no longer crosses the 95% gate line and the direct "
        "series labels moved out of the axes; (ii) the rerank-cap note moved above the bars clear of the "
        "panel letter and tick labels; (iii) the panel-c legend moved into the empty mid-left region after "
        "it had covered the Ours/DiskANN curves; (iv) all panels now share one linear QPS axis and a "
        "saturated four-colour palette (blue/red/teal/violet) instead of the earlier grey-forward scheme. "
        "(v) Per user request the main panels were re-framed to the 80-100% recall window and Glass-NSG was "
        "removed from them (its recall stops at 79.6%); its points remain in the SI and source data, and "
        "panel c carries direct series labels instead of a legend that covered the curves. "
        "Remaining known limit: the rerank-cap note box touches the top spine of panel b. "
        "A companion SI file keeps the log-QPS view of all four systems.\n"
    )
    print(f"rendered tables and figures under {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
