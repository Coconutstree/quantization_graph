# Ours/core -- vendored ExRaBitQ4 algorithm core (C++ headers)

The paper method's quantization core lives here as header-only C++ (the
DiskANN3 graph/search framework itself stays in `baselines/diskann/`; the
integration bridge is `src/graph_core/native/rabitq_bridge.cpp`).

## Why this directory exists

The Ours-DiskANN runner is a Rust program, but the ExRaBitQ4 codec, distance
kernels, paper-prune factors, and symmetric Vamana build/search helpers are
implemented in C++ headers (`hnswlib/space_rabitq.h`,
`hnswlib/vamana_index.h`). Both the quantizer-fair experiment
(`legacy/01_quantizer_fair/faiss_quantizer_smoke.cpp`) and the
Ours-DiskANN native bridge include these headers directly, so they are
vendored under `Ours/core/` and are the single source of truth for the codec.

## Provenance

Vendored from the upstream `hnsw_rabitq` repository (Apache-2.0, locked
commit `c8ea9195d629e6f7140136bdb03d7b9ec5afc0df`; see `Ours/NOTICE.md`),
keeping only the `hnswlib/` headers needed to build and run the method.

## Usage

* Experiment 01 includes `hnswlib/space_rabitq.h` with include root
  `Ours/core/` (see `legacy/01_quantizer_fair/CMakeLists.txt`).
* Experiment 02 compiles `rabitq_bridge.cpp` with `-I <repo_root>` and
  includes `Ours/core/hnswlib/...` (see `src/graph_core/build.rs`).

Do not edit these headers without updating `Ours/NOTICE.md` (upstream lock).
