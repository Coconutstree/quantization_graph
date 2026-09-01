#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID_BASE="${RUN_ID_BASE:-debug_diskenv_${TS}}"
DATASETS="${DATASETS:-agnews,gist,dbpedia}"
WORKERS="${WORKERS:-${QG05_DEBUG_WORKERS}}"
WORKERS="${WORKERS//,/ }"

widths() {
  python3 -c '
vals=list(range(10,31))
vals.extend(range(40,101,10))
vals.extend(range(140,581,40))
print(",".join(str(v) for v in vals))
'
}
W_BASE="$(widths)"
W_05A="${W_BASE},1000"
W_05B="${W_BASE}"
W_05C="${W_BASE}"

log() { echo "[$(date '+%F %T')] $*"; }

log "RUN_ID_BASE=${RUN_ID_BASE} datasets=${DATASETS} workers=${WORKERS}"
mkdir -p "${OUT_ROOT}"

python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase doctor --layers 05a,05b,05c --datasets "${DATASETS}" \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  || log "WARN: doctor reported failures; continuing"

run_layer() {
  local layer="$1" workers="$2" widths_env="$3" methods="$4"
  local rid="${RUN_ID_BASE}_w${workers}_${layer}"
  log "== layer ${layer} run-id=${rid} workers=${workers} =="
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --out-root "${OUT_ROOT}" --methods "${methods}"
  log "== layer ${layer} done =="
}

for WK in ${WORKERS}; do
  log "== worker ${WK} full 05a/05b/05c start =="
  run_layer 05a "${WK}" "${W_05A}" "PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"
  run_layer 05b "${WK}" "${W_05B}" "PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk"
  run_layer 05c "${WK}" "${W_05C}" "Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"
  log "== worker ${WK} full 05a/05b/05c done =="
done

log "ALL DONE"
