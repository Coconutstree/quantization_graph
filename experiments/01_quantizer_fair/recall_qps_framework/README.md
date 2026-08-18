# 01 Fixed-Candidate Recall/QPS Framework

This layer reports pure quantizer behavior, not graph search behavior. All
methods must use the same queries and the same fixed candidate vectors. Exact
float32 L2 distances define the target ranking; each quantizer computes its own
compressed distance on those candidates.

For Experiment 01, `recall` means fixed-candidate Recall@10/top-k overlap
against exact L2 inside the shared candidate set. `qps` and
`latency_mean_us` measure compressed-distance evaluation over that same fixed
candidate set. Do not use HNSW `efSearch`, DiskANN `search_list_size`, Vamana
`L_search`, or other graph-search sweeps as Experiment 01 data.

The shared reporting schema is:

```text
suite,dataset,method,metric,k,nominal_bpd,actual_bytes_per_vector,index_size_mb,
peak_rss_mb,build_time_ms,train_time_ms,encode_time_ms,search_param_name,
search_param_value,recall,qps,latency_mean_us,latency_p50_us,latency_p95_us,
distance_calls,threads,repeat_id,git_commit
```

`latency_mean_us` is the mean time for one query vector, in microseconds.

For this experiment, `search_param_name` should describe the fixed candidate
budget, for example `fixed_candidates_k` or `candidate_size`.

Implemented first:
- `scripts/run_faiss_quantizer_fair.sh`: builds/reuses the shared fixed
  candidate set, runs Faiss PQ/SQ compressed-distance evaluation, and writes
  `faiss_quantizer_summary.csv`, `PQ_4bit_accuracy.csv`, and
  `SQ_4bit_accuracy.csv`.
- `runners/run_saq_b4.sh`
- `parsers/parse_saq_accuracy.py`
- `parsers/parse_saq_qps.py`

Readable raw log shape:

```text
experiment_profile=fixed_candidate_quantizer_fair
suite=01_quantizer_fair
dataset=...
method=PQ_4bit
candidate_size=460

# Each data row below uses the same query-candidate pairs.
candidate_size fixed_candidate_recall qps latency_mean_us latency_p50_us latency_p95_us distance_calls mean_relative_error p95_relative_error top10_overlap pairwise_flip_rate
460 ...
```

## SAQ Fixed-Candidate Runner

SAQ must be evaluated on the same fixed candidate file as PQ/SQ/Ours. The
runner loads raw `data/<dataset>/<dataset>_{base,query}.fvecs` for exact L2,
SAQ PCA/index files from `baselines/saq/data/<dataset>/`, and candidate ids
from `work/01_quantizer_fair/<dataset>/fixed_candidates_k<CANDIDATE_SIZE>.bin`.

Compile and run dbpedia:

```bash
cd /home/kai3/coco/quantization_graph
DATASETS="dbpedia" scripts/run_saq_fixed_candidates_fair.sh
```

Explicit dbpedia command:

```bash
cd /home/kai3/coco/quantization_graph
DATASETS="dbpedia" \
CANDIDATE_SIZE=1000 \
RERANK_CANDIDATES="10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460" \
scripts/run_saq_fixed_candidates_fair.sh
```

Outputs:

- `results/dbpedia/raw/01_quantizer_fair/SAQ/SAQ_B4_fixed_candidates.log`
- `results/dbpedia/csv/01_quantizer_fair/SAQ_B4_accuracy.csv`
- `results/dbpedia/csv/01_quantizer_fair/SAQ_B4_recall_qps.csv`
- appended summary row in `results/dbpedia/csv/01_quantizer_fair/faiss_quantizer_summary.csv`
