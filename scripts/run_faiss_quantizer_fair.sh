#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}" || exit 1

CONFIG="${CONFIG:-experiments/01_quantizer_fair/configs/formal_faiss_4bit.env}"
if [[ -f "${CONFIG}" ]]; then
  # shellcheck source=/dev/null
  source "${CONFIG}"
fi

DATA_ROOT="${DATA_ROOT:-${ROOT}/data}"
OUT_ROOT="${OUT_ROOT:-results}"
WORK_ROOT="${WORK_ROOT:-work}"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++-11}"
BUILD_DIR="${BUILD_DIR:-build/01_quantizer_fair}"

: "${DATASETS:?missing DATASETS; set it in ${CONFIG}}"
: "${METHODS:?missing METHODS; set it in ${CONFIG}}"
: "${MAX_TRAIN:?missing MAX_TRAIN; set it in ${CONFIG}}"
: "${MAX_QUERIES:?missing MAX_QUERIES; set it in ${CONFIG}}"
: "${REBUILD_CANDIDATES:?missing REBUILD_CANDIDATES; set it in ${CONFIG}}"
: "${BUILD_JOBS:?missing BUILD_JOBS; set it in ${CONFIG}}"
: "${REPEAT_ID:?missing REPEAT_ID; set it in ${CONFIG}}"
: "${SEED:?missing SEED; set it in ${CONFIG}}"
: "${CANDIDATE_SIZE:?missing CANDIDATE_SIZE; set it in ${CONFIG}}"
: "${RERANK_CANDIDATES:?missing RERANK_CANDIDATES; set it in ${CONFIG}}"
: "${HARD_NEGATIVE_SEARCH_K:?missing HARD_NEGATIVE_SEARCH_K; set it in ${CONFIG}}"
: "${HARD_NEGATIVE_HNSW_M:?missing HARD_NEGATIVE_HNSW_M; set it in ${CONFIG}}"
: "${HARD_NEGATIVE_EF_CONSTRUCTION:?missing HARD_NEGATIVE_EF_CONSTRUCTION; set it in ${CONFIG}}"
: "${HARD_NEGATIVE_EF_SEARCH:?missing HARD_NEGATIVE_EF_SEARCH; set it in ${CONFIG}}"

python scripts/check_datasets.py \
  --datasets ${DATASETS} \
  --data-root "${DATA_ROOT}" \
  --out-root "${OUT_ROOT}"

python scripts/write_quantizer_manifest.py \
  --datasets ${DATASETS} \
  --out-root "${OUT_ROOT}"

"${CMAKE_BIN}" \
  -S experiments/01_quantizer_fair \
  -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="${CXX_BIN}"

"${CMAKE_BIN}" \
  --build "${BUILD_DIR}" \
  -j "${BUILD_JOBS}"

for DATASET in ${DATASETS}; do
  WORK_DIR="${WORK_ROOT}/01_quantizer_fair/${DATASET}"
  mkdir -p "${WORK_DIR}"

  CANDIDATE_BIN="${WORK_DIR}/fixed_candidates_k${CANDIDATE_SIZE}.bin"
  CANDIDATE_META="${WORK_DIR}/fixed_candidates_k${CANDIDATE_SIZE}.meta.json"
  if [[ "${REBUILD_CANDIDATES}" != "1" && -f "${CANDIDATE_BIN}" && -f "${CANDIDATE_META}" ]]; then
    echo "${DATASET}: reuse ${CANDIDATE_BIN}"
  else
    "${BUILD_DIR}/faiss_hard_negative_candidates" \
      --dataset "${DATASET}" \
      --data-root "${DATA_ROOT}" \
      --out-root "${OUT_ROOT}" \
      --candidate-root "${WORK_ROOT}" \
      --candidate-size "${CANDIDATE_SIZE}" \
      --search-k "${HARD_NEGATIVE_SEARCH_K}" \
      --hnsw-M "${HARD_NEGATIVE_HNSW_M}" \
      --efConstruction "${HARD_NEGATIVE_EF_CONSTRUCTION}" \
      --efSearch "${HARD_NEGATIVE_EF_SEARCH}" \
      --seed "${SEED}" \
      --force \
      2>&1 | tee "${WORK_DIR}/faiss_hard_negative_candidates.log"
  fi

  "${BUILD_DIR}/faiss_quantizer_smoke" \
    --dataset "${DATASET}" \
    --data-root "${DATA_ROOT}" \
    --out-root "${OUT_ROOT}" \
    --candidate-root "${WORK_ROOT}" \
    --candidate-size "${CANDIDATE_SIZE}" \
    --max-train "${MAX_TRAIN}" \
    --max-queries "${MAX_QUERIES}" \
    --methods "${METHODS}" \
    --rerank-candidates "${RERANK_CANDIDATES}" \
    --repeat-id "${REPEAT_ID}" \
    --seed "${SEED}" \
    --accuracy-only \
    --overwrite-summary \
    2>&1 | tee "${WORK_DIR}/faiss_quantizer_summary.log"
done

python scripts/export_paper_quantizer_table.py \
  --datasets ${DATASETS} \
  --out-root "${OUT_ROOT}" \
  --work-root "${WORK_ROOT}"
