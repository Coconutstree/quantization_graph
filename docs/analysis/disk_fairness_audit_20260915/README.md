# Four-method disk fairness audit

Date: 2026-09-15. Scope: Ours, SymphonyQG, Glass-NSG, DiskANN-PQ.
OG-LVQ is covered by ../og_lvq_official_parity_audit_20260915.md.
The performance queue remains paused. Production search code and pinned
performance binaries were not changed in this audit.

## Findings ordered by severity

### P1: Glass does not reproduce upstream tie handling

The disk port sorts candidates by (distance, ID), including when a full pool
receives an equal-distance candidate. Upstream LinearPool rejects a new
candidate whose distance is >= the worst full-pool distance. Below capacity,
upstream inserts after existing equal distances, without sorting them by ID.

Sources: native/glass_disk_port.cpp:455; baselines/pyglass/glass/neighbor.hpp:361,
382; baselines/pyglass/glass/searcher/graph_searcher.hpp:132.

New diagnostic: experiments/05_disk_system_fair/tests/audit_glass_official_search.cpp.
It compares actual disk search_one to upstream GraphSearcher::Search, using the
SAME graph, original quantizer object, and byte-copied SQ4U codes. The 64-node,
64-dimensional fixture deliberately contains tied quantized distances. Widths
1,10,20,64,580 and three queries produce 15 ordered top-10 mismatches, all also
with different membership. Exit code 2 denotes a detected mismatch, not a crash.

Example at width 10, query 0:

- Upstream: 60,56,52,48,44,40,36,32,28,24
- Disk: 0,4,8,12,16,20,24,28,32,36

This proves non-equivalence on a valid tied fixture. It does NOT measure the
mismatch rate or performance impact on AGNews/GIST/DBpedia. Tied candidates may
be equally good by the quantized metric; this is a method-reproduction failure,
not proof that every returned neighbor is inaccurate.

Glass DOES use the actual SQ4U query encoder and integer distance computer.
Its global seen set has the same once-discovered intention as the upstream
bitset; unlike OG-LVQ, global deduplication alone is not a confirmed difference.
Upstream Search and SearchBatch themselves differ below k: Search uses
max(k,ef) for window/capacity, whereas SearchBatch uses ef/max(k,ef).
The reference API must be explicitly selected before judging those widths.

Remedy: reuse upstream LinearPool and the declared upstream search entry point;
compare ordered results AND expansion traces on tied/random/real fixtures.
No production fix was made in this audit.

### P2: Timing and counters are not cross-method equivalents

- Glass and SymphonyQG write the full traversal duration to BOTH
  distance_compute_us and queue_compute_us, including I/O. They write the
  post-search recall_at_10 duration to rerank_us. These components cannot be
  interpreted as CPU-only phases or added together.
- Their per-query latency includes recall evaluation, whereas Ours and DiskANN
  stop per-query latency before recall evaluation. C++ reader destruction is
  outside their latency timer but remains inside the batch QPS measurement.
- Glass/Symphony recreate their direct readers per query. Ours/DiskANN retain
  reader resources across queries. C0 cache contents are cleared in each case,
  but resource lifecycle overhead is not identical.
- Ours visited_nodes counts newly discovered IDs; Symphony/Glass count expanded
  nodes. DiskANN explicitly maps visited_nodes to total_comparisons, a proxy,
  not an independently measured visited-set cardinality.
- Symphony distance_calls increments once per expansion, not for each edge
  approximation or fallback exact evaluation. Do not compare it numerically
  with other methods' distance evaluation counts.
- Ours locality io_wait_us wraps lookup/copy/allocation as well as the read,
  so it is not pure storage wait either.

Sources: glass_disk_port.cpp:630,712; symphonyqg_disk_port.cpp:604,687;
native_rust/src/ours_port.rs:974; native_rust/src/locality.rs:53;
native_diskann/src/main.rs:735,847,1039,1327.

### P2: DiskANN cache adapter can duplicate reads within a batch

cached_reader.rs collects misses before inserting returned pages into cache.
Two requests for the same uncached page in that batch can both be submitted;
there is no within-batch deduplication. It also splits aligned multi-page
requests into 4 KiB requests instead of retaining their coalescing.
This is an I/O-efficiency concern, not a proven search-algorithm change.
Physical impact on current datasets has not been measured.

### P2: Existing parity evidence has narrower scope than official equivalence

Glass/Symphony measured_parity compares the same port under AIO vs mmap.
Ours compares the same 05 search over disk vs memory pages, not experiment-02
search_ours_paper_active directly. DiskANN compares two readers under the same
official searcher, which is useful for storage correctness but not an
independent audit of every locally modified upstream component.
Rust inline parity uses overlap/recall/average-counter tolerances rather than
strict ordered-ID and expansion-sequence equality.

AGNews has 32,000 comparisons for each Rust method and 39,200 for each C++
method in saved evidence. Those counts do not change the reference scope.

## Method-by-method disposition

| Method | Algorithm audit | Evidence boundary |
| --- | --- | --- |
| Glass-NSG | Confirmed mismatch | 15/15 tied-fixture ID/member mismatches |
| SymphonyQG | No mismatch found in inspected valid-input loop | 78 upstream/disk fixture comparisons passed; edge codes/factors in the fixture are zero-filled, so realistic FastScan numerics remain untested |
| DiskANN-PQ | Official DiskIndexSearcher is actually invoked | Reader changes and cache wrapper inspected; cache and I/O tests pass; proxy counters and batching need correction/qualification |
| Ours | No new active-config search mismatch established | Standard R64/int8/beam1 path compared statically with experiment02; storage tests pass; independent 02-vs-05 query tracing still needed |

Symphony directly reuses QGQuery, QGScanner, SearchBuffer, ResultBuffer,
HashBasedBooleanSet and L2. The previous forced candidate-row demand read is
absent. A candidate row is read for expansion or fallback exact scoring;
prefetch is not emulated by a forced disk read. The fallback snapshot/tie rules
match upstream update_results. Row padding still incurs method-specific I/O.

Ours: both source and disk paths use batch size 64. On a degree-64 graph, the
initial per-expansion gate, ordered distance batch, and gate recheck before
insertion align under int8 and disabled early stopping. Locality maps physical
slots and restores requested logical-ID order; no logical neighbor sorting
was found in this path. The current source ALSO contains optional adaptive
sorting, ratio gating, shortlist truncation, and revisit modes. These change
the search policy when enabled. Saved AGNews/GIST/DBpedia Ours commands contain
NO adaptive-route flags and pin binary a64dfc64...; do not retrospectively
attribute current optional code to those runs. Generic equivalence for every
codec, degree, early-stop setting or adaptive mode is not claimed.

DiskANN uses official DiskIndexSearcher::search with SearchMode::graph,
resident PQ codes and disk full-vector records. The local Linux reader change
drains completions and handles short reads; it does not replace candidate
selection. Do not confuse this official disk search with the 05B shared-graph
runner or with a custom search loop.

## Tests run in this audit

| Check | Outcome |
| --- | --- |
| Newly compiled Glass upstream/disk fixture | 15 mismatches out of 15; see glass_fixture.log |
| Existing Symphony upstream/disk fixture | 78 comparisons pass; see symphony_fixture.log |
| DiskANN query_cache.rs, freshly compiled with rustc --test | 2 tests pass: bounded LRU and query-scope reset |
| Existing DiskANN io_completion_regression binary | 1 test passes: multi-batch bytes and short-read recovery |
| Existing diskann_fair test binary, disk_port:: filter | 8 tests pass, including Ours layout/residual namespaces and over-capacity batches |
| test_export_ours_locality_layout.py | 2 tests pass |

The existing Rust test executables were not rebuilt in this turn. Their hashes
and inspected source hashes are recorded in source_hashes.json, separately
from pinned performance binaries. Passing storage tests is NOT a full upstream
algorithm parity certificate.

## Memory and result status

Saved AGNews/GIST results for all four methods report successful whole-process
2 GiB address-space bound measurements, but memory_accounting_complete=false
and formal_ready=false. This is not evidence that the limit was exceeded;
it distinguishes the conservative process bound from category accounting and
the overall fairness acceptance decision.

No existing results were deleted or promoted to formal. The queue remains
blocked by ALGORITHM_AUDIT_BLOCKED.json. Resolve the confirmed Glass and OG-LVQ
algorithm differences, independent references, and metric definitions before
approving a new benchmark version. Never merge changed-algorithm results with
old width checkpoints as though they were one measurement series.

## Reproduction

Compile the Glass fixture from the repository root (upstream refiner templates
currently emit unrelated narrowing warnings):

```sh
g++-11 -std=c++20 -O2 -march=native -fopenmp -Ibaselines/pyglass -Ibaselines/pyglass/third_party/helpa -Iexperiments/05_disk_system_fair/native experiments/05_disk_system_fair/tests/audit_glass_official_search.cpp build/formal_single_run_gcc11/05_disk_system_fair/libqgraph05_direct_io.a -laio -lcrypto -lpthread -ldl -o /tmp/qgraph05_glass_official_audit
/tmp/qgraph05_glass_official_audit
```

This tiny diagnostic uses temporary files, not the production dataset graphs.
