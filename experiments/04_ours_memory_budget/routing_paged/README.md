# Low-memory routing paging

> 本文记录原始 v1 分页实现与实验。当前批量化实现及 CLOCK/LRU 对照见 [routing_paged_optimized](../routing_paged_optimized/README.md)；本目录历史结果不变。

Build and run the independent small experiment:

```bash
python experiments/04_ours_memory_budget/routing_paged/run.py --build --run
```

Code snapshots/binary: `work/ours_memory_budget/routing_paged/`.
Results: `results/04_ours_memory_budget/routing_paged/{gist,agnews}/`.
Progress: `ours_routing_paged_tests.md`. No historical cache results are overwritten.
An existing unaccepted run stops the driver; inspect/archive it before retrying.

The normal native Ours 05C C0 entry selects Resident or Paged before allocating
codes/factors. Resident uses the existing kernel/storage path and creates no
routing service. Paged supports the ordinary baseline memory policy, not
adaptive navigation, full-resident ablations, or optional hybrid/record caches.
The memory-experiment snapshot generator includes the new routing module.

## Memory and switches

Effective budget is the smaller of `--search-dram-budget-gib` and RLIMIT_AS soft
limit. Routing size comes from the verified sidecar and quantizer headers.
Resident admission includes codes, factors, centroids, per-worker visited +
12 MiB reserve, optional locality-map loading peak, explicit other/safety reserves.

Paged admission additionally reserves a bounded 64-record scratch window per
worker and 10 MiB for the completion-thread stack, AIO controls and service.
Cache pages cost 4096 + 256 reserved bytes each; the allowance includes page-slot,
hash/LRU/allocator overhead. In-flight pages use those same slots, not another
unbounded buffer pool. Metadata is bounded by the admitted cache capacity and
scratch by 64 records per query thread. Counters are fixed-size.

- `--routing-max-inflight-pages`: default 64, shared across all query threads;
  reduced to available slots, capped at 4096 for bounded control allocation.
- `--routing-cache-bytes`: optional cap on page data **plus per-page accounting**;
  otherwise all available paging capacity is used. A cap below one charged page
  is rejected when paging is required. This flag does not force paging if DB1 fits.
- `--routing-other-reservation-bytes`, `--routing-safety-reservation-bytes`:
  default 0 for compatibility. Experiments explicitly use 256 MiB and 64 MiB.

These are admission reservations, not complete allocator attribution. Whole-process
RLIMIT_AS, VmPeak and RSS are verified separately. No system-free-RAM heuristic or
container-sharing guarantee is claimed. The routing limit does not limit existing
payload I/O; payload and routing can still contend for the device.

## I/O and search semantics

Original sidecar layout is retained: 24-byte header, codes array, factors array.
Ranges can straddle pages. The last O_DIRECT read may return only the file's valid
remaining bytes; that exact length is required. Padding outside EOF is zeroed,
never interpreted as record data. Truncation or I/O failure fails the query.

One completion thread owns libaio. Queries acquire pinned shared page slots,
submit missing pages without waiting, and compute ready records with the existing
batch kernel. Ready bytes are copied to bounded scratch, allowing immediate page
unpinning; thus even one slot can serve a record spanning several pages.
Candidates update search state only after scores are restored to original order.
Global backpressure and shared in-flight requests prevent unbounded submission.
A generation counter prevents missed wakeups. Failure wakes all waiters;
io_destroy drains/cancels requests before control/buffer destruction.

`routing_stats` start/end snapshots delimit measured queries after warmup. Physical
reads are charged once to the submitting query and included in ordinary total I/O.
The driver reconciles their sum against service counters and the resident payload
reference. `wait_ns` sums query-thread condition-variable wait time (can exceed
wall time with 32 threads); total query timing also includes lock contention,
copying, cache maintenance and scoring.

## Experiment protocol

Select the first 100 IDs from the existing 800-test-query order, extract matching
query/groundtruth rows, and retain the original-ID mapping and hashes. No validation
or training queries are mixed in. L100/300/580, beam1, workers32, two rounds;
resident controls and page capacities near 25% / 75% of sidecar pages.

Each L starts with an empty routing cache. `cold` measures the entire batch from
that empty start; it does not mean every query is cold. `warm` repeats the same
100 queries as warmup before timing. Existing O_DIRECT storage preparation is
preserved and extended with one direct pass over the routing sidecar for every
mode. Device/controller caches are uncontrolled; no cold-SSD claim is made.

Use equal search parameters and exact per-query results/counters for correctness.
Resident controls use 2 GiB; paged cases use their calculated smaller hard budgets.
These are feasibility/overhead comparisons, not equal-budget competing algorithms.

## Tests

```bash
cargo test --offline --manifest-path src/graph_core/Cargo.toml --lib disk_port::
python -m unittest discover -s tests -p test_routing_preflight.py
g++ -std=c++17 -O2 -pthread experiments/04_ours_memory_budget/routing_paged/test_completion_order.cpp -laio -o /tmp/routing_completion_test
/tmp/routing_completion_test
```

Coverage includes admission boundaries; one-slot, cross-page and tail reads;
8 concurrent queries; merged requests and pin protection; truncation/failure;
reversed completion harvesting; failure after partial submission with ASan/UBSan; bitwise score parity for B1/INT4/INT8/Full and
batch sizes around 32/64; existing payload/cache regression tests.

The ASan failure-injection test uses `ASAN_OPTIONS=detect_leaks=0` because
LeakSanitizer cannot run under the sandbox ptrace setup; address/undefined-behavior
checks remain enabled. No leak-sanitizer pass is claimed.

The pilot triggers Paged using conservative admission reservations. It does not
prove that Resident would necessarily OOM under each paged run's hard limit.
Larger paging caches can also have higher measured VmPeak than the resident
control because of service/scratch/allocator overhead. Use the recorded actual
VmPeak/RSS and budget separately; do not claim an empirically calibrated OOM
boundary or uniformly smaller process footprint.
