# Adaptive route search repair: AGNews 100-query check

Scope: in-memory 02 runner, first 100 AGNews queries, own R64/Lbuild400 graph,
search list 49, beam 49, one actual search worker, one repeat. Neither row is a
05C disk/memory-budget result. Existing old output directories were preserved.

| Variant | Recall@10 | QPS | Mean latency ms |
|---|---:|---:|---:|
| Existing Ours | 0.996 | 569.715343 | 1.751125 |
| d64 neighbor ordering, repaired | 0.996 | 138.399571 | 7.220407 |

Repairs: compute projected query once; order fresh neighbors by its asymmetric
score, then execute the existing DB1/full4 path. Do not put projected scores in
the original-distance candidate pool or bypass the gate with `continue`.
QPS and per-query counters now divide by the actual measured query count;
query-limit accuracy lookup also uses the measured split length.

Both processes exited successfully. Release build and git diff --check passed.
Raw results are under results/disk_environment/06_adaptive_disk_ann/agnews/
fixed_order_baseline_100q and fixed_order_d64_100q, each in
02_diskann_fair/agnews/csv/diskann_fair_raw.csv and logs/Ours/.

Interpretation: the earlier 0.619 was not a valid estimate of dimensionality
reduction alone. This repair restores high recall but adds CPU cost. It retains
the full DB1 resident sidecar and adds d64; it does NOT yet solve the goal of
replacing resident full-dimensional 1-bit codes under a memory budget. Candidate
pool capacity remains 49 in both runs, to preserve this comparison's settings.
The old two-query 30.55 QPS value used an incorrect numerator and is invalid.
