# SAQ Accuracy Adapter

Official README commands:

```bash
cd baselines/saq
./bin/create_index -dataset ${DATASET} -B 4
./bin/test_relative_error -dataset ${DATASET} -B 4
```

Native output is written under `baselines/saq/results/saq/`. Convert it to the
canonical accuracy CSV with:

```bash
python experiments/01_quantizer_fair/recall_qps_framework/parsers/parse_saq_accuracy.py \
  --dataset ${DATASET} \
  --native-root baselines/saq/results/saq \
  --out results/${DATASET}/csv/01_quantizer_fair/SAQ_B4_accuracy.csv
```

SAQ requires its README preprocessing files before `create_index`:
- `${DATASET}_centroid_4096*.fvecs`
- `${DATASET}_cluster_id_4096.ivecs`
- `${DATASET}_base_pca.fvecs`
- `${DATASET}_query_pca.fvecs`
- `${DATASET}_base_pca.vars.fvecs`
