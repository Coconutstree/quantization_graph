# Ours -- method package

The repository root is the **core code directory**; this folder is the
**method package**. The paper's method is **Ours-DiskANN**: ExRaBitQ4 4-bit
symmetric Vamana construction inside the DiskANN3 framework, paper-pruned
search, residual4 rerank.

**Measurement rule:** Ours-DiskANN is always measured at **M=32 and M=64**
for every dataset (two symmetric 4-bit Vamana constructions). Experiment 02
reports **M=32** (degree-matched with the R=32 shared graph), experiment 03
reports **M=64** (full-system configuration). The 03 adapter lives in
`experiments/03_system_fair/adapters/ours_adapter.py` and reads the M=64
rows produced by `Ours/experiments/run_ours.py --M 32,64`.

**Formal default:** ExRaBitQ uses **K=1 centroid** across the whole pipeline
(01 quantizer comparison and 02/03 Ours-DiskANN symmetric Vamana share the
same K). K>1 multi-centroid support is reserved for future work.

## Layout

```text
Ours/
  experiments/
    run_ours.py            # unified entry: run Ours-DiskANN for --M 32|64
  config.json              # formal configuration (R=L_build=400, ...)
  logs/<dataset>/          # main-method logs (R32/R64 runs)
  tests/                   # unit tests (standard library)
  pyproject.toml / requirements.txt
  README.md / CORE_SOURCES.md / NOTICE.md
```

## Reproduce

```bash
# run Ours-DiskANN at M=32 and M=64 (default), or pick one:
python Ours/experiments/run_ours.py --dataset dbpedia --M 32,64
python Ours/experiments/run_ours.py --dataset dbpedia --M 32

# experiment 03 (Ours is included via its adapter at M=32/M=64)
python experiments/03_system_fair/run_system_fair.py \
  --dataset dbpedia --systems Ours,SymphonyQG,OG-LVQ,Glass-NSG \
  --validate --run --repeats 1 --threads 64

# tests
python -m unittest discover -s Ours/tests
```

## Dependencies on the repository root

* Ours-DiskANN binary: `experiments/02_diskann_fair/target/release/run_diskann_fair`
  (the 02 runner is reused as the method implementation; 02's own comparison
  does not list Ours).
* Algorithm core / framework: `hnsw_rabitq/` and `baselines/diskann/`.
