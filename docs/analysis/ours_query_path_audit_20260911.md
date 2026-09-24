# Ours query-path audit

Scope: current working source, with historical comparison to 7183eaf.
That commit is available, but is NOT the absent b50cf043 commit recorded
in the historical benchmark. Formal queue remains paused.

## Confirmed issues

1. Ours QPS timing includes worker creation, warmup, and worker teardown.
   native_rust/src/main.rs starts wall_start at line 1119, before warmup
   at line 1158, and stops after joins at line 1206. The numerator at
   line 1357 is only the test run count. Workers have no common post-
   warmup barrier, so early workers can measure while others warm up.
   SymphonyQG native port lines 713-718 excludes warmup from its timer.
   This is a cross-method timing inconsistency. The same Ours timing
   structure exists in 7183eaf; it is not established as a new regression.
   Do not correct old QPS with an assumed fixed warmup percentage.

2. Payload reads and distance computation happen for a survivor batch
   before a second gate. distance_evaluations increments only after
   that gate (ours_port.rs around 1060-1071), so it is not a count of
   all actually computed distances. Batching can fetch subsequently
   rejected candidates; changing it requires search-parity validation.

3. io_wait_us includes page lookup, allocation, copying and decoding,
   not just AIO. Kernel-call probe independently established that the
   current run predominantly waits in io_getevents; this naming issue
   alone does not explain the historical throughput gap.

## Concurrency and batching

- Each worker constructs its own graph_factory inside the spawned
  closure (main.rs around 1133), plus its own payload reader. The graph
  reader is reused across queries via BackendFactory::create, which
  resets only the query cache (lib.rs 1222-1233). It is not a new AIO
  context per query, nor one global reader serializing all 32 workers.
- Graph and payload cache mutex sharing is within that worker/query.
  The result mutex is acquired after search, not held over disk reads.
- For each frontier node, graph.read_nodes(&[node]) completes before
  payload processing (ours_port.rs 935 onward). Payload pages are
  submitted in batches; each batch waits for all completions before
  continuing. beam is not the same as asynchronous I/O queue depth.
- DirectAioReader merges only consecutive physical page numbers in a
  submitted batch, not arbitrary scattered pages. It uses min_nr=1 in
  io_getevents and loops until all submitted requests have completed.
  Multiple completion calls per batch are expected, not duplicate reads.
- direct_io.cpp is identical to 7183eaf. The bridge read path is unchanged;
  added bridge functions concern query caching. This does not prove
  equivalence to the historical missing binary.

## Repair boundaries

Use one measured phase after every worker has finished warmup, and stop
measurement before teardown. A robust implementation must propagate
warmup errors without leaving peers blocked on a barrier. Record warmup,
measured search and teardown separately. Audit all methods against the
same scope; retain original results and rerun under a new result ID.
Correct distance counters separately without changing candidate logic.
Neither fix is evidence that historical 174 QPS will return.

No production search behavior or formal result was modified in this audit.

## Subsequent correction

Ours batch measurement now waits for every worker to finish initialization
and warmup before releasing test work. Dropped readiness/start channels
cancel waiting peers on setup failure; no unconditional barrier is used.
The final test completion timestamp is captured before reader teardown.
Distance evaluation accounting now counts the complete computed batch
before the second gate; rerank remains a separately reported operation.
These changes do not alter candidate selection or graph contents.
Cargo release check/build passed. Runtime regression is stored separately
under results/disk_environment/05_disk_system_fair/ours_timer_fix_20260911_231358.
Formal pinned binaries and prior result artifacts are not replaced.
The generic non-Ours Rust batch timing still needs a separate scope audit;
this correction is specifically for the Ours batch runner.

## Request-order comparison

Compared current source against available commit 7183eaf, specifically:

- Both traverse frontier nodes in order, fetch one node's adjacency,
  then process that node's survivors in chunks of 64.
- Both sort and deduplicate payload page IDs BEFORE filtering cache hits.
  Missing pages retain ascending page order before read_pages.
- Both wait for the payload batch before calculating its distances and
  updating the candidate pool; this was not introduced by the cache fix.
- gather_records iterates input candidate IDs, not HashMap iteration
  order. Page sorting therefore does not reorder candidate evaluation.
- The native direct_io.cpp file is byte-identical to 7183eaf. Adjacent
  merge limits, submission chunks and completion waiting are unchanged.
- An added adaptive-route branch can reorder candidates, but recorded
  AGNews runs have adaptive_route_mode=disabled; it is not active here.

The real cache-policy change is separate unbounded graph/payload maps
versus a combined, file-namespaced, 4 MiB per-query cache. At widths
causing eviction, it can change the missing-page request sequence.
Width-12 diagnostic traces have no eviction, so this mechanism is not
supported as the cause of the observed width-12 slowdown. It should not
be generalized to high widths (e.g. 580) without a separate check.

This is source-path evidence, not an exact old/new syscall trace match.
Cross-worker interleaving and scheduling can differ even with unchanged
per-query logic. No historical per-batch page trace is available in this
audit, and 7183eaf is not the missing historical benchmark commit.

## Additional working-tree fixes

- QueryPageCache::get now uses an uninitialized stack page and allocates
  the returned Box only on a hit. The bridge initializes all bytes before
  success; a miss never reads the uninitialized page.
- The generic Rust batch runner now uses the same post-warmup start
  handshake and pre-teardown end timestamp as Ours.
- Summary rows expose effective_search_width. Requested width remains
  unchanged for provenance; Ours effective width is max(requested, 10).
  Downstream plots must use this field or exclude unsupported widths;
  existing CSVs/plots have not been regenerated.
- Adaptive projection loading validates dimensions, complete file length,
  checked byte offsets and finite values. Prefix rows of a larger valid
  components matrix remain supported. This is not active in the AGNews
  DB1 control configuration.

Verification: release binary cargo check passed; all 11 Rust library tests
passed, including native AIO page correctness, over-capacity cache batches
and new invalid-shape/truncation/non-finite NPY cases. The generic runner
handshake error paths have not received an injected-failure runtime test.
No new full performance sweep or formal binary replacement was performed.
Pure I/O/CPU component accounting, downstream effective-width propagation,
complete search-memory accounting and the historical slowdown remain open.
