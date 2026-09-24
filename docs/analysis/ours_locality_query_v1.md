# Experimental native locality query path

The new path is opt-in via --locality-layout-dir and three required SHA-256
arguments for combined pages, residual pages and ID mapping. It is restricted
to isolated native_integration_ 05C C0 runs without adaptive routing.
Default execution and existing pinned formal binaries remain unchanged.

## Execution

- Logical node IDs and graph neighbor order remain unchanged. The resident
  ID-to-slot mapping translates logical IDs to BFS physical slots.
- Candidate distance reads fetch graph+compact records, with deduplicated
  and coalesced page requests. Only compact bytes are used for distance.
- Node expansion reuses the combined page from the same bounded query
  cache. A miss loads the combined page normally; no candidate is skipped.
- Final rerank reads residual.pages for the selected IDs and uses the
  unchanged native residual distance implementation.
- Combined and residual files use separate cache namespaces within ONE
  4 MiB query cache. Mapping capacity is included in resident_bytes.
- This is read reuse, not cross-step speculative prefetch or a new search
  algorithm. It does not promise to eliminate every serial read.

## Validation and limits

The exporter verifies all base adjacency and quantization records byte for
byte, and stores source/file hashes. The diagnostic runner rechecks them.
The native loader verifies required file hashes, mapping permutation and
file lengths. V1 only supports combined/residual records each <= 4096 bytes;
larger dimensions are rejected, not silently truncated.
Old-layout memory execution remains the built-in parity reference.
Trace rows include result_ids so complete ordered top-10 can be compared.
The added map is extra resident memory, not free cache capacity.
Legacy readers are still initialized by the common runner but do not serve
payload/graph reads in the new direct path; initialization is outside QPS.
Complete allocator/workspace accounting remains unverified (formal_ready=false).

Run scripts/local_runs/check_ours_locality.py after building the release
qgraph05_shared_graph_port. It stores a private binary and compares widths
12 and 100, w32, 100 warmup + 800 test queries on AGNews. Results go to a
new locality_query_<timestamp> folder; never to the existing formal run.

## First integrated results

Run: results/disk_environment/05_disk_system_fair/locality_query_20260911_234136.
Same executable, w32, C0, widths 12 and 100, 800 test queries per width.
All 1600 ordered top-10 lists, Recall values and DB1/full4 search counters
match between layouts. The old native memory path is also retained as
the runner's built-in parity reference.

| Width | Legacy QPS | Locality QPS | Legacy requests/query | Locality requests/query |
| --- | ---: | ---: | ---: | ---: |
| 12 | 37.2294 | 633.1810 | 340.34375 | 183.39875 |
| 100 | 11.0358 | 193.9700 | 854.06250 | 601.70875 |

Width-12 bytes/query: 1394129.92 -> 929182.72.
Width-100 bytes/query: 3498583.04 -> 2714009.60.
Resident bytes: 113872632 -> 116950160 (3077528-byte ID map).
Recall@10 stays 0.956875 / 0.992625 at widths 12 / 100.
Native reader unit test passed for mapped logical IDs, combined-page
reuse, separate residual namespace, out-of-range IDs and invalid mappings.
The 11 existing library tests passed before the integrated experiment.

These are diagnostic measurements, not a cold-storage guarantee or proof
of the historical regression's cause. Layout order, colocation and split
residual were changed together; individual contributions are not isolated.
O_DIRECT bypasses OS page cache, not RAID/controller caches. Files were
recently exported, and no controller cache reset or cold-storage control
was performed. Do not advertise the observed ratio as a general speedup.

Reverse-order width-12 check, locality_query_20260911_234449:
locality first 636.8439 QPS, legacy second 37.5342 QPS; all 800 ordered
top-10 and search-counter comparisons passed. This reduces simple run-
order confounding but does not control hardware-cache residency.
Formal queue remains paused and formal pinned executable SHA-256 remains
77ee1b3b761a1bd19decb3eb9f5ab38e99b54395832f7d38659d464ce6b37abe.
