# Ours -- source map (references the repository root)

| layer | authoritative location | role |
|---|---|---|
| Algorithm core | `hnsw_rabitq/hnswlib/space_rabitq.h`, `hnsw_rabitq/hnswlib/rabitq_vamana.h` | ExRaBitQ4 codec/distance kernels; Vamana build/search with 4-bit symmetric distances |
| Framework | `baselines/diskann/` | DiskANN3 Rust crates (provider / graph / search / prune) |
| Ours-DiskANN integration | `experiments/02_diskann_fair/` | method implementation: native bridge + DiskANN3 provider runner (`--methods Ours --max-degree M`) |
| 03 adapter | `experiments/03_system_fair/adapters/ours_adapter.py` | feeds Ours M32/M64 rows into the 03 comparison |
| Method entry | `Ours/experiments/run_ours.py` | unified CLI that runs Ours-DiskANN and archives logs to `Ours/logs/<dataset>/` |

Nothing is vendored under `Ours/`; licenses and locked commits are recorded
in `Ours/NOTICE.md`.
