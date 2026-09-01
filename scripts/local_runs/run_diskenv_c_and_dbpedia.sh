#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

RUN_ID_BASE="${RUN_ID_BASE:-formal_diskenv_20260827_c_dbpedia}"

W_BASE="$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')"
W_05A="${W_BASE},1000"
W_BC="${W_BASE}"
M_05A="PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"
M_05B="PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk"
M_05C="Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { echo "[$(date '+%F %T')] $*"; }

run_phase_set() {
  local rid="$1" layer="$2" ds="$3" workers="$4" widths="$5" methods="$6"
  log "===== ${ds} ${layer} run-id=${rid} workers=${workers} ====="
  QG05_FAST_WIDTHS="${widths}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --out-root "${OUT_ROOT}" --methods "${methods}"
  log "===== ${ds} ${layer} DONE ====="
}

log "START only disk-environment fast run: agnews/gist 05C, dbpedia 05A/05B/05C"
for ds in agnews gist; do
  run_phase_set "formal_diskenv_20260826_114755_bc_${ds}" 05c "${ds}" 32 "${W_BC}" "${M_05C}"
done

for layer in 05a 05b 05c; do
  case "${layer}" in
    05a) run_phase_set "${RUN_ID_BASE}_dbpedia_05a" 05a dbpedia 1 "${W_05A}" "${M_05A}" ;;
    05b) run_phase_set "${RUN_ID_BASE}_dbpedia_05b" 05b dbpedia 32 "${W_BC}" "${M_05B}" ;;
    05c) run_phase_set "${RUN_ID_BASE}_dbpedia_05c" 05c dbpedia 32 "${W_BC}" "${M_05C}" ;;
  esac
done
log "ALL REQUESTED DISK-ENV RUNS DONE"
