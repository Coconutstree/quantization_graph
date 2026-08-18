# Ours -- source map (references the repository root)

| layer | authoritative location | role |
|---|---|---|
| Algorithm core | `Ours/core/hnswlib/space_rabitq.h`, `Ours/core/hnswlib/vamana_index.h` | ExRaBitQ4 codec/distance kernels; Vamana build/search with 4-bit symmetric distances (vendored from `hnsw_rabitq`, see `Ours/NOTICE.md`) |
| Framework | `baselines/diskann/` | DiskANN3 Rust crates (provider / graph / search / prune) |
| Ours-DiskANN integration | `experiments/02_diskann_fair/` | method implementation: native bridge + DiskANN3 provider runner (`--methods Ours --max-degree M`) |
| 03 adapter | `experiments/03_system_fair/adapters/ours_adapter.py` | feeds Ours M32/M64 rows into the 03 comparison |
| Method entry | `Ours/experiments/run_ours.py` | unified CLI that runs Ours-DiskANN and archives logs to `Ours/logs/<dataset>/` |

The algorithm core is vendored under `Ours/core/hnswlib/`; licenses and
locked commits are recorded in `Ours/NOTICE.md`.
