# Faiss PQ Accuracy Adapter

Current implementation: `legacy/01_quantizer_fair/faiss_quantizer_smoke.cpp`.

Configuration:
- Method name: `PQ`
- Faiss type: `faiss::ProductQuantizer`
- 4-bit setting: `pq_m = dimension`, `pq_nbits = 4`
- Metric: L2

Output:
- `results/${DATASET}/csv/01_quantizer_fair/faiss_PQ_fixed_candidate_raw.csv`
- `results/${DATASET}/csv/01_quantizer_fair/faiss_quantizer_summary.csv`

The current distance path decodes 4-bit PQ codes and computes L2 against the
reconstructed vector. This is correct for accuracy, but conservative for a pure
PQ ADC kernel-speed claim.
