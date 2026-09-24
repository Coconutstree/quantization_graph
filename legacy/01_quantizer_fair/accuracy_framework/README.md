# 01 Accuracy Framework

This layer measures 4-bit quantization accuracy and compressed-distance cost.
It is not an ANN search benchmark.

`scripts/run_faiss_quantizer_fair.sh` is the Experiment 01 entrypoint for
Faiss PQ/SQ. It evaluates compressed distances on the shared fixed-candidate
query/base pairs, then reports distance error, fixed-candidate Recall@10,
compressed-distance QPS, and per-query latency. Do not mix HNSW `efSearch`,
DiskANN `search_list_size`, or any graph-search parameter into this layer.

Each adapter should expose, or wrap, the method's official 4-bit encode and
distance-estimation path. All methods consume the same dataset and the same
query-candidate pairs, then emit the canonical accuracy CSV:

```text
suite,dataset,method,metric,nominal_bpd,actual_bytes_per_vector,index_size_mb,
train_time_ms,encode_time_ms,mean_relative_error,p95_relative_error,
mean_absolute_error,top10_overlap,pairwise_flip_rate,fixed_candidate_recall,
query_count,candidate_size,threads,repeat_id,git_commit
```

Current status:

| Adapter | Status |
|---|---|
| `faiss_pq` | Implemented by `../faiss_quantizer_smoke.cpp` for fixed candidates. |
| `faiss_sq` | Implemented by `../faiss_quantizer_smoke.cpp` for fixed candidates. |
| `saq` | Parser/wrapper scaffold added; official SAQ `test_relative_error` is the first source. |
| `lvq` | Pending. |
| `ours` | Pending. |
