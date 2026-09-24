#!/usr/bin/env python3
"""Replace only Ours/DiskANN rows in the GIST 03 tables with the current pair run."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "disk_bench"))
import orchestrator  # noqa: F401  (installs diskfair package alias)
from diskfair.native_contract import flatten_artifact

TARGET = ROOT / "results/03_disk_system/gist"
RUN = TARGET / "gist_aligned_20260921_03_ram4"
PAIR = TARGET / "gist_aligned_20260922_03_pair"
METHODS = {"Ours-Disk", "DiskANN-PQ-Disk"}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    formal = RUN / "tables/formal_test_rows.csv"
    old = read_rows(formal)
    fields = list(old[0])
    replacement: dict[str, list[dict[str, str]]] = {}
    for method in sorted(METHODS):
        path = next((PAIR / f"raw/{method}/test").glob("*reference/result.json"))
        artifact = json.loads(path.read_text())
        rows = flatten_artifact(path, artifact)
        replacement[method] = [{k: str(row.get(k, "")) for k in fields} for row in rows]

    merged = [row for row in old if row["method"] not in METHODS]
    for method in ("Ours-Disk", "DiskANN-PQ-Disk"):
        merged.extend(replacement[method])
    merged.sort(key=lambda r: (r["method"], int(r["search_width"])))
    with formal.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)

    manifest_path = formal.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update({
        "formal_ready": False,
        "rows": len(merged),
        "source_pair_run_id": "gist_aligned_20260922_03_pair",
        "replacement_note": (
            "Ours-Disk and DiskANN-PQ-Disk replaced with current pair reference artifacts; "
            "external parity was stopped by user request, so these rows are diagnostic and not formal-ready."
        ),
    })
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    summary = TARGET / "tables/recall_qps_summary.csv"
    summary_fields = list(read_rows(summary)[0])
    old_summary = read_rows(summary)
    new_summary = [r for r in old_summary if r["method"] not in METHODS]
    for method in ("Ours-Disk", "DiskANN-PQ-Disk"):
        for row in replacement[method]:
            new_summary.append({k: row.get(k, "") for k in summary_fields})
    new_summary.sort(key=lambda r: (r["method"], int(r["search_width"])))
    with summary.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(new_summary)
    md = [
        "# GIST 03 Recall–QPS summary",
        "",
        "Current pair reference diagnostic: 4 GiB, beam 4, 32 workers, 800 test queries per width.",
        "Ours-Disk and DiskANN-PQ-Disk are from `gist_aligned_20260922_03_pair`; Glass and SymphonyQG are preserved from the previously admitted GIST 03 run.",
        "External parity for the new pair was stopped by user request; these replacement rows are therefore marked `formal_ready=false` and are not a formal admission claim.",
        "",
        "| method | width | Recall@10 | QPS |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in new_summary:
        md.append(f"| {row['method']} | {row['search_width']} | {float(row['recall'])*100:.3f}% | {float(row['qps']):.3f} |")
    (TARGET / "tables/recall_qps_summary.md").write_text("\n".join(md) + "\n")
    (TARGET / "tables/recall_qps_summary.sources.json").write_text(json.dumps({
        "source_pair_run": "results/03_disk_system/gist/gist_aligned_20260922_03_pair",
        "replaced_methods": sorted(METHODS),
        "preserved_methods": ["Glass-NSG-DiskPort", "SymphonyQG-DiskPort"],
        "formal_ready": False,
        "external_parity": "stopped by user request",
    }, indent=2) + "\n")
    print(f"wrote {formal} and {summary}; rows={len(merged)}")


if __name__ == "__main__":
    main()
