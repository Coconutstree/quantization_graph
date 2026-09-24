#!/usr/bin/env python3
"""Plot completed GIST pair reference artifacts as a provisional diagnostic figure."""
from pathlib import Path
import csv, json, glob

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/03_disk_system/gist/gist_aligned_20260922_03_pair"
OUT = ROOT / "results/diagnostics/aligned_20260921/gist_pair_20260922"
OUT.mkdir(parents=True, exist_ok=True)

rows = []
for method in ("Ours-Disk", "DiskANN-PQ-Disk"):
    paths = glob.glob(str(RUN / "raw" / method / "test" / "*reference" / "result.json"))
    if len(paths) != 1:
        raise SystemExit(f"expected one reference result for {method}, found {len(paths)}")
    data = json.loads(Path(paths[0]).read_text())
    for item in data["summary_rows"]:
        rows.append({
            "method": method,
            "width": item["search_width"],
            "recall_at_10": item["recall"],
            "qps": item["qps"],
            "source": paths[0],
            "formal_ready": data.get("formal_ready", False),
        })

with (OUT / "reference_rows.csv").open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

fig, ax = plt.subplots(figsize=(7.2, 5.0), dpi=180)
styles = {"Ours-Disk": ("#1677b3", "o"), "DiskANN-PQ-Disk": ("#d46b2c", "s")}
for method, (color, marker) in styles.items():
    part = sorted((r for r in rows if r["method"] == method), key=lambda r: r["width"])
    ax.plot([r["recall_at_10"] for r in part], [r["qps"] for r in part],
            marker=marker, linewidth=2, markersize=5, color=color, label=method)
    for r in part:
        ax.annotate(str(r["width"]), (r["recall_at_10"], r["qps"]),
                    xytext=(3, 3), textcoords="offset points", fontsize=7, color=color)
ax.set_xlabel("Recall@10")
ax.set_ylabel("QPS")
ax.set_yscale("log")
ax.grid(True, which="both", alpha=0.25)
ax.legend(frameon=False)
ax.set_title("GIST 03 pair reference diagnostic\nexternal parity incomplete; not formal-ready")
fig.tight_layout()
fig.savefig(OUT / "recall_qps_reference_diagnostic.png", dpi=300)
fig.savefig(OUT / "recall_qps_reference_diagnostic.svg")
fig.savefig(OUT / "recall_qps_reference_diagnostic.pdf")
print(OUT)
