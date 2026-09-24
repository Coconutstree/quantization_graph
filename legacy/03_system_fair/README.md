# 03 system-fair experiment (end-to-end systems)

Python equivalent of the C++ framework listed in
`docs/plans/BASELINE_EXPERIMENT_PLAN_MS_V2.md` §0.4. Five full ANN systems are compared
end-to-end on the same data, with all systems quantized to a unified 4-bit
payload where their official implementation supports it:

| system | implementation | 4-bit path |
|---|---|---|
| Ours | `legacy/ours_experiments/run_ours.py` → `src/graph_core/target/release/run_diskann_fair` | ExRaBitQ4 symmetric Vamana + DB1 coarse gate + INT8 query reused by residual4 rerank, M=32/64, L_build=400, efSearch sweep |
| SymphonyQG | official python binding (SIGMOD'25) | native QG quantization |
| NGT-QG | official Yahoo Japan NGT `qbg` CLI | native QG quantization |
| OG-LVQ | official Intel SVS python bindings | LVQ4 (4 bit/dim) Vamana |
| Glass-NSG | official pyglass 2.1.0 | NSG + SQ4U quantized search |

## Layout

```text
legacy/03_system_fair/
  run_system_fair.py        # runner (equivalent of run_system_fair.cpp)
  system_adapter.py         # adapter interface (equivalent of system_adapter.h/cpp)
  validation_tuner.py       # validation-based config selection
  pareto_builder.py         # merge -> median -> interpolate pipeline
  adapters/*_adapter.py     # one per system
  adapters/_scripts/*.py    # worker scripts invoked by adapters
```

## Run

```bash
# validation tuning + full sweeps for dbpedia (defaults: 1 repeat)
python legacy/03_system_fair/run_system_fair.py --dataset dbpedia \
  --validate --run --repeats 1

# post-processing (CSVs, figures, audit)
python scripts/plot_system_fair.py --dataset dbpedia
python scripts/generate_experiment_audit.py --dataset dbpedia
```

Outputs follow the plan's `0.7` layout:
`results/<dataset>/{manifests,indexes,raw,csv,figures,audit}/03_system_fair/...`.

Raw logs are unified: each method writes a single file
`results/<dataset>/raw/03_system_fair/<method>/<method>.log` containing a
header (build time ms, graph build time ms, index size MB, peak RSS MB,
M/R, efConstruction/W/EF, nominal bpd, threads) plus one block per search
parameter (recall / QPS / mean / p50 / p95 latency). Per-parameter files
from earlier runs can be merged with:

```bash
python scripts/consolidate_system_logs.py --dataset dbpedia
```

## Known status

* Ours uses the ExRaBitQ4 symmetric Vamana build/search path from
  `Ours/core/hnswlib/` inside the DiskANN3 framework (see
  `src/graph_core/`). Formal rows use only the DB1 x INT8-query
  codec; full, b1, and INT4 queries are retained solely in codec ablations.
* NGT-QG: two fixes were required. (1) `ngt`/`qbg` indexes created from TSV
  use 1-based object ids; the adapter subtracts 1 so recall is computed
  against the 0-based ground truth. (2) The QG default quantization is
  16 centroids (4 bits) per 1-dim subvector, so with a small result set the
  quantized search collapses early; the adapter therefore sweeps the native
  result-expansion parameter `-p` (internal candidate pool = k * p, exact
  rerank on the pool) at k=10 / epsilon=0.1, which produces a real
  Recall-QPS curve (smoke: 0.008 -> 1.0).
