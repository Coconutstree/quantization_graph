# Query Cache Accounting Status (2026-09-11)

Status: incomplete migration; no new formal performance results.

## Sampled Reference Policy (2026-09-13)

The user stopped the queue during DBpedia Ours full reference replay.
Completed disk performance artifacts are retained; interrupted reference output
is not passing evidence. The queue remains stopped until explicitly resumed.

Updated `run_optimized_05_queue.py --resume` performs:

1. Full byte verification when exporting each locality layout. Reused layouts
   must match every saved source/output SHA-256 from that verification.
2. Deterministic, evenly spaced selection of at most 32 query rows, with their
   matching groundtruth. Rust ports check widths 10/100/580; C++ ports check
   1/100/580 to cover their supported low-width fallback. At most 96 comparisons
   per method. Samples have separate query files/order/hash and no warmup.
3. A single full disk sweep after the sampled check passes, with Rust inline
   parity disabled. No full-query reference replay follows the measurement.

Rust inline disk/memory comparison is limited to the sample. C++ uses sampled
direct-I/O versus mmap comparison. This is sampled correctness evidence, not
an assertion of full-query parity or automatic formal acceptance.

Resume skips completed datasets and the completed AGNews repair. Within the
interrupted dataset it reuses complete disk measurements only after checking
command settings, binary identity, trace SHA-256, width/query counts and the
memory-limit result. DBpedia Ours need not repeat its completed full disk sweep.
Sampling checks precede NEW measurements; for an existing retained measurement
they are a retrospective check and do not change its historical provenance.

## Optimized Follow-Up Queue (2026-09-12)

Entry: `scripts/local_runs/run_optimized_05_queue.py`.
Output: `results/disk_environment/05_disk_system_fair/test_L_400_w_32/`.
Live status: `optimized_queue_state.json` in that output directory.

1. Remeasure AGNews DiskANN widths 540 and 580 under the same 2 GiB process
   limit; compare all 1600 query results against the existing reference.
   Preserve pre-repair artifacts in `_history`, replace only those trace and
   summary rows, record source hashes, then regenerate the Recall >=0.90 plot.
2. Process GIST, DBpedia and SIFT10M serially. Export and byte-verify each
   dataset's own locality layout; never reuse AGNews physical mappings.
   Use binaries pinned by the completed AGNews acceptance run.
3. Run each method's supported width sweep once at w32/C0; perform actual
   reference comparisons separately. Archive old method output before writing
   new output. Stop on budget, input mutation or parity failures.
4. Export CSV and plot each completed dataset. Old DBpedia processes have
   been terminated; the legacy continuation entry remains disabled.

Timing repair is not blanket formal acceptance. `formal_acceptance.json`
records outstanding workspace/budget-policy and timing/storage acceptance;
the queue does not set final formal status without those requirements passing.

## Isolated Acceptance Remeasurement (2026-09-12)

Implemented `scripts/local_runs/run_agnews_acceptance.py` and
`measure_05_process.py`. Historical artifacts are never rewritten.

- Disk measurements run under a hard 2 GiB RLIMIT_AS, with the actual limit
  verified via prlimit and process high-water RSS collected via wait4.
  This is a conservative bound on the entire user address space, including
  inputs, libraries, allocator arenas, caches and stacks. It is NOT a sum of
  attributed workspace categories, and does not measure kernel/device memory.
  A failed allocation or unverified limit cannot pass the budget check.
- Reference runs are separate, uncapped processes. Ours/DiskANN can defer
  inline memory parity explicitly; zero comparisons now mean not_verified.
  The whole-process measurement mode disables RSS high-water resets.
- C++ ports expose an opt-in mmap reference reader, with distinct backend
  metadata and zero direct-I/O counters. Full ordered top-10 IDs and search
  counters are compared against disk traces. This proves storage-path parity,
  NOT an independent reimplementation of the baseline's algorithm. Symphony
  also has 78 passing upstream-library comparisons on tie/fallback fixtures.
- Storage evidence records fixed 100-query warmup, w32, C0, query-order hash
  and unchanged index metadata. The supervisor serializes native experiments
  at the current dataset boundary. Controller caches remain uncontrolled;
  this protocol must not be described as guaranteed cold storage.
- AGNews needs new query measurements for all five methods under this common
  evidence protocol. Graphs are reused. Ours and DiskANN use 40 valid widths;
  the other methods retain all 49. One measured repeat, separate reference
  runs, fresh output directory, CSV and Recall-QPS draft export afterward.
- The current GIST dataset is allowed to finish; its queue parent waits at
  the dataset boundary. After AGNews evidence collection the parent resumes
  DBpedia/SIFT10M. Existing pinned queue binaries are not overwritten, so
  those older queue measurements remain pending acceptance, not formal.

Tests: memory/trace evidence tests 4/4; native contract tests 10/10;
direct-I/O/mmap byte comparison passes; Symphony upstream fixtures 78/78.
Five native targets rebuilt. End-to-end AGNews evidence is pending execution;
category attribution and device-cache control are not claimed complete.

## AGNews Acceptance Audit (2026-09-12)

Verdict: saved-result integrity passes; final formal acceptance fails.
Latest evidence: results/disk_environment/05_disk_system_fair/single_test_w32_20260911/acceptance_agnews_20260912_000942/.

- All five methods complete: 49 rows each except DiskANN-PQ (40 valid
  widths), 800 test queries per row, 188800 trace records total.
- Query IDs, query-order hashes, trace byte/sector accounting and bounded
  query-cache allocations pass. Summary means agree with traces within
  1e-5 relative tolerance for serialized C++ decimal precision.
- Executable hashes are recoverable for all methods. SymphonyQG uses the
  saved pre-logging executable; the current command-path binary differs.
  Reproduction must use the matching backup recorded in acceptance.json.
- All five fail the formal contract: incomplete memory accounting,
  missing measured worker_scratch_bytes, and formal_ready=false.
- C++ source audit finds fixed parity=0-delta/1-overlap output in
  SymphonyQG, OG-LVQ and Glass; the reference hash is index.meta, not an
  independent query-result reference. These fields alone cannot establish
  implementation parity. Separate tests do not replace full run evidence.
- Timing-scope provenance and storage-cache protocol remain unverified.
  Ours widths 1..9 remain requested labels with effective width 10.
- No saved measurement or formal readiness flag was edited. The queue
  was not stopped or duplicated, and no performance experiment ran during
  this artifact acceptance audit.

The earlier 000851 audit used an overly strict summary tolerance and only
the current binary path; its integrity failures are superseded by 000942.

## Acceptance Hardening (2026-09-12)

- Removed hardcoded passed parity from the three C++ disk ports. They now
  emit not_verified/null until real comparison evidence is implemented.
- Contract validation handles null parity and requires a positive measured
  comparison count (except explicit fast diagnostics); it no longer invents
  one comparison when evidence is absent.
- 05B/05C acceptance now requires a declared test-only measurement scope
  and verified controlled-warm or controlled-cold storage protocol. Ports
  declare storage_cache_protocol=uncontrolled, so current artifacts cannot
  accidentally pass. These declarations are necessary, not sufficient
  substitutes for recorded operational evidence.
- Ours rejects widths below top-k. The query helper schedules >=10 for
  Ours and DiskANN. CSV/plot export excludes historical Ours 1..9 rows;
  original raw results remain untouched.
- Existing fixed background binaries were not replaced. New source rules
  do not retrospectively change their behavior or acceptance status.
- Contract regression tests pass 10/10. Full measured workspace accounting,
  real C++ reference-query comparisons and controlled storage runs are
  still NOT implemented/accepted; these are remaining implementation work,
  not items solved by changing readiness flags.

## Single-Run Formal Preparation

- The formal repeat policy is now one run (`repeat_id=0`). Runner and plotter
  validate unique operating points and use `formal_test_rows.csv`; neither
  synthesizes a five-run median, IQR, or CV from one measurement.
- Contract regression tests pass 10/10, including duplicate rejection and
  rejection of absent or explicitly incomplete 05B/05C memory accounting.
- Four-dataset doctor passes 42/42. Its DRAM check establishes only resident
  code lower-bound feasibility, not complete workspace accounting.
- C++ ports previously reported the warmup parameter without running warmup.
  Warmup now executes the same query path before the timed loop; its query
  results are discarded and do not enter the measured trace.
- DiskANN warmup now enters the same query-cache scope as measured queries.
- New C++ binaries are built separately under
  `build/formal_single_run_gcc11/05_disk_system_fair`. Live background binaries
  and the port registry were not replaced during the running validation sweep.
- Ours admission reservation now explicitly includes the byte-per-node visited
  array and 4 MiB query cache in addition to the existing workspace allowance.
  This remains a reservation, NOT a measured workspace peak. Rust source passes
  `cargo check`; the live production binaries were not rebuilt.
- Formal test execution is still blocked on complete workspace accounting and
  genuine implementation-parity evidence. The existing validation queue is
  diagnostic and has not been relabeled as formal.

## Implemented

- SymphonyQG, OG-LVQ, and Glass use `query_page_cache.hpp`: 4 KiB pages,
  per-query LRU, and a 4 MiB capacity including fixed page storage and hash/LRU
  container storage. Graph and payload pages share that capacity within each
  method, with separate file namespaces. Query destruction discards the cache.
- Cache hits, misses, evictions, and allocated container bytes are emitted in
  query traces. These are distinct from the cross-query `cache_bytes` field.
- Peak RSS uses Linux `getrusage(RUSAGE_SELF)`, not a fabricated resident-byte
  estimate. It is a process-lifetime high-water mark, not per-query incremental
  memory, and can include export when export and search share a process.
- Unmeasured worker scratch is null; search artifacts explicitly report
  `memory_accounting_complete=false` and `formal_ready=false`.
- Contract validation rejects explicitly incomplete memory accounting.
- Existing graph/index files have not been rebuilt or changed for this update.
- Ours graph and payload readers now share the same C++ cache through the native
  bridge, using separate file namespaces. Cached pages are copied into batch-owned
  buffers before eviction, so a batch may safely exceed cache capacity. A new
  query releases the previous payload cache before constructing the graph reader.
- DiskANN uses a fixed-storage Rust LRU with the same 4 KiB/4 MiB policy. A scoped
  thread-local cache is owned by each synchronous query, not by pooled readers;
  destruction clears it even when search returns an error. This wrapper applies
  only to the experiment port, not the upstream library's normal reader.
- DiskANN I/O counters now describe requests and bytes actually sent to the
  Linux reader on cache misses, instead of treating logical vertex loads as I/O.
- Ours and DiskANN also emit null worker-scratch measurements and explicitly
  reject formal readiness. Their former 8 MiB/worker estimate is retained only
  as `worker_scratch_reservation_bytes`, not as a measurement.
- Current compiled binary hashes have been refreshed in `ports.local.json`.

## Remaining Before A Fair Rerun

- Account for candidate queues, visited sets, decoded records, pending I/O
  buffers, and allocator overhead in addition to the fixed cache containers.
  The 4 MiB check currently covers cache capacity only, not the full 2 GiB budget.
- Add a complete resident/codebook/workspace/cache budget gate; process peak RSS
  alone cannot identify the search-index working set.
- Extend the diagnostic checks to other datasets and compare all returned IDs
  against a reference, including high-width eviction-heavy cases. The AGNews
  sample below does not establish recall or performance for the full test split.
- Replace inherited hardcoded implementation-parity claims with actual evidence
  before allowing formal-ready results.
- Run diagnostic queries, then rerun the requested performance sweep into fresh
  result directories with policy and binary provenance. Do not mix old metrics.

## Verification Performed

- Three C++ native targets rebuilt successfully.
- Cache reference test: 10,000 operations exercising capacity, file isolation,
  collisions, LRU eviction, and byte integrity passed.
- Native contract tests: 9/9 passed, including incomplete-accounting rejection.
- Glass tiny integration completed search but failed formal contract validation
  because worker scratch is not yet measured. This is NOT a passing end-to-end
  integration test or evidence that memory fairness has been completed.
- No formal performance sweep was started by this update.

## Continuation Verification

- Shared Rust port tests: 6/6 passed, including memory/direct graph agreement,
  cache file isolation, and a 1,300-page batch exceeding the cache capacity.
- Ours payload test: passed for all four coalescing/reuse combinations with
  1,300 pages; compact and residual bytes match the memory reference exactly.
- DiskANN cache tests: 2/2 passed using standalone `rustc --test`, including
  10,000 reference-LRU operations and query-scope isolation. The Cargo test
  invocation could not run offline because dev dependency `anes` was absent;
  the release binary itself builds successfully offline.
- Five-method AGNews diagnostic: 4 queries, C0, one worker, widths 64 and 256;
  all five methods completed with eight trace rows each and unchanged index
  file sizes/mtimes. Cache storage is positive and <=4 MiB on every trace row.
- DiskANN's measured disk/memory parity: Recall delta 0, top-10 overlap 1,
  comparison-count delta 0. These are sample results, not a full-dataset audit.
- Results: `results/graph/query_cache_audit_20260911_agnews/report.json`.
