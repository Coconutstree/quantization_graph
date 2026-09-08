#!/usr/bin/env bash
# After UCI SIFT10M is ready, run the five 03/05C systems under C0.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

THREADS="${THREADS:-80}"
WORKERS="${WORKERS:-32}"
SIFT_SESSION="${SIFT_SESSION:-uci_sift10m_pipeline}"
DATASETS_CSV="${DATASETS_CSV:-sift10m,bigann10m,deep1B}"
RUN_ID="${RUN_ID:-disk_03_c0_scale_$(date +%Y%m%d)}"
METHODS="Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"

log() { printf '[03-c0] %s %s\n' "$(date --iso-8601=seconds)" "$*"; }

log "waiting for $SIFT_SESSION"
while tmux has-session -t "$SIFT_SESSION" 2>/dev/null; do
  sleep 60
done

if [[ ! -f work/uci_sift10m/COMPLETE ]]; then
  log "ERROR: SIFT10M pipeline exited without COMPLETE; queue stopped"
  exit 1
fi

# The SIFT shared graph was built with DiskANN's float32 Vamana construction,
# so install it as DiskANN-PQ's 03 source without rebuilding identical topology.
sift_shared="results/graph/sift10m/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
sift_diskann="results/graph/sift10m/03_system_fair/DiskANN-PQ-Disk/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
mkdir -p "$(dirname "$sift_diskann")"
if [[ ! -s "$sift_diskann" ]]; then
  cp --reflink=auto "$sift_shared" "$sift_diskann"
  cp --reflink=auto "${sift_shared%.bin}.json" "${sift_diskann%.bin}.json"
fi

log "building only the 03-native Ours and DiskANN-PQ graphs for BIGANN10M/Deep1B"
DATASETS="bigann10m deep1B" GRAPH_ROLES="ours diskann" THREADS="$THREADS" \
  bash scripts/local_runs/build_05_core_graphs.sh

for dataset in sift10m bigann10m deep1B; do
  system_root="results/graph/$dataset/03_system_fair"
  mkdir -p "$system_root"
  if [[ ! -e "$system_root/Ours-Disk" && ! -L "$system_root/Ours-Disk" ]]; then
    ln -s ../../Ours "$system_root/Ours-Disk"
  fi
done

log "rebuilding native ports, including the updated SymphonyQG port"
JOBS="${BUILD_JOBS:-32}" bash scripts/build_formal_local.sh
python3 scripts/write_05_ports_local.py

common=(
  --layers 05c
  --datasets "$DATASETS_CSV"
  --methods "$METHODS"
  --storage-modes hybrid_disk
  --workers "$WORKERS"
  --val-queries 0
  --run-id "$RUN_ID"
)

log "doctor"
python3 experiments/05_disk_system_fair/orchestrator.py --phase doctor "${common[@]}"
for phase in export validate tune run plot; do
  log "$phase"
  python3 experiments/05_disk_system_fair/orchestrator.py --phase "$phase" "${common[@]}"
done
log "ALL COMPLETE run_id=$RUN_ID"
