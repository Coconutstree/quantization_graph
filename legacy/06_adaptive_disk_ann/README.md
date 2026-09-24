# Adaptive 1-bit routing-code pilot

The old PCA/NNP projection prototype, its encoding entry points, brute-force
route/rerank script, and dedicated Python tests were removed on 2026-09-21.
The results and remaining tools below are historical; runners require existing
projection/code artifacts and no longer provide a way to regenerate them here.

## GIST error-source ablation (2026-09-11)

See `results/disk_environment/06_adaptive_disk_ann/gist/disk_replacement_20260911_071515_723405781/ABLATION_REPORT.md`.
The 100-query diagnostic separates exhaustive original/projected/1-bit ranking
from actual graph-search experiments. Full4 without lowdim screening restores
recall at substantially higher I/O; allowing rejected nodes to be reconsidered
recovers only part of the screening loss. Exhaustive-ranking coverage is not
reported as graph-search recall or as an additive causal error decomposition.

`diagnose_route_ranking.py` streams the full base and reports GT10 coverage
among top10/top49 candidates. Projected floating-point rows are temporary
batches, not a deployable low-memory ANN representation. New `_rv` variants
enable optional reconsideration of lowdim-rejected nodes; default behavior
is unchanged. Reproduce the graph pilot with:

```bash
python legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py --dataset gist --route-dir results/disk_environment/06_adaptive_disk_ann/gist/cross_dataset_v2/codes_nnp --queries 100 --query-offset 100 --budget-gib 1 --widths 49 --variants baseline,d256,d256_k32_t100,d256_k32_t100_rv
```

## Cross-dataset Recall–QPS pilot (GIST and DBpedia)

Completed results are in
`results/disk_environment/06_adaptive_disk_ann/cross_dataset_curves_20260910_224730/`:
`VALIDATION_REPORT.md`, `recall_qps.pdf`/`.svg`/`.png`, `source_data.csv`, and
`QA_NOTES.md`. Both datasets fell back to original Ours under the validation
recall threshold; plotted lowdim curves are diagnostics. GIST degraded in
recall and throughput at all three matched widths. DBpedia width49 retained
nearly the same test recall with lower peak RSS, but lower QPS; test outcomes
were not used to override the frozen fallback. All 12 curve points passed
the data audit, and PDF text met the 5-pt glyph floor.

The removed preparation script fitted each dataset's projection on a seeded random
4096-row base-only sample, then encoded the full base in bounded batches.
It corrected a negative-pair bug in `_local_neighbour_pairs`: the distance
diagonal was infinity during argmax, so every negative was the anchor itself.
Old NNP ordering consequently had zero negative separation scores and could
degenerate to the original PCA component order. New v2 artifacts do not
overwrite old AGNews codes. Historical AGNews results are not evidence of
the corrected NNP ordering's benefit.

The cross-dataset curve protocol fixes 1 GiB accounting budget, workers=32,
beam=1 and each dataset's existing own R64/Lbuild400 graph/disk index.
Validation queries 0–99 at width49 compare full DB1 and d64/128/256/512 with
keep32/ratio1, allowing absolute Recall@10 loss 0.001. A frozen candidate is
tested on queries 100–999 at widths16/49/96, with one run per point. DBpedia's
remaining 9000 queries are excluded from this equal-query-count pilot, not
from a claimed full test. Original data and indices remain unchanged.

If automatic selection returns the original full-DB1 baseline, the best
validation-recall lowdim candidate is still plotted as **Diagnostic**, never
as a successful automatic selection. This prevents hiding unsuccessful
cross-dataset generalization behind a fallback to the baseline. Test results
are never used to change the frozen candidate.

```bash
# Requires the previously generated v2 projection/code artifacts.
python legacy/06_adaptive_disk_ann/run_cross_dataset_curves.py
python legacy/06_adaptive_disk_ann/plot_cross_dataset_curves.py RESULTS_DIRECTORY
```

The curve runner currently expects those v2 route directories. It writes
dataset validation logs, frozen decisions, raw test artifact links and all
12 curve points to `source_data.csv`. Plotting uses the saved Python backend,
editable PDF/SVG plus a 600-dpi PNG preview. Lines connect width-ordered
measurements, without smoothing or interpolation; no error bars are invented
for single-run throughput. Hard memory enforcement and formal submission
readiness are not claimed.

## Budget-driven selection workflow (AGNews native disk integration)

`run_adaptive_selection.py` profiles `baseline` (full DB1) and
`d64/d128/d256/d512_k32_t100` on the first 100 queries, using the same graph,
width=49, beam=1 and workers=32. It freezes the fastest eligible configuration
per budget before evaluating the remaining 900 queries. The code dimensions
are pre-encoded; this selects one resident representation at startup, not
per-query dimensionality switching. Keep=32 and ratio=1 are fixed in this scan.

```bash
python legacy/06_adaptive_disk_ann/test_route_selector.py
python legacy/06_adaptive_disk_ann/run_adaptive_selection.py --budgets 0.3,0.375,0.5 --max-recall-drop 0.001
```

The tolerance is an absolute Recall@10 difference (0.001 = 0.1 percentage
points), not a relative percentage. Baseline recall at 0.5 GiB supplies the
validation target even if full DB1 cannot be admitted at a smaller budget.
Baseline itself remains an eligible choice when it fits and is fastest.
Timing profiles are never transferred between budgets, because cache capacity
may differ. When no tested candidate passes recall, accounted memory, and
observed peak RSS, the decision is `no_feasible_config`. This means no feasible
member of the tested grid, not a proof that every possible ANN method fails.

Each run creates an isolated `adaptive_selection_*` result directory with:

- `config.json`: budgets and tolerance;
- `validation.json`: metrics and links to exact native commands/logs;
- `frozen_selection.json`: decisions persisted before test execution;
- `test.json`: frozen choices tested without reselecting on test outcomes;
- per-configuration driver logs, including explicit budget-admission failures.

Limitations: the existing query slices were used in earlier development and
are not an untouched final test set. This demonstrates procedural separation,
not absence of historical test leakage. Single-run QPS cannot establish a
statistically stable winner. Cgroup files are not writable in this environment;
accounting and measured RSS are **not** a hard memory-limit experiment. No
RLIMIT_AS substitute is presented as an RSS limit. The generic selector can
consume other datasets' profiles, but this runner currently targets AGNews.

The earlier prototype descriptions below are retained for artifact generation.

Reuse a completed validation archive without running searches again:

```bash
python legacy/06_adaptive_disk_ann/select_route_profile.py --profile-dir results/disk_environment/06_adaptive_disk_ann/agnews/adaptive_selection_20260910_165710 --budget-gib 0.375
```

The command prints a JSON decision; optional `--output NEW_FILE.json` saves it
without overwriting prior decisions. It reads validation metrics only, never
`test.json`. An unseen budget returns `unprofiled_budget`, whereas a tested
budget with no qualifying candidate returns `no_feasible_config`. Changing
`--max-recall-drop` makes a new selection and requires separate validation;
it does not retroactively change the previous frozen experiment.

### Completed selection run: 2026-09-10

Artifacts: `results/disk_environment/06_adaptive_disk_ann/agnews/adaptive_selection_20260910_165710/`.
Validation baseline Recall@10 was 0.992, hence the configured minimum was
0.991. d64/d128/d256 with keep32/ratio1 scored 0.975/0.984/0.989 and were
rejected by recall. d512 scored 0.992. Both baseline and d512 were rejected
by accounting admission at 0.3 GiB; this rejection is expected, not a crash.

| Budget GiB | Frozen choice | Test Recall@10 | Test QPS | Peak RSS MiB |
|---:|---|---:|---:|---:|
| 0.3 | No feasible member of tested grid | — | — | — |
| 0.375 | d512, keep32, ratio1 | 0.989556 | 18.66 | 264.1 |
| 0.5 | d512, keep32, ratio1 | 0.989556 | 19.15 | 264.8 |
| 0.5 | Reference original Ours | 0.989556 | 17.98 | 319.2 |

The test recall requirement uses the corresponding test reference minus the
same predeclared tolerance (0.001), not the validation reference's absolute
recall. Both frozen choices pass this relative recall requirement and the
accounted/observed-memory checks. Test outcomes were not used to reselect.
At 0.5 GiB, the observed reduction in peak RSS is about 17%; code/metadata
resident memory is 51.9 versus 108.6 MiB, and I/O/query is 490.1 versus 590.6.
These single-run QPS observations are not proof of stable throughput gains.

This implements a finite-grid startup selector and its validation/test workflow;
it does not establish a new untouched test set, hard-budget enforcement,
cross-dataset generality, per-query adaptation, or a globally optimal dimension.
At a strict recall target the same dimension can legitimately be selected at
multiple budgets; the selector does not force different dimensions just to
make the result appear adaptive. The hard-budget experiment remains blocked
until an isolated writable/delegated cgroup or suitable test environment exists.

### Extended validation: 1 and 2 GiB, with matched-budget controls

Artifacts: `results/disk_environment/06_adaptive_disk_ann/agnews/adaptive_selection_20260910_211526/`.
Both budgets selected d512/keep32/ratio1 on the first 100 validation queries
before testing the remaining 900 queries. Recall tolerance remains 0.001,
and each configuration was measured once. No candidate was changed after
test outcomes. Same-budget original Ours controls were then completed
separately, rather than treating the 0.5 GiB reference as a timing control.

| Budget GiB | Method | Recall@10 | QPS | Peak RSS MiB | I/O/query |
|---:|---|---:|---:|---:|---:|
| 1 | Original Ours | 0.989556 | 17.88 | 318.3 | 590.6 |
| 1 | Selected d512 | 0.989556 | 19.24 | 266.5 | 490.1 |
| 2 | Original Ours | 0.989556 | 17.89 | 318.3 | 590.6 |
| 2 | Selected d512 | 0.989556 | 19.03 | 267.2 | 490.1 |

These observations support roughly 16% less peak RSS and 17% fewer I/O
requests at equal average Recall@10 on this test slice. Single-run throughput
is 6–8% higher; this is not a statistically established speedup. Equal average
recall does not imply identical returned neighbors for every query. The
standard BFS cache has reached its node-count cap in both budgets, so the
two rows do not demonstrate improved scaling with budget. Query slices were
previously used in development, and hard memory enforcement is still absent.

`budget_controls.json` contains matched-budget baseline measurements and raw
artifact links. `audit_adaptive_selection.py` recomputes decisions from
validation only, checks frozen/test agreement, verifies 900 unique query IDs
and trace recall, compares query/graph/binary identifiers and search budgets,
and checks direct I/O, disabled full DB1 on the adaptive path, codec parameters,
and accounted/observed memory. The audit and all six selector unit tests passed.

```bash
# Reproduce in a new isolated result directory:
python legacy/06_adaptive_disk_ann/run_adaptive_selection.py --budgets 1,2 --max-recall-drop 0.001
# Use the directory printed by the command above:
python legacy/06_adaptive_disk_ann/complete_budget_controls.py RESULTS_DIRECTORY
python legacy/06_adaptive_disk_ann/audit_adaptive_selection.py RESULTS_DIRECTORY
# Reuse this completed archive without rerunning searches:
python legacy/06_adaptive_disk_ann/select_route_profile.py --profile-dir results/disk_environment/06_adaptive_disk_ann/agnews/adaptive_selection_20260910_211526 --budget-gib 1
```

Coverage now spans 0.3/0.375/0.5 GiB in the earlier archive and 1/2 GiB in this
archive; archives remain separate and the CLI must receive the matching one.
No results have been extrapolated to unprofiled budgets or other datasets.

## Tests

```bash
python3 -m unittest discover -s legacy/06_adaptive_disk_ann -p 'test_*.py'
```

The C++ sidecar/kernel parity checks can be run with:

```bash
g++ -std=c++17 -O2 \
  legacy/06_adaptive_disk_ann/asymmetric_route_kernel.cpp \
  legacy/06_adaptive_disk_ann/asymmetric_route_kernel_test.cpp \
  -o /tmp/adaptive_asymmetric_route_kernel_test
/tmp/adaptive_asymmetric_route_kernel_test

g++ -std=c++17 -O2 \
  legacy/06_adaptive_disk_ann/route_sidecar.cpp \
  legacy/06_adaptive_disk_ann/route_sidecar_test.cpp \
  -o /tmp/adaptive_route_sidecar_test
/tmp/adaptive_route_sidecar_test
```

Acceptance checks:

- projection metadata records the dataset-specific method and dimensions;
- each code file has exactly `ceil(d_route / 8)` bytes per row;
- profile selection returns `NoFeasibleProfile` instead of exceeding the budget;
- test queries are never used in projection fitting or dimension selection;
- Phase-1 keeps beam, cache policy, and I/O mode fixed.
