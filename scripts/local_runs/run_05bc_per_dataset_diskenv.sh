#!/usr/bin/env bash
# Run 05B and 05C per dataset (agnews -> gist -> dbpedia), dataset-major.
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

W=$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')
METHODS_BC="PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { echo "[$(date '+%F %T')] $*"; }

for DS in ${DATASETS}; do
  RID="${RUN_ID_BASE}_bc_${DS}"
  log "===== dataset ${DS} run-id=${RID} ====="
  QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers 32 --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers 32 --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers 32 --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${W}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "${RID}" --layers 05b,05c --datasets "${DS}" \
    --out-root "${OUT_ROOT}" --methods "${METHODS_BC}"
  log "===== dataset ${DS} done ====="
done
log "ALL BC DATASETS DONE"
