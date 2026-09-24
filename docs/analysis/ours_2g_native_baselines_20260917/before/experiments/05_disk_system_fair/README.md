# 05 disk-system fair suite

This directory implements the formal orchestration and audit boundary in
`docs/plans/DISK_EXPERIMENT_PROTOCOL_20260917.md`. The central rule is:

> Existing 01/02/03 disk ports preserve their original algorithms. New 05C
> baselines call official native disk implementations; their graph, codec,
> storage strategy and search parameters retain official semantics.

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
| 05C | Native disk end-to-end comparison | Ours, official DiskANN-PQ, AiSAQ and Starling |

`quantizers.py`, `exporters.py` and `disk_search.py` are retained only as
format/unit-test prototypes. No formal entry point imports them.

The current 05C protocol and memory inventory are in
[OFFICIAL_DISK_BASELINES.md](../../docs/plans/OFFICIAL_DISK_BASELINES.md).
Glass/Symphony/OG-LVQ are supplementary methods selected explicitly with
`--methods`; they are not required for the four-method primary comparison.

AiSAQ and Starling sources are pinned and their official CLIs compile and pass
small-data build/search checks. Run `python3 scripts/setup_disk_baselines.py`
to reproduce the build, then use `run_official_disk_baseline.py --help`.
Their formal registry entries remain **pending** until per-query artifacts,
whole-process memory accounting and validation-set tuning are integrated.
The standalone CLI runner records `formal_ready=false`. Search now requires a
writable delegated cgroup and defaults to a 4 GiB limit; build memory is separate.
Resource enforcement alone does not certify the native algorithm or timing.

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
- `OG-LVQ-DiskPort` is currently **blocked**: graph construction and saved rows
  come from SVS, but its distance evaluator is a local scalar LVQ decoder.
  The official default SearchBuffer is reused; official distance/search parity
  is not established. The packaged LVQ headers contain declarations without
  the implementation needed to bind disk rows to the official distance kernel.
  It must not be labelled an unchanged official algorithm.

The 2026-09-16 baseline audit repaired Glass candidate handling by directly
using upstream LinearPool and matching GraphSearcher::Search (the default
single-query API in the experiment-03 adapter). Both capacity and expansion
window are max(k, ef). Ordered top-10 agrees in 30 tied/random fixture cases.
SymphonyQG passes 126 ordered comparisons, including 48 using an index built
by the official QGBuilder with nonzero quantization codes/factors.
These checks are not full real-dataset or expansion-trace certificates.
The existing Glass binary is blocked until rebuilt and separately validated;
OG-LVQ remains blocked until the official LVQ implementation can be integrated.
See `docs/analysis/baseline_algorithm_audit_20260916/README.md` for evidence.

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

Use a fresh run ID for one fixed budget, worker count, cache policy and method set.
Measured phases require a delegated cgroup v2 parent with the memory controller
already enabled, `memory.peak`, explicit CPU IDs, and a NUMA memory node. The runner
creates only private child groups. It never enables global controllers or silently
substitutes RLIMIT_AS/RSS accounting. Set these values after hardware preflight:

```bash
python experiments/05_disk_system_fair/memory_runner.py \
  --check --cgroup-parent "$QG05_CGROUP_PARENT" --budget-gib 4

# Illustration: select methods only after their registry/algorithm audits pass.
# Reuse the same arguments for export, validate, tune and run, changing only phase.
python experiments/05_disk_system_fair/run_disk_suite.py \
  --phase tune --layer 05c --datasets gist --run-id cg4_w32_gist_v1 \
  --methods Ours-Disk --workers 32 --repeats 1 \
  --search-dram-budget-gib 4 --cache-mode standard \
  --cgroup-parent "$QG05_CGROUP_PARENT" \
  --cpu-affinity "$QG05_CPU_AFFINITY" --numa-node "$QG05_NUMA_NODE"
```

`--experiment-group budget_scan` accepts 1/2/4/8 GiB at 32 workers; create a
separate run ID and tune each budget. `--experiment-group thread_scaling` accepts
one of 1/4/8/16/32 workers at 4 GiB, again with separate tuning/run IDs. There are
no implicit GIST sensitivity runs. 05A always uses C0; system `standard` retains
native cache semantics, while `--cache-mode c0` is a separate controlled condition.

Validation and test use the same declared budget/workers. 05A test now uses only
the held-out test split. Each operating point runs once (`repeat_id=0`); report its measured QPS, Recall
and per-query latency distribution directly. No median file or cross-run IQR/CV
is generated. All widths executed in one native process
share its lifecycle memory peak, explicitly labelled; process startup/load time
is never used as query QPS time. Failed attempts retain resource evidence and
must use a fresh run ID on retry. `QG05_FAST=1` cannot emit formal tables/plots.

## Result isolation

Every formal run is immutable under `.formal_runs`; after aggregation/plotting,
CSV files, terminal logs and figures are published into the public numbered disk
environment directories:

```text
results/disk_environment/
  disk_cgroup_v2_20260917/<run-id>/01_quantizer_fair/<dataset>/{csv,logs,figures,manifests}/   # disk 05A
  disk_cgroup_v2_20260917/<run-id>/02_diskann_fair/<dataset>/{csv,logs,figures,manifests}/     # disk 05B
  disk_cgroup_v2_20260917/<run-id>/03_system_fair/<dataset>/{csv,logs,figures,manifests}/      # disk 05C
  .formal_runs/runs/<run-id>/{manifests,05A_disk_quantizer_io,05B_diskann_shared_graph,05C_disk_system_fair}/
```

Native artifacts are never appended. Aggregation is atomic and only occurs
after the complete method × storage-mode × worker × single-run matrix passes.
The plotter verifies artifact hashes and refuses smoke, memory, validation,
mixed-run or incomplete data. Method filtering happens only after evidence
validation. Published CSVs alone are insufficient; plot from the original run root.
Historical single-repeat tables remain untouched and cannot enter the new protocol.

## Figures

The plot stage writes one paper-style primary figure per disk experiment and
copies that figure into each selected dataset's published `figures/` directory:

- 05A: `disk05a_quantizer_fair_summary`, a compact summary of payload-on-disk
  matched-recall QPS and storage-invariant quantization error;
- 05B: `disk05b_shared_graph_recall_qps`, shared-graph Recall@10-QPS curves;
- 05C: `disk05c_system_recall_qps`, end-to-end disk-system Recall@10-QPS curves.

The layout follows the manuscript figures under `paper/figures`: quantitative
multi-dataset grids with a single visual claim per experiment. Auxiliary
latency, I/O, memory and sensitivity plots are not published by default. Only
measured Pareto points are drawn; Recall=0.95 markers or bars are interpolated
only from the plotted measurements. Outputs are SVG, PDF, PNG (300 dpi) and TIFF
(600 dpi).
