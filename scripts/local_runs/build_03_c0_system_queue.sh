#!/usr/bin/env bash
# After UCI SIFT10M is ready, run 03/05C on datasets with completed formal graphs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

THREADS="${THREADS:-80}"
WORKERS="${WORKERS:-32}"
SIFT_SESSION="${SIFT_SESSION:-uci_sift10m_pipeline}"
DATASETS_CSV="${DATASETS_CSV:-agnews,gist,dbpedia,sift10m}"
RUN_ID="${RUN_ID:-disk_03_c0_pilot_$(date +%Y%m%d)}"
METHODS="Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"
FAST_WIDTHS="${FAST_WIDTHS:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460,500,540,580}"

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
sift_shared="artifacts/graphs/sift10m/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
sift_diskann="artifacts/graphs/sift10m/03_system_fair/DiskANN-PQ-Disk/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
mkdir -p "$(dirname "$sift_diskann")"
if [[ ! -s "$sift_diskann" ]]; then
  cp --reflink=auto "$sift_shared" "$sift_diskann"
  cp --reflink=auto "${sift_shared%.bin}.json" "${sift_diskann%.bin}.json"
fi

for dataset in agnews gist dbpedia sift10m; do
  system_root="artifacts/graphs/$dataset/03_system_fair"
  mkdir -p "$system_root"
  if [[ ! -e "$system_root/Ours-Disk" && ! -L "$system_root/Ours-Disk" ]]; then
    ln -s ../../Ours "$system_root/Ours-Disk"
  fi
done

log "incrementally verifying the updated SymphonyQG and DiskANN-PQ ports"
cmake --build build/disk/native \
  --target qgraph05_symphonyqg_disk_port -j "${BUILD_JOBS:-32}"
cargo build --release --locked --manifest-path experiments/03_disk_system/native_diskann/Cargo.toml
python3 scripts/write_05_ports_local.py

common=(
  --layers 05c
  --datasets "$DATASETS_CSV"
  --methods "$METHODS"
  --storage-modes hybrid_disk
  --workers "$WORKERS"
  --repeats 1
  --val-queries 0
  --run-id "$RUN_ID"
)

log "pilot widths=$FAST_WIDTHS"
log "doctor"
QG05_FAST=1 QG05_FAST_WIDTHS="$FAST_WIDTHS" \
  python3 src/disk_bench/orchestrator.py --phase doctor "${common[@]}"
for phase in export validate tune run plot; do
  log "$phase"
  QG05_FAST=1 QG05_FAST_WIDTHS="$FAST_WIDTHS" \
    python3 src/disk_bench/orchestrator.py --phase "$phase" "${common[@]}"
done
log "PILOT COMPLETE run_id=$RUN_ID"
