# 05 disk-system fair suite

This directory implements the formal orchestration and audit boundary in
`DISK_SYSTEM_EXPERIMENT_PLAN.md`. The central rule is:

> Experiments 05A/05B/05C preserve the 01/02/03 graph, codec, distance kernel,
> search loop and parameter semantics. Only the storage backend changes.

The previous Python/NumPy smoke implementation violated this rule. It trained
different quantizers, used a Python graph loop, silently fell back to buffered
I/O and mixed 03 memory rows into 05C. Those outputs are marked invalid and are
never read by the formal runner or plotter.

All source implementations and local bindings resolve from the repository's
`baselines/` directory and are pinned by `baselines/DEPENDENCY_LOCK.json`.
Formal runners never fall back to sibling checkouts or `/tmp` bindings.

Build the repository-local native dependencies and experiment binaries with:

```bash
bash scripts/build_formal_local.sh
```

## Formal layers

| Layer | Source experiment | Required methods |
|---|---|---|
| 05A | 01 fixed-candidate quantizer fair | PQ-4bit, SQ-4bit, SAQ-B4, Ours RaBitQ K=1; resident and payload-on-SSD paired |
| 05B | 02 DiskANN fair | PQ/SQ/SAQ share the baseline graph; Ours retains the same ExRaBitQ-symmetric graph and search used by 02/03 |
| 05C | 03 end-to-end system fair | Ours, SymphonyQG, OG-LVQ, Glass-NSG and official DiskANN-PQ disk |

`quantizers.py`, `exporters.py` and `disk_search.py` are retained only as
format/unit-test prototypes. No formal entry point imports them.

Current 05C port status:

- `Ours-Disk` is implemented by the repository-native 05B/05C Rust port. It
  uses the same ExRaBitQ-symmetric graph and production DB1 x INT8 search as
  experiments 02/03; 05B and 05C differ only by comparison group.
- `DiskANN-PQ-Disk` is implemented through the official `diskann-disk`
  Linux `O_DIRECT` + `io_uring` search path.
- `SymphonyQG-DiskPort` is implemented by `qgraph05_symphonyqg_disk_port`.
  It builds the official QG index, keeps the official rotator/query LUT and
  FastScan distance kernel, stores each official node row on 4 KiB pages, and
  reads that row from native `O_DIRECT`/libaio when a node is expanded.
- `Glass-NSG-DiskPort` is implemented by `qgraph05_glass_disk_port`. It builds
  the official Glass NSG graph, uses the official SQ4U quantizer/distance
  kernel, stores graph rows and SQ4U codes in 4 KiB pages, and reads them with
  native `O_DIRECT`/libaio during traversal.
- `OG-LVQ-DiskPort` is implemented by `qgraph05_og_lvq_disk_port`. It uses the
  official SVS Python binding to build the Vamana+LVQ4 index, converts the
  official saved graph and LVQ4 rows into 4 KiB pages, and performs timed search
  by reading graph/LVQ rows through native `O_DIRECT`/libaio. The distance path
  is SVS LVQ4 layout-compatible and is covered by
  `tests/run_og_lvq_disk_port_parity.py`, which compares disk top-10 results
  against the official SVS memory search on the same saved index.

## Native port contract

Each method needs a native executable declared in `ports.local.json`. Start
from `ports.example.json`, but change `status` to `ready` only after the port:

- invokes the exact source kernel listed in the registry;
- stores its graph/payload under the supplied NVMe `--disk-index-dir`;
- uses 4 KiB-aligned `O_DIRECT` and native asynchronous I/O;
- keeps neither the whole graph nor the whole payload in memory;
- passes the memory-vs-disk ID/distance/recall parity gate;
- emits the version-2 result artifact and per-query trace requested by the CLI.

Every `ready` registry entry must also pin `binary_sha256` and an
`implementation_fingerprint`; the run aborts if the executable changes.

The orchestrator verifies all of those fields. Missing/scaffold/memory-only
ports fail before any benchmark is started.

For the remaining blocked external systems, a real 05C port must do more than
save an index under `--disk-index-dir`: graph neighbors and quantized payload
records must be read from 4 KiB `O_DIRECT` pages during the actual graph
traversal, and the reported per-query bytes must come from those reads.

## Commands

Use one stable run ID across all phases:

```bash
RUN_ID=formal_nvme_20260822
PORTS=experiments/05_disk_system_fair/ports.local.json
NVME=/home/msy2025/qgraph_nvme
OUT_ROOT=results/disk_environment/.formal_runs

python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase doctor --layer all --ports "$PORTS" --disk-root "$NVME"
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "$RUN_ID" --ports "$PORTS" --disk-root "$NVME" --out-root "$OUT_ROOT"
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase validate --run-id "$RUN_ID" --ports "$PORTS" --disk-root "$NVME" --out-root "$OUT_ROOT"
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase tune --run-id "$RUN_ID" --ports "$PORTS" --disk-root "$NVME" --out-root "$OUT_ROOT"
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "$RUN_ID" --ports "$PORTS" --disk-root "$NVME" --out-root "$OUT_ROOT" \
  --workers 1,32 --repeats 5
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase plot --run-id "$RUN_ID" --out-root "$OUT_ROOT"
```

Datasets run serially, so they cannot contend for the same NVMe. Method order
is rotated across repeats while 05A storage modes stay paired. The default
formal workers are `1,32`: worker 1 is for latency/P95 and worker 32 matches the
current 32-core in-memory GIST runs for QPS. GIST automatically adds workers 4,
8 and 16, DRAM budgets 1/4 GiB, and the B=2 GiB `C=0` diagnostic for 05B/05C.
The run phase refuses to start without the validation tuning lock.

## Result isolation

Every formal run is immutable under `.formal_runs`; after aggregation/plotting,
CSV files, terminal logs and figures are published into the public numbered disk
environment directories:

```text
results/disk_environment/
  01_quantizer_fair/<dataset>/{csv,logs,figures,manifests}/   # disk 05A
  02_diskann_fair/<dataset>/{csv,logs,figures,manifests}/     # disk 05B
  03_system_fair/<dataset>/{csv,logs,figures,manifests}/      # disk 05C
  .formal_runs/runs/<run-id>/{manifests,05A_disk_quantizer_io,05B_diskann_shared_graph,05C_disk_system_fair}/
```

Native artifacts are never appended. Aggregation is atomic and only occurs
after the complete method × storage-mode × worker × five-repeat matrix passes.
The plotter verifies artifact hashes and refuses smoke, memory, validation,
mixed-run or incomplete data. For `QG05_FAST=1` diagnostics, `--methods` may be
used to publish a partial curve without claiming it as a complete formal run.

## Figures

The figure types mirror the source experiments:

- 05A: fixed-candidate Recall–QPS and quantization error;
- 05B: shared-graph Recall–QPS and Recall–P95 latency;
- 05C: system Recall–QPS, Recall–P95 latency, high-recall log variants, and
  QPS–resident-memory at measured/interpolated Recall@10=0.95.

Only measured Pareto points are drawn. Recall=0.95 is interpolated only when
two measured points bracket it; otherwise that method is omitted/N/A. Outputs
are SVG, PDF, PNG (300 dpi) and TIFF (600 dpi).
