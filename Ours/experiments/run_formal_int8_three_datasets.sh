#!/usr/bin/env bash
# Re-run the formal Ours search on the frozen R64/Lbuild400 graphs.
# Payload encoding first-touches NUMA node 0; timed single-thread search is
# then pinned to CPU 20. Baseline rows are preserved, while obsolete formal
# Ours full/b1/INT4 rows are removed after a successful INT8 run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

BIN="${BIN:-experiments/02_diskann_fair/target/release/run_diskann_fair}"
OUT_ROOT="${OUT_ROOT:-results}"
DATASETS="${DATASETS:-agnews gist dbpedia}"
SEARCH_CPU="${SEARCH_CPU:-20}"
ENCODE_CPUS="${ENCODE_CPUS:-0,4,8,12,16,20,24,28,32,36,40,44,48,52,56,60,64,68,72,76}"
BUILD_THREADS="${BUILD_THREADS:-20}"
SEARCH_SIZES="${SEARCH_SIZES:-10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460}"
RUN_LOG_DIR="${ROOT}/logs/formal_int8"
mkdir -p "${RUN_LOG_DIR}"

for dataset in ${DATASETS}; do
  graph="${OUT_ROOT}/02_diskann_fair/${dataset}/indexes/Ours/${dataset}_Ours_R64_Lbuild400.graph.bin"
  query="${OUT_ROOT}/03_system_fair/${dataset}/csv/_query_splits/test_query.fvecs"
  gt="${OUT_ROOT}/03_system_fair/${dataset}/csv/_query_splits/test_gt.ivecs"
  progress="${OUT_ROOT}/02_diskann_fair/${dataset}/logs/Ours/${dataset}_Ours_R64_Lbuild400.log"
  stdout_log="${RUN_LOG_DIR}/${dataset}.stdout.log"
  for required in "${BIN}" "${graph}" "${query}" "${gt}"; do
    if [[ ! -e "${required}" ]]; then
      echo "missing required input: ${required}" >&2
      exit 1
    fi
  done

  echo "[formal-int8] ${dataset}: encode on node-0 physical cores"
  taskset -c "${ENCODE_CPUS}" "${BIN}" \
    --dataset "${dataset}" --methods Ours \
    --max-degree 64 --build-beam 400 \
    --centroid-count 1 --centroid-train-samples 100000 \
    --refine-passes 2 --repeats 1 --threads 1 \
    --build-threads "${BUILD_THREADS}" \
    --search-list-sizes "${SEARCH_SIZES}" \
    --query-coarse-codec int8 \
    --b1-epsilon 1.9 --rerank-candidates 100 \
    --out-root "${OUT_ROOT}" \
    --graph-file "${graph}" \
    --query-path "${query}" --gt-path "${gt}" \
    >"${stdout_log}" 2>&1 &
  pid=$!

  cleanup() {
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  }
  trap cleanup INT TERM EXIT
  while kill -0 "${pid}" 2>/dev/null; do
    if [[ -f "${progress}" ]] &&
       rg -q 'progress_stage=payload_encode status=done' "${progress}"; then
      taskset -pc "${SEARCH_CPU}" "${pid}" >&2
      echo "[formal-int8] ${dataset}: search pinned to CPU ${SEARCH_CPU}"
      break
    fi
    sleep 1
  done
  wait "${pid}"
  trap - INT TERM EXIT

  python - "${OUT_ROOT}/02_diskann_fair/${dataset}/csv/diskann_fair_raw.csv" <<'PY'
import sys
from pathlib import Path
from Ours.experiments.run_ours import keep_only_formal_int8_rows

keep_only_formal_int8_rows(Path(sys.argv[1]), 64)
PY
  mkdir -p "${ROOT}/Ours/logs/${dataset}"
  cp "${progress}" "${ROOT}/Ours/logs/${dataset}/$(basename "${progress}")"

  python experiments/03_system_fair/run_system_fair.py \
    --dataset "${dataset}" --systems Ours --run --repeats 1 --threads 1 \
    --out-root "${OUT_ROOT}" --overwrite-systems Ours
  echo "[formal-int8] ${dataset}: complete"
done
