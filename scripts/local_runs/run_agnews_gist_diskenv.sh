#!/usr/bin/env bash
# Local agnews/gist debug driver. 05A is plotted from an existing run id; 05B/05C
# are worker-major so each worker completes before the next starts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID_BASE="${RUN_ID_BASE:-debug_diskenv_${TS}}"
DATASETS="${DATASETS:-agnews gist}"
WORKERS="${WORKERS:-${QG05_DEBUG_WORKERS}}"
WORKERS="${WORKERS//,/ }"
RID_05A="${RID_05A:-formal_diskenv_20260826_071928_05a}"

W_05A=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals)+",1000")')
W_BC=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')
METHODS_05A="PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"
METHODS_BC="PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { echo "[$(date '+%F %T')] $*"; }

for DS in ${DATASETS}; do
  log "===== dataset ${DS}: 05a plot ====="
  QG05_FAST_WIDTHS="${W_05A}" python3 src/disk_bench/run_disk_suite.py \
    --phase plot --run-id "${RID_05A}" --layers 05a --datasets "${DS}" \
    --out-root "${OUT_ROOT}" --methods "${METHODS_05A}" || log "WARN: 05a plot failed for ${DS}"
done

for WK in ${WORKERS}; do
  log "===== worker ${WK}: agnews/gist 05b/05c START ====="
  for DS in ${DATASETS}; do
    RID="${RUN_ID_BASE}_w${WK}_bc_${DS}"
    log "===== worker ${WK} dataset ${DS}: 05b/05c run-id=${RID} ====="
    QG05_FAST_WIDTHS="${W_BC}" python3 src/disk_bench/run_disk_suite.py \
      --phase export --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
      --workers "${WK}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
    QG05_FAST_WIDTHS="${W_BC}" python3 src/disk_bench/run_disk_suite.py \
      --phase validate --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
      --workers "${WK}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
    QG05_FAST_WIDTHS="${W_BC}" python3 src/disk_bench/run_disk_suite.py \
      --phase run --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
      --workers "${WK}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
    QG05_FAST_WIDTHS="${W_BC}" python3 src/disk_bench/run_disk_suite.py \
      --phase plot --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
      --out-root "${OUT_ROOT}" --methods "${METHODS_BC}"
    log "===== worker ${WK} dataset ${DS} 05b/05c done ====="
  done
  log "===== worker ${WK}: agnews/gist 05b/05c DONE ====="
done
log "ALL AGNEWS+GIST DONE"
