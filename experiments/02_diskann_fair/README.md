# 02 DiskANN Fair

This runner owns the adapter slot for the second experiment:

```text
shared DiskANN/Vamana graph + shared search loop + interchangeable 4bit payload
```

Current implementation:

```text
PQ  -> shared float32 Vamana graph + FixedChunkPQTable 4bit-equivalent search
SQ  -> shared float32 Vamana graph + WithBits<4> scalar search
SAQ -> shared float32 Vamana graph + spherical Impl<4> search
LVQ -> not wired; Microsoft DiskANN has no LVQ provider in this source tree
Ours -> DiskANN3 in-memory provider + ExRaBitQ4 native bridge + residual4 block16 rerank
```

The graph is always built with float32 L2 distance through DiskANN
`FullPrecision`. The first real adapter run materializes the shared Vamana graph
under `results/${DATASET}/indexes/02_diskann_fair/shared_graph/`; later adapters
with the same dataset, `R`, `Lbuild`, `alpha`, and `seed` load and copy that
same adjacency table into their own quantized provider. Search then uses each
method's 4bit payload distance.

Graph construction uses batched `multi_insert` (`GRAPH_BUILD_BATCH_SIZE=32768`,
`IntraBatchCandidates::Max(32)`). Do **not** pass the whole dataset as one
`multi_insert` batch: DiskANN3's default `IntraBatchCandidates::All` then
evaluates every point against every other point in the batch (O(N^2) fp32 L2
computations), which is what made the original full-dataset build take
6,000-16,000 s on 1M-scale sets. The batch parameters are recorded in the shared
graph meta file and in the raw log; changing them invalidates the cached graph.

Timing fields are now honest per-run measurements:

```text
graph_build_time_ms           = this run's graph stage (build, or load+copy when reusing)
shared_graph_build_time_ms    = original fp32 shared-graph build time (from meta; absent for Ours)
graph_build_mode              = built_in_run | reused_shared
```

SQ/SAQ reuse the shared graph and do not rebuild it; their `graph_build_time_ms`
is only the load/copy cost while `shared_graph_build_time_ms` keeps the one-time
fp32 build cost for end-to-end accounting.

The real adapters stream progress into the raw log while loading data, training
the quantizer, building or loading the shared graph, and sweeping
`search_list_size`. Final logs contain graph build time, measured peak RSS,
estimated in-memory index size, recall@10, QPS, mean latency, and p95 latency.

## Ours-DiskANN migration contract

`Ours-DiskANN` is different from the PQ/SQ/SAQ fair payload adapters above. To
preserve the current `Ours/core` optimizations, it must not use the shared
float32 graph builder in `run_with_provider`. A valid Ours-DiskANN adapter has to
live inside the DiskANN3 graph/provider framework while preserving these
behaviors:

```text
build graph: ExRaBitQ4 <-> ExRaBitQ4 symmetric distance
build traversal: same current ExRaBitQ Vamana build search semantics
prune: same current RobustPrune/backedge/refine semantics with the ExRaBitQ4 distance kernel
search traversal: same current ExRaBitQ Vamana beam-search semantics
query distance: Float32 query -> ExRaBitQ4 asymmetric distance
search optimization: existing paper-prune path
rerank: existing residual4/residual8 sidecar rerank
DBpedia default rerank layout: residual_bits=4, residual_block_size=16, residual_scale_mode=mse, residual_scale_storage=fp16
framework boundary: DiskANN3 owns provider/search/prune APIs; it must not change the Ours algorithmic path
```

The legacy C++ `RaBitQVamanaIndex` path in `Ours/core/hnswlib/` is useful as
the oracle for distance kernels and result parity, but it must not be reported as
`Ours-DiskANN`: it stores a custom `VMNARBQ1` index and bypasses DiskANN3
`DataProvider`, `SearchStrategy`, and `PruneStrategy`.

Current Ours implementation status:

```text
implemented: DiskANN3 provider/prune trait integration and neighbor-provider graph storage
implemented: ExRaBitQ4 encode through native RaBitQSpace bridge
implemented: ExRaBitQ4 symmetric graph-build distance through native bridge
implemented: Ours/core native Vamana bulk/grouped-backedge/refine build imported into DiskANN3 neighbor provider
implemented: Float32 query -> ExRaBitQ4 traversal distance through native bridge
implemented: paper-prune active search loop against the DiskANN3 neighbor provider
implemented: residual4 block16 mse/fp16 rerank through native bridge
```

The formal defaults are compiled into `Args::default()`:

```text
data_root=data
out_root=results
max_degree=32
build_beam=400
alpha=1.2
search_beam_width=1
threads=1
build_threads=64
repeats=5
seed=20260813
search_list_sizes=10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460
```

Run dbpedia with all currently real DiskANN adapters:

```bash
cd <repo>
conda run -n rust-build cargo run --release \
  --manifest-path experiments/02_diskann_fair/Cargo.toml \
  --bin run_diskann_fair \
  -- \
  --dataset dbpedia \
  --methods PQ,SQ,SAQ
```

Only pass build/search flags when intentionally overriding the formal defaults.
This is a real build/search run; dbpedia can take a long time and will use a lot
of memory because the in-memory DiskANN path loads the fvecs matrix.

Important output paths:

```text
results/dbpedia/raw/02_diskann_fair/PQ/dbpedia_PQ_R32_Lbuild400.log
results/dbpedia/indexes/02_diskann_fair/shared_graph/diskann_fp32_R32_Lbuild400_alpha1.2_seed20260813.graph.bin
results/dbpedia/indexes/02_diskann_fair/shared_graph/diskann_fp32_R32_Lbuild400_alpha1.2_seed20260813.graph.json
results/dbpedia/manifests/02_diskann_fair_manifest.csv
results/dbpedia/csv/02_diskann_fair/diskann_fair_raw.csv
results/dbpedia/indexes/02_diskann_fair/PQ/PQ_bpd4_payload.json
```

Compilation note: this crate depends on local path crates under
`baselines/diskann`. If Cargo has not cached the registry dependencies yet, run
without `--offline` once in an environment that can access crates.io.
