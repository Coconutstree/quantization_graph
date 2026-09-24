# Recall-QPS Runners

Runners call each method's official 4-bit search flow and keep the native raw
log.

Implemented:
- `run_saq_b4.sh`: wraps SAQ README commands for `B=4`.

Pending:
- `run_faiss_ivfpq4.py` or `.cpp`
- `run_faiss_ivfsq4.py` or `.cpp`
- `run_lvq4.py`
- `run_ours4.sh`

Do not use HNSW-PQ/SQ runner output as 01 Recall-QPS data. That belongs to a
separate HNSW/system experiment.
