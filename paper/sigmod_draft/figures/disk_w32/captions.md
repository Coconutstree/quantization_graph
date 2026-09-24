## fig01_throughput

Throughput at high recall. Each curve retains every measured search-width setting; markers appear every fifth setting for readability. All panels use the same logarithmic vertical scale. Ours denotes ExRaBitQ-Disk with locality layout; SymphonyQG, OG-LVQ, and Glass-NSG denote disk ports. DBpedia has only two completed systems. Measurements use 32 workers and C0 (no cross-query caching), with one run per setting; no between-run uncertainty is estimated.

## fig02_read_volume

Read volume at high recall. Mean bytes read per query are expressed in MiB, with identical logarithmic vertical scales across datasets. The method identities and missing-system scope are those of Figure~\ref{fig:fig01_throughput}. Higher throughput need not imply fewer transferred bytes. Measurements use 32 workers and C0 (no cross-query caching), with one run per setting; no between-run uncertainty is estimated.

## fig03_tail_latency

Tail latency at high recall. The 95th percentile is computed over test queries within each setting, not over repeated runs. Vertical scales are logarithmic and identical across datasets. Method identities follow Figure~\ref{fig:fig01_throughput}. Measurements use 32 workers and C0 (no cross-query caching), with one run per setting; no between-run uncertainty is estimated.

## fig04_screening_io

Screening and page demand in the locality variant. Top: mean DB1 checks and survivors per query, on logarithmic axes. Bottom: mean primary, reranking, and other 4 KiB read pages per query, on a linear vertical scale and logarithmic search-width axis. Other reads equal total read sectors minus primary and reranking pages. These counters do not isolate a causal screening benefit. Measurements use 32 workers and C0 (no cross-query caching), with one run per setting; no between-run uncertainty is estimated.

Supplemental fig05_full_sweep shows all recall values for throughput; fig06_requests shows I/O requests per query at high recall. Same measurement and method scope.
