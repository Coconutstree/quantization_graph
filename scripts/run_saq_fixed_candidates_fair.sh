#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}" || exit 1

CONFIG="${CONFIG:-experiments/01_quantizer_fair/configs/formal_saq_4bit.env}"
if [[ -f "${CONFIG}" ]]; then
  # shellcheck source=/dev/null
  source "${CONFIG}"
fi

DATA_ROOT="${DATA_ROOT:-${ROOT}/data}"
OUT_ROOT="${OUT_ROOT:-results/disk_environment}"
WORK_ROOT="${WORK_ROOT:-work}"
SAQ_ROOT="${SAQ_ROOT:-baselines/saq}"
SAQ_DATA_ROOT="${SAQ_DATA_ROOT:-${SAQ_ROOT}/data}"
SAQ_BUILD_DIR="${SAQ_BUILD_DIR:-${SAQ_ROOT}/build_gcc11}"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
SAQ_CXX_BIN="${SAQ_CXX_BIN:-$(command -v g++)}"
SAQ_CMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-${ROOT}/baselines/deps/local;/usr}"
BUILD_JOBS="${BUILD_JOBS:-16}"
CANDIDATE_SIZE="${CANDIDATE_SIZE:-1000}"
MAX_QUERIES="${MAX_QUERIES:-0}"
RERANK_CANDIDATES="${RERANK_CANDIDATES:-10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460,1000}"
REPEAT_ID="${REPEAT_ID:-0}"
GIT_COMMIT="${GIT_COMMIT:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}"

: "${DATASETS:?missing DATASETS; example: DATASETS=\"dbpedia\" scripts/run_saq_fixed_candidates_fair.sh}"

"${CMAKE_BIN}" \
  -S "${SAQ_ROOT}" \
  -B "${SAQ_BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="${SAQ_CXX_BIN}" \
  -DBUILD_UNIT_TESTS=OFF \
  -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH}" \
  -DCMAKE_MODULE_PATH="${SAQ_CMAKE_MODULE_PATH:-${ROOT}/baselines/deps/local/usr/share/glog/cmake}" \
  -DUnwind_INCLUDE_DIR="${SAQ_UNWIND_INCLUDE_DIR:-${ROOT}/baselines/deps/local/usr/include}" \
  -DUnwind_LIBRARY="${SAQ_UNWIND_LIBRARY:-${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu/libunwind.so}"

"${CMAKE_BIN}" --build "${SAQ_BUILD_DIR}" --target create_index test_qps test_relative_error -j "${BUILD_JOBS}"

for DATASET in ${DATASETS}; do
  RAW_DIR="${OUT_ROOT}/01_quantizer_fair/${DATASET}/logs/SAQ"
  mkdir -p "${RAW_DIR}"

  "${SAQ_ROOT}/bin/test_fixed_candidates" \
    -dataset "${DATASET}" \
    -B "${B:-4}" \
    -K "${K:-4096}" \
    -num_threads "${QUERY_THREADS:-1}" \
    -searcher_vars_bound_m "${SEARCHER_VARS_BOUND_M:-4}" \
    -saq_data_root "${SAQ_DATA_ROOT}" \
    -repo_data_root "${DATA_ROOT}" \
    -work_root "${WORK_ROOT}" \
    -out_root "${OUT_ROOT}" \
    -candidate_size "${CANDIDATE_SIZE}" \
    -max_queries "${MAX_QUERIES}" \
    -rerank_candidates "${RERANK_CANDIDATES}" \
    -repeat_id "${REPEAT_ID}" \
    -git_commit "${GIT_COMMIT}" \
    2>&1 | tee "${RAW_DIR}/SAQ_B4_fixed_candidates.log"
done
