#!/usr/bin/env bash
# Local 05B/05C debug driver. Worker-major: each worker runs 05B -> 05C for
# all requested datasets before the next worker starts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID_BASE="${RUN_ID_BASE:-debug_diskenv_${TS}}"
DATASETS="${DATASETS:-agnews gist dbpedia}"
WORKERS="${WORKERS:-${QG05_DEBUG_WORKERS}}"
WORKERS="${WORKERS//,/ }"

W=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')
METHODS_BC="PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { echo "[$(date '+%F %T')] $*"; }

for WK in ${WORKERS}; do
  log "===== worker ${WK} 05b/05c START ====="
  for DS in ${DATASETS}; do
    RID="${RUN_ID_BASE}_w${WK}_bc_${DS}"
    log "===== worker ${WK} dataset ${DS} run-id=${RID} ====="
    QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
      --phase export --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
      --workers "${WK}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0 \
      --storage-modes hybrid_disk,disk_payload
    QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
      --phase validate --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
      --workers "${WK}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0 \
      --storage-modes hybrid_disk,disk_payload
    QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
      --phase run --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
      --workers "${WK}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0 \
      --storage-modes hybrid_disk,disk_payload
    QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
      --phase plot --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --out-root "${OUT_ROOT}" --methods "${METHODS_BC}"
    log "===== worker ${WK} dataset ${DS} done ====="
  done
  log "===== worker ${WK} 05b/05c DONE ====="
done
log "ALL BC DATASETS DONE"
