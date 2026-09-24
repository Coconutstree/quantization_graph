# Faiss SQ Accuracy Adapter

Current implementation: `legacy/01_quantizer_fair/faiss_quantizer_smoke.cpp`.

Configuration:
- Method name: `SQ`
- Faiss type: `faiss::ScalarQuantizer`
- 4-bit setting: `QT_4bit`
- Metric: L2

Output:
- `results/${DATASET}/csv/01_quantizer_fair/faiss_SQ_fixed_candidate_raw.csv`
- `results/${DATASET}/csv/01_quantizer_fair/faiss_quantizer_summary.csv`
