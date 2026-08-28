#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID_BASE="${RUN_ID_BASE:-formal_diskenv_${TS}}"
DISK_ROOT="${DISK_ROOT:-/home/msy2025/qgraph_nvme}"
OUT_ROOT="${OUT_ROOT:-results/disk_environment/.formal_runs}"
PORTS="${PORTS:-experiments/05_disk_system_fair/ports.local.json}"
DATASETS="${DATASETS:-agnews,gist,dbpedia}"

export QG05_FAST=1
export QG05_OURS_ABLATIONS="${QG05_OURS_ABLATIONS:-full4-resident/no-gate}"
export PATH=/home/msy2025/.local/bin:$PATH
export LD_LIBRARY_PATH="${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu:${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu/openblas-pthread:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu:${ROOT}/baselines/deps/local/usr/lib/x86_64-linux-gnu/openblas-pthread:${LIBRARY_PATH:-}"
export CPLUS_INCLUDE_PATH="${ROOT}/baselines/deps/local/usr/include:${CPLUS_INCLUDE_PATH:-}"

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

log "RUN_ID_BASE=${RUN_ID_BASE} datasets=${DATASETS}"
mkdir -p "${OUT_ROOT}"

python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase doctor --layers 05a,05b,05c --datasets "${DATASETS}" \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
  || log "WARN: doctor reported failures; continuing"

run_layer() {
  local layer="$1" workers="$2" widths_env="$3" methods="$4"
  local rid="${RUN_ID_BASE}_${layer}"
  log "== layer ${layer} run-id=${rid} workers=${workers} =="
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile nvme --out-root "${OUT_ROOT}" \
    --workers "${workers}" --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
  QG05_FAST_WIDTHS="${widths_env}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "${rid}" --layers "${layer}" --datasets "${DATASETS}" \
    --out-root "${OUT_ROOT}" --methods "${methods}"
  log "== layer ${layer} done =="
}

run_layer 05a 1 "${W_05A}" "PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"
run_layer 05b 32 "${W_05B}" "PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk"
run_layer 05c 32 "${W_05C}" "Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log "ALL DONE"
