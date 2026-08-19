#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
DATA_ROOT="${DATA_ROOT:-${ROOT_DIR}/data}"
OUT_ROOT="${OUT_ROOT:-${ROOT_DIR}/results}"
SAQ_ROOT="${SAQ_ROOT:-${ROOT_DIR}/baselines/saq}"

: "${DATASETS:?missing DATASETS; pass it explicitly, e.g. DATASETS=\"dbpedia gist agnews\"}"

B="${B:-4}"
K="${K:-4096}"
BUILD_THREADS="${BUILD_THREADS:-64}"
QUERY_THREADS="${QUERY_THREADS:-1}"
FIX_NPROBE="${FIX_NPROBE:-0}"
SEARCHER_VARS_BOUND_M="${SEARCHER_VARS_BOUND_M:-4}"
RUN_PREP="${RUN_PREP:-0}"
RUN_CREATE_INDEX="${RUN_CREATE_INDEX:-1}"
RUN_ACCURACY="${RUN_ACCURACY:-1}"
RUN_QPS="${RUN_QPS:-1}"
PYTHON_BIN="${PYTHON_BIN:-python}"

PARSER_DIR="${ROOT_DIR}/experiments/01_quantizer_fair/recall_qps_framework/parsers"

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "missing required file: ${path}" >&2
    return 1
  fi
}

link_dataset_file() {
  local source="$1"
  local target="$2"
  require_file "${source}"
  if [[ -e "${target}" && ! -L "${target}" ]]; then
    return 0
  fi
  ln -sfn "$(realpath "${source}")" "${target}"
}

check_saq_prep() {
  local dataset="$1"
  local dir="${SAQ_ROOT}/data/${dataset}"
  require_file "${dir}/${dataset}_centroid_${K}.fvecs"
  require_file "${dir}/${dataset}_cluster_id_${K}.ivecs"
  require_file "${dir}/${dataset}_base_pca.fvecs"
  require_file "${dir}/${dataset}_query_pca.fvecs"
  require_file "${dir}/${dataset}_centroid_${K}_pca.fvecs"
  require_file "${dir}/${dataset}_base_pca.vars.fvecs"
}

for DATASET in ${DATASETS}; do
  SRC_DIR="${DATA_ROOT}/${DATASET}"
  SAQ_DATA_DIR="${SAQ_ROOT}/data/${DATASET}"
  RAW_DIR="${OUT_ROOT}/01_quantizer_fair/${DATASET}/logs/SAQ"
  CSV_DIR="${OUT_ROOT}/${DATASET}/csv/01_quantizer_fair"
  mkdir -p "${SAQ_DATA_DIR}" "${RAW_DIR}" "${CSV_DIR}"

  link_dataset_file "${SRC_DIR}/${DATASET}_base.fvecs" "${SAQ_DATA_DIR}/${DATASET}_base.fvecs"
  link_dataset_file "${SRC_DIR}/${DATASET}_query.fvecs" "${SAQ_DATA_DIR}/${DATASET}_query.fvecs"
  link_dataset_file "${SRC_DIR}/${DATASET}_groundtruth.ivecs" "${SAQ_DATA_DIR}/${DATASET}_groundtruth.ivecs"

  if [[ "${RUN_PREP}" == "1" ]]; then
    (
      cd "${SAQ_ROOT}"
      PYTHONUNBUFFERED=1 "${PYTHON_BIN}" python/ivf.py "${DATASET}" "${K}" \
        2>&1 | tee "${RAW_DIR}/SAQ_B${B}_ivf_k${K}.log"
      PYTHONUNBUFFERED=1 "${PYTHON_BIN}" python/pca.py "${DATASET}" \
        2>&1 | tee "${RAW_DIR}/SAQ_B${B}_pca.log"
    )
  fi

  if ! check_saq_prep "${DATASET}"; then
    echo "${DATASET}: SAQ IVF/PCA files are missing." >&2
    echo "Set RUN_PREP=1 to run baselines/saq/python/ivf.py and pca.py first." >&2
    exit 1
  fi

  if [[ "${RUN_CREATE_INDEX}" == "1" ]]; then
    (
      cd "${SAQ_ROOT}"
      ./bin/create_index \
        -dataset "${DATASET}" \
        -B "${B}" \
        -K "${K}" \
        -num_threads "${BUILD_THREADS}" \
        2>&1 | tee "${RAW_DIR}/SAQ_B${B}_create_index.log"
    )
  fi

  if [[ "${RUN_ACCURACY}" == "1" ]]; then
    (
      cd "${SAQ_ROOT}"
      ./bin/test_relative_error \
        -dataset "${DATASET}" \
        -B "${B}" \
        -K "${K}" \
        -num_threads "${QUERY_THREADS}" \
        -searcher_vars_bound_m "${SEARCHER_VARS_BOUND_M}" \
        2>&1 | tee "${RAW_DIR}/SAQ_B${B}_relative_error.log"
    )
    "${PYTHON_BIN}" "${PARSER_DIR}/parse_saq_accuracy.py" \
      --dataset "${DATASET}" \
      --native-root "${SAQ_ROOT}/results/saq" \
      --out "${CSV_DIR}/SAQ_B${B}_accuracy.csv" \
      --B "${B}" \
      --K "${K}" \
      --searcher-vars-bound-m "${SEARCHER_VARS_BOUND_M}" \
      --threads "${QUERY_THREADS}"
  fi

  if [[ "${RUN_QPS}" == "1" ]]; then
    (
      cd "${SAQ_ROOT}"
      ./bin/test_qps \
        -dataset "${DATASET}" \
        -B "${B}" \
        -K "${K}" \
        -fix_thread "${QUERY_THREADS}" \
        -fix_nprobe "${FIX_NPROBE}" \
        -searcher_vars_bound_m "${SEARCHER_VARS_BOUND_M}" \
        2>&1 | tee "${RAW_DIR}/SAQ_B${B}_qps.log"
    )
    "${PYTHON_BIN}" "${PARSER_DIR}/parse_saq_qps.py" \
      --dataset "${DATASET}" \
      --native-root "${SAQ_ROOT}/results/saq" \
      --out "${CSV_DIR}/SAQ_B${B}_recall_qps.csv" \
      --B "${B}" \
      --K "${K}" \
      --fix-thread "${QUERY_THREADS}" \
      --fix-nprobe "${FIX_NPROBE}" \
      --searcher-vars-bound-m "${SEARCHER_VARS_BOUND_M}"
  fi
done
