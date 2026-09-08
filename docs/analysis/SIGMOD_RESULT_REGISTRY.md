# SIGMOD Result Registry

Last audited: 2026-09-07

This file is the paper-facing source of truth for dataset coverage, canonical runs, and result readiness. Plotting/report scripts should read the canonical entries below under `results/disk_environment/` instead of guessing the newest run from `results/`.

Clean results entry point:

- `results/disk_environment/`

Paper scripts should treat `results/disk_environment/` as the only result root.

Top-level cleanup status:

- Kept: `results/disk_environment/`.
- Deleted: historical compatibility symlinks (`results/agnews`, `results/dbpedia`, `results/gist`, `results/02_diskann_fair`, `results/03_system_fair`) and extra paper/archive layers.
- Mostly deleted: the old memory-result tree; any remaining files are owner/root permission residue and must not be used in paper tables.
- Pending owner/root cleanup: old `nobody:nogroup` diagnostic directories named `results/04_query_codec_1bit_scan` and `results/query_coarse_bench_gist_1bit_search*`; these are not canonical and must not be used in paper tables.

## Status Legend

| Status | Meaning |
|---|---|
| ready | Suitable for main paper after normal final checks. |
| partial | Useful but incomplete; do not use as a full method comparison. |
| legacy | Exists only in archived or old-layout results; rerun before main paper use. |
| data-only | Dataset files exist, but no canonical result exists yet. |
| smoke | Engineering test only; never use as a paper dataset. |

## Dataset Inventory

| Dataset | Base vectors | Dim | Queries | GT depth | Size on disk | Workload role | Data status |
|---|---:|---:|---:|---:|---:|---|---|
| agnews | 769,382 | 1024 | 1,000 | 100 | 3.0 GiB | medium-scale text embedding | ready data |
| dbpedia | 990,000 | 1536 | 10,000 | 100 | 5.8 GiB | medium-scale high-dimensional text embedding | ready data |
| gist | 1,000,000 | 960 | 1,000 | 100 | 3.6 GiB | classic visual descriptor | ready data |
| deep1B | 9,990,000 | 96 | 10,000 | 100 | 7.3 GiB | 10M-scale deep visual embedding | ready data |
| sift10m | 10,000,000 | 128 | 1,000 | 1,000 | 12 GiB | classic 10M ANN benchmark | ready data |
| msmarco | 113,520,750 | 1024 | 1,677 | 1,000 | 436 GiB | large-scale modern text retrieval | ready data, expensive |
| msmarco-v2.1-embed-english-v3 | raw shards | unknown here | raw queries | not converted here | 217 GiB | source material for MS MARCO variants | raw data |
| glove | 1,183,514 | 25 | 10,000 | 100 | 123 MiB | low-dimensional word embedding sanity check | needs filename/schema cleanup |
| smoke03 | 5,000 | 128 | 200 | 100 | 2.7 MiB | pipeline smoke test | smoke |

Notes:

- `glove` currently uses `glove_base.fvecscs`, not the standard `<dataset>_base.fvecs` filename expected by most scripts.
- `msmarco` is already converted to fvecs/ivecs and is the strongest available scale-out dataset, but full-baseline runs may be very expensive.

## Canonical Result Matrix

| Dataset | 01 quantizer fair | 02 shared graph / DiskANN fair | 03 disk system fair | Paper status | Main action |
|---|---|---|---|---|---|
| agnews | `results/disk_environment/01_quantizer_fair/agnews/csv/formal_test_rows.csv` | `results/disk_environment/02_diskann_fair/agnews/csv/formal_test_rows.csv` | Ours/OG-LVQ/Glass from `agnews_05c_rerun_20260901_152210`; SymphonyQG from `fix_w32_diskpayload_symphony_20260831_140957` | partial-ready | Add/confirm DiskANN-PQ in 05C, then freeze. |
| gist | `results/disk_environment/01_quantizer_fair/gist/csv/formal_test_rows.csv` | `results/disk_environment/02_diskann_fair/gist/csv/formal_test_rows.csv` | `results/disk_environment/03_system_fair/gist/csv/formal_test_rows.csv` plus detailed report inputs | partial | Rerun or verify SymphonyQG 05C sweep; current report says only a formal single point was available. |
| dbpedia | missing in current disk root | legacy archive only | legacy archive only | legacy | Rerun all disk phases using current 05 framework. |
| sift10m | missing | missing | missing | data-only | Run 05A/05B/05C next; this is the cleanest classic 10M result. |
| deep1B | missing | missing | missing | data-only | Run 05A/05B/05C after SIFT10M; use as deep visual 10M result. |
| msmarco-10m | split needed from `msmarco` | missing | missing | data-only | Create deterministic 10M subset and run full baseline suite. |
| msmarco-100m | split needed from `msmarco` | optional | missing | data-only | Use as scale-out experiment; Ours plus one or two strongest baselines is acceptable if full suite is too costly. |
| glove | missing | missing | missing | not main | Fix filename only if an appendix sanity check is needed. |
| smoke03 | diagnostics only | diagnostics only | diagnostics only | smoke | Keep for CI/smoke tests only. |

## Canonical Runs

### AGNews

Use these sources for the current paper-facing AGNews disk report:

| Layer | Canonical source | Coverage | Notes |
|---|---|---|---|
| 05A | `results/disk_environment/01_quantizer_fair/agnews/csv/formal_test_rows.csv` | PQ, SQ, SAQ, Ours | workers=1 in current aggregate. |
| 05B | `results/disk_environment/02_diskann_fair/agnews/csv/formal_test_rows.csv` | Ours, PQ, SQ, SAQ | mixed workers 16/32 appear in the file; report must state selected worker policy. |
| 05C | `results/disk_environment/.formal_runs/runs/agnews_05c_rerun_20260901_152210/05C_disk_system_fair/agnews/aggregate/formal_test_rows.csv` | Ours, OG-LVQ, Glass-NSG | 120 rows, workers=32. |
| 05C SymphonyQG | `results/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/05C_disk_system_fair/agnews/aggregate/formal_test_rows.csv` | SymphonyQG | 40 rows, workers=32. |

Known issue: the current AGNews 05C comparison in `docs/analysis/disk_w32_agnews_analysis_report*.md` does not include DiskANN-PQ in the method table, although DiskANN-PQ is a natural system baseline.

### GIST

Use these sources with caution:

| Layer | Canonical source | Coverage | Notes |
|---|---|---|---|
| 05A | `results/disk_environment/01_quantizer_fair/gist/csv/formal_test_rows.csv` | PQ, SQ, SAQ, Ours | workers=1 in current aggregate. |
| 05B | `results/disk_environment/02_diskann_fair/gist/csv/formal_test_rows.csv` | Ours, PQ, SQ, SAQ | workers=32. |
| 05C | `results/disk_environment/03_system_fair/gist/csv/formal_test_rows.csv` | DiskANN-PQ, Ours, OG-LVQ, Glass-NSG, SymphonyQG | CSV has five methods, but the hand-written report records SymphonyQG as only a formal single-point reference. Verify before main-paper use. |
| Detailed report inputs | `docs/analysis/source_query_rows_w32_gist.csv` and related source tables | Ours/OG/Glass + SymphonyQG single point | Good for diagnosis, not yet a clean final table. |

Known issue: `docs/analysis/disk_w32_gist_analysis_report_feishu_body.md` says the GIST w32 run is diagnostic and not final-paper-ready because it has only one repeat and incomplete SymphonyQG sweep.

### DBpedia

Current useful result files are only legacy disk archive records:

| Source | Status | Use |
|---|---|---|
| `results/disk_environment/archive/05_disk_system_fair_legacy/` | legacy | Rerun with the current native/direct-I/O pipeline before using in SIGMOD tables. |

## Recommended SIGMOD Dataset Set

Main paper minimum:

| Group | Datasets | Purpose |
|---|---|---|
| Classic ANN | `sift10m`, `gist` | Align with established ANN evaluations. |
| Deep visual | `deep1B` 9.99M slice | Show robustness on modern learned visual embeddings. |
| Text embeddings | `agnews`, `dbpedia` | Keep current medium-scale text coverage. |
| Retrieval / scale | `msmarco-10m` | Add a real retrieval workload with modern text embeddings. |

Scale-out figure:

| Scale | Dataset source | Methods |
|---|---|---|
| 1M | deterministic MS MARCO subset or current `agnews/dbpedia` | full suite if cheap |
| 10M | `msmarco-10m`, `sift10m`, or `deep1B` | full suite |
| 30M | MS MARCO prefix | Ours + strongest feasible baselines |
| 100M | MS MARCO prefix | Ours + one or two strongest feasible baselines |

## Next Run Queue

Run in this order to maximize paper value per machine hour:

1. `sift10m`: run 05A, 05B, 05C with all available baselines.
2. `deep1B`: run 05A, 05B, 05C with all available baselines.
3. `dbpedia`: rerun current disk suite to replace legacy archive data.
4. `gist`: repair/confirm SymphonyQG 05C sweep and repeat policy.
5. `agnews`: add DiskANN-PQ 05C or explicitly justify its absence.
6. `msmarco-10m`: create deterministic subset, compute/verify ground truth, run full suite.
7. `msmarco-100m`: run scale-out experiment with reduced baseline set if needed.

## Suggested Canonical Commands

Before running, keep each run ID stable and descriptive:

```bash
RUN_ID=sigmod_sift10m_w32_YYYYMMDD
python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase doctor --layers all --datasets sift10m \
  --ports experiments/05_disk_system_fair/ports.local.json \
  --disk-root work/05_disk_system_fair/disk_root \
  --out-root results/disk_environment \
  --workers 32 --search-dram-budget-gib 2.0
```

```bash
RUN_ID=sigmod_sift10m_w32_YYYYMMDD
python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "$RUN_ID" --layers all --datasets sift10m \
  --ports experiments/05_disk_system_fair/ports.local.json \
  --disk-root work/05_disk_system_fair/disk_root \
  --disk-profile auto \
  --out-root results/disk_environment \
  --workers 32 --repeats 1 --seed 20260813 \
  --search-dram-budget-gib 2.0
```

```bash
python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase validate --run-id "$RUN_ID" --layers all --datasets sift10m \
  --ports experiments/05_disk_system_fair/ports.local.json \
  --disk-root work/05_disk_system_fair/disk_root \
  --disk-profile auto \
  --out-root results/disk_environment \
  --workers 32 --repeats 1 --seed 20260813 \
  --search-dram-budget-gib 2.0
```

```bash
python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "$RUN_ID" --layers all --datasets sift10m \
  --ports experiments/05_disk_system_fair/ports.local.json \
  --disk-root work/05_disk_system_fair/disk_root \
  --disk-profile auto \
  --out-root results/disk_environment \
  --workers 32 --repeats 1 --seed 20260813 \
  --search-dram-budget-gib 2.0
```

For final paper runs, use `--repeats 3` once the single-repeat pass is clean.

## Cleanup Rules

- Keep `results/` top-level sparse: `results/disk_environment/` is the formal result root.
- Delete ad hoc benchmark dumps once they are not listed as canonical disk-environment sources here.
- Do not let report scripts pick "latest" by timestamp; use this registry.
- Archive data can support debugging narratives, but main-paper tables should cite only canonical current-pipeline results.
- Every table should state: dataset size, dimension, metric, query count, workers, DRAM budget, direct I/O setting, page size, repeat count, and run ID.
- A dataset is not `ready` until 05C has the agreed baseline set, parity/status is clean, and the selected recall range is comparable across methods.
