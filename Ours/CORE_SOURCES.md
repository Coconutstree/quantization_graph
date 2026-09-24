# Ours source map

| Role | Source |
|---|---|
| Quantization and Vamana | `Ours/core/hnswlib/` |
| Graph construction and Rust bridge | `src/graph_core/` |
| Disk search and I/O | `experiments/02_disk_shared_graph/native/`, `src/disk_bench/native/` |
| Disk experiment entry points | `experiments/01_disk_quantizer/`, `experiments/02_disk_shared_graph/`, `experiments/03_disk_system/` |
| Framework | `baselines/diskann/` |

The old `legacy/ours_experiments/run_ours.py` drives a historical resident runner; it is not the public disk experiment entry. See the root README for disk reproduction.
