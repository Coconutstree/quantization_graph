# AGNews disk-resident replacement pilot

Actual 05C native O_DIRECT/libaio path, existing own R64/Lbuild400 graph and disk
payload, first 100 AGNews queries, search width 49, beam 1, workers 32, one run
per configuration, no warmup. Both methods use the same standard BFS graph
cache policy; cache capacity is computed from the budget after codec and
worker scratch. The existing 10%-of-base cache cap binds in both tested budgets.
These are isolated integration runs (`formal_ready=false`), not publication QPS.

| Budget GiB | Method | Recall@10 | QPS | Codec resident MiB | Process peak RSS MiB | I/O/query |
|---:|---|---:|---:|---:|---:|---:|
| 0.5 | Original Ours | 0.992 | 41.597 | 108.597 | 287.985 | 612.37 |
| 0.5 | d64 replacement | 0.992 | 18.248 | 9.063 | 393.102 | 1832.23 |
| 2 | Original Ours | 0.992 | 41.528 | 108.597 | 288.379 | 612.37 |
| 2 | d64 replacement | 0.992 | 17.947 | 9.063 | 393.371 | 1832.23 |

Raw artifact directories (repository-relative):

- results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260909_235701
- results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260909_235714

Each contains baseline/d64 .json summaries, .queries.jsonl per-query traces,
.argv.json exact commands, and .log terminal output. Earlier c0 checks are in
disk_replacement_20260909_235630 and disk_replacement_20260909_235643.

Implementation: adaptive loader bypasses the full DB1/factor sidecar allocation.
The low-dimensional code orders fresh neighbors; all candidates are verified by
full4 records read from disk and final residual rerank. No reduced-dimensional
score is treated as an original-space lower bound. Every disk record read is
used for distance evaluation. Query projection runs once per query. Scales
remain FP32 in RAM in this prototype, although persisted scales are FP16.

Accounting: 05C now counts the codec actually loaded instead of the export's
maximum-resident-ablation footprint. The d64 codec uses 9,502,920 bytes versus
113,872,632 bytes for the original codec. Both graph caches use 26,259,456 bytes.
Worker scratch reservation is 268,435,456 bytes for 32 workers. Budget admission
is accounting-based; this run does not establish a cgroup-enforced hard limit.

Conclusion: codec memory shrinks substantially with unchanged recall on these
100 queries, but total process peak memory increases and I/O roughly triples.
The per-query payload reuse HashMap has no page-count bound, which contributes
variable memory as more candidates reach full4. This prototype is not yet an
effective total-memory solution. Next work must bound/account for transient
buffers uniformly and reduce full4 reads without unsafe low-dimensional pruning.
The budgets did not change BFS cache size because its node cap bound first;
their QPS differences must not be interpreted as a budget scaling effect.

Validation: release native disk build, successful exits for both variants,
asymmetric-score/reconstruction and FP16-subnormal tests passed via
`cargo test --manifest-path src/graph_core/Cargo.toml --bin run_diskann_fair adaptive_numerics_tests`.
Full lib-test compilation is blocked by existing disk_port.rs test calls missing
a prepare argument; the targeted binary tests pass. git diff --check passed.
