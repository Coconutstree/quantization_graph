# OG-LVQ official parity audit (2026-09-15)

Status: partially corrected; performance queue paused and blocked.

## Confirmed and corrected

- The disk port previously used an unconditional global unordered_set and
  distance/ID sorting. It now directly uses the packaged SVS SearchBuffer,
  with the official default visited filter disabled. This matches the default,
  not yet a verified saved-index search configuration.
- Entry-point distance evaluation is now included in the local counter.
- Source metadata no longer claims an official distance kernel or proven
  algorithm preservation.
- The queue refuses to start while ALGORITHM_AUDIT_BLOCKED.json exists.
  Pinned historical binaries and results were not replaced or deleted.

## Correction to the earlier audit

Official index.h lines 582-583 increases BOTH capacity and window to top-k
when capacity is too small. Thus max(width, 10) is not by itself a mismatch
for top-10 under the default equal-window/capacity configuration. Widths 1-9
are nevertheless not independent effective search widths.

## Validation

- qgraph05_og_lvq_disk_port builds successfully with GCC 11.
- qgraph05_og_lvq_buffer_test passes: tie insertion order, duplicate ID,
  disabled visited filter, expansion termination, reopening on a better
  candidate, and separate window/capacity behavior.
- These are buffer regression tests, NOT end-to-end official parity.

## Still required before performance restart

- Integrate or independently compare the official LVQ distance implementation.
  Packaged lvq.h exposes template declarations; implementation availability
  and compiled-library interfaces still need investigation. The disk port
  currently retains its local scalar decoder.
- Resolve the saved official search parameters and compare actual official
  searches against the disk port: ordered IDs, expansion sequence, distances,
  and counters, including ties and boundary cases.
- Existing AIO/mmap parity compares the SAME search implementation and is not
  an independent official reference.
- Do not combine corrected-binary measurements with legacy width checkpoints.
  Pin a new version and use a distinct result root after validation.
- No batch-I/O/search-order optimization was introduced by this correction.

The stopped DBpedia OG-LVQ measurement was width 340. Earlier checkpoints
are retained as legacy experimental evidence, not approved formal results.
