"""Summarize isolated disk pilot JSONs; preserve every configuration, not just winners."""
import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
args = parser.parse_args()
rows = []
for path in sorted(args.directory.glob('*.json')):
    doc = json.loads(path.read_text())
    if not isinstance(doc, dict) or 'summary_rows' not in doc:
        continue
    for r in doc['summary_rows']:
        rows.append(dict(variant=path.stem, queries=r['query_count'],
                         recall_at_10=r['recall'], qps=r['qps'],
                         io_per_query=r['io_requests_per_query'],
                         full4_candidates=r['full4_candidates'],
                         codec_mib=doc['resident_bytes'] / 2**20,
                         peak_rss_mib=r['peak_rss_bytes'] / 2**20,
                         budget_gib=doc['search_dram_budget_gib'],
                         route_dim=doc.get('adaptive_route_dim', 0),
                         route_keep=doc.get('adaptive_route_keep', 0),
                         route_ratio=doc.get('adaptive_route_ratio', 0),
                         norm_correction=bool(doc.get('adaptive_route_norms', '')),
                         scale_calibration=doc.get('adaptive_route_calibrate', '0')))
if not rows:
    raise SystemExit('No completed results found')
with (args.directory / 'summary.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
for r in rows:
    print(f"{r['variant']:14s} Recall@10={r['recall_at_10']:.4f} "
          f"QPS={r['qps']:.2f} I/O={r['io_per_query']:.1f} "
          f"codec={r['codec_mib']:.1f} MiB RSS={r['peak_rss_mib']:.1f} MiB")
print('CSV:', args.directory / 'summary.csv')
