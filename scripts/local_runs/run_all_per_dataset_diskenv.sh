#!/usr/bin/env bash
# Local debug disk-suite: worker-major. Each worker runs one full 05a -> 05b
# -> 05c pass before the next worker starts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID_BASE="${RUN_ID_BASE:-debug_diskenv_${TS}}"
DATASETS="${DATASETS:-agnews gist dbpedia}"
WORKERS="${WORKERS:-${QG05_DEBUG_WORKERS}}"
WORKERS="${WORKERS//,/ }"

W_05A=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals)+",1000")')
W_BC=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')
M_05A="PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"
M_05B="PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk"
M_05C="Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { echo "[$(date '+%F %T')] $*"; }

run_phases() {
  local layer="$1" ds="$2" workers="$3" widths_env="$4" methods="$5"
  local rid="${RUN_ID_BASE}_w${workers}_${ds}_${layer}"
  local storage_modes="hybrid_disk"
  if [[ "${layer}" == "05a" ]]; then
    storage_modes="resident,payload_on_ssd"
  elif [[ "${layer}" == "05b" ]]; then
    storage_modes="hybrid_disk,disk_payload"
  fi
  log "===== ${ds} ${layer} run-id=${rid} workers=${workers} ====="
  QG05_FAST_WIDTHS="${widths_env}" python3 src/disk_bench/run_disk_suite.py \
    --phase export --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0 \
    --storage-modes "${storage_modes}"
  QG05_FAST_WIDTHS="${widths_env}" python3 src/disk_bench/run_disk_suite.py \
    --phase validate --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0 \
    --storage-modes "${storage_modes}"
  QG05_FAST_WIDTHS="${widths_env}" python3 src/disk_bench/run_disk_suite.py \
    --phase run --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0 \
    --storage-modes "${storage_modes}"
  QG05_FAST_WIDTHS="${widths_env}" python3 src/disk_bench/run_disk_suite.py \
    --phase plot --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --out-root "${OUT_ROOT}" --methods "${methods}"
  log "===== ${ds} ${layer} DONE ====="
}

log "DEBUG WORKER-MAJOR START disk_root=${DISK_ROOT} disk_profile=${DISK_PROFILE} datasets=${DATASETS} workers=${WORKERS}"
python3 src/disk_bench/run_disk_suite.py \
  --phase doctor --layers 05a,05b,05c --datasets "${DATASETS// /,}" \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  || log "WARN: doctor reported failures; continuing"

for WK in ${WORKERS}; do
  log "===== worker ${WK} FULL 05a/05b/05c START ====="
  for DS in ${DATASETS}; do
    run_phases 05a "${DS}" "${WK}" "${W_05A}" "${M_05A}"
    run_phases 05b "${DS}" "${WK}" "${W_BC}" "${M_05B}"
    run_phases 05c "${DS}" "${WK}" "${W_BC}" "${M_05C}"
    log "===== worker ${WK} dataset ${DS} ALL DONE ====="
  done
  log "===== worker ${WK} FULL 05a/05b/05c DONE ====="
done
log "DEBUG WORKER-MAJOR ALL DONE"
