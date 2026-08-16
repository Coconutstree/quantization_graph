# quantization_graph

Standalone home for the paper's main method package, copied from
`quantized_hnsw/Ours`. See `Ours/README.md` for the full method description.

## Method

**Ours-DiskANN**: ExRaBitQ4 4-bit symmetric Vamana construction inside the
DiskANN3 framework, paper-pruned search, residual4 rerank. The method is
always measured at **M=32** and **M=64** for every dataset.

## Layout

```text
.
├── Ours/       # method package: run_ours.py + config + tests + logs
├── LICENSE     # Apache-2.0
└── .gitignore
```

## Reproduce

```bash
# unit tests (standard library + numpy)
python -m unittest discover -s Ours/tests

# run Ours-DiskANN at M=32 and M=64 (requires the quantized_hnsw repo root)
python Ours/experiments/run_ours.py --dataset dbpedia --M 32,64
```

## Dependencies on the quantized_hnsw repository root

`Ours/` is a light method package: it does not vendor the C++/Rust
implementation. `Ours/CORE_SOURCES.md` and `Ours/NOTICE.md` map each layer to
its authoritative location in `quantized_hnsw`:

| layer | authoritative location |
|---|---|
| Algorithm core | `hnsw_rabitq/` |
| Framework | `baselines/diskann/` |
| Ours-DiskANN integration | `experiments/02_diskann_fair/` |
| 03 adapter | `experiments/03_system_fair/adapters/ours_adapter.py` |

`Ours/experiments/run_ours.py` resolves the repository root as its parent
directory and expects `experiments/02_diskann_fair/target/release/run_diskann_fair`
plus `data/` and `results/` to exist there.
