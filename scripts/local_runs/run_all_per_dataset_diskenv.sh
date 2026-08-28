#!/usr/bin/env bash
# Full formal disk-suite per dataset (05a -> 05b -> 05c), dataset-major.
# For a real NVMe run: set DISK_ROOT to the mounted NVMe path first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"
export QG05_FAST=1
export QG05_OURS_ABLATIONS="${QG05_OURS_ABLATIONS:-full4-resident/no-gate}"
export PATH=/home/msy2025/.local/bin:$PATH
export LD_LIBRARY_PATH="${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu:${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu/openblas-pthread:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu:${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu/openblas-pthread:${LIBRARY_PATH:-}"
export CPLUS_INCLUDE_PATH="${ROOT}/baselines/deps/local/usr/include:${CPLUS_INCLUDE_PATH:-}"

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID_BASE="${RUN_ID_BASE:-formal_diskenv_${TS}}"
DISK_ROOT="${DISK_ROOT:-/home/msy2025/qgraph_nvme}"
OUT_ROOT="${OUT_ROOT:-results/disk_environment/.formal_runs}"
PORTS="${PORTS:-experiments/05_disk_system_fair/ports.local.json}"
DATASETS="${DATASETS:-agnews gist dbpedia}"

W_05A=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals)+",1000")')
W_BC=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')
M_05A="PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"
M_05B="PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk"
M_05C="Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { echo "[$(date '+%F %T')] $*"; }

run_phases() {
  local layer="$1" ds="$2" workers="$3" widths_env="$4" methods="$5"
  local rid="${RUN_ID_BASE}_${ds}_${layer}"
  log "===== ${ds} ${layer} run-id=${rid} workers=${workers} ====="
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "${rid}" --layers "${layer}" --datasets "${ds}" \
    --out-root "${OUT_ROOT}" --methods "${methods}"
  log "===== ${ds} ${layer} DONE ====="
}

log "FULL FORMAL START disk_root=${DISK_ROOT} datasets=${DATASETS}"
for DS in ${DATASETS}; do
  run_phases 05a "${DS}" 1 "${W_05A}" "${M_05A}"
  run_phases 05b "${DS}" 32 "${W_BC}" "${M_05B}"
  run_phases 05c "${DS}" 32 "${W_BC}" "${M_05C}"
  log "===== dataset ${DS} ALL DONE ====="
done
log "FULL FORMAL ALL DONE"
