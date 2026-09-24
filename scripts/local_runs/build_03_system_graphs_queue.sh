#!/usr/bin/env bash
# Build query-ready 03/05C indexes only. This script never runs search queries.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SIFT_SESSION="${SIFT_SESSION:-uci_sift10m_pipeline}"
DATASETS="${DATASETS:-agnews gist dbpedia sift10m}"
RUN_ID_BASE="${RUN_ID_BASE:-disk_03_graph_build_$(date +%Y%m%d)}"
DISK_ROOT="$ROOT/work/05_disk_system_fair/disk_root"
OUT_ROOT="$ROOT/results/archive/legacy_layout_20260918/disk_environment/.formal_runs"
LOG_ROOT="$ROOT/logs/05_core_graphs"

mkdir -p "$LOG_ROOT"
log() { printf '[03-graphs] %s %s\n' "$(date --iso-8601=seconds)" "$*"; }

log "waiting for $SIFT_SESSION"
while tmux has-session -t "$SIFT_SESSION" 2>/dev/null; do
  sleep 60
done

if [[ ! -f work/uci_sift10m/COMPLETE ]]; then
  log "ERROR: SIFT10M pipeline exited without COMPLETE; graph queue stopped"
  exit 1
fi

# Rebuild only binaries whose source changed.
native_build="build/disk/native"
glass_src="experiments/03_disk_system/native/glass_disk_port.cpp"
glass_bin="$native_build/qgraph05_glass_disk_port"
symphony_src="experiments/03_disk_system/native/symphonyqg_disk_port.cpp"
symphony_bin="$native_build/qgraph05_symphonyqg_disk_port"
if [[ ! -x "$glass_bin" || "$glass_src" -nt "$glass_bin" ||
      ! -x "$symphony_bin" || "$symphony_src" -nt "$symphony_bin" ]]; then
  log "building provenance-aware SymphonyQG and Glass ports"
  cmake --build "$native_build" \
    --target qgraph05_symphonyqg_disk_port qgraph05_glass_disk_port -j 8
fi

diskann_src="experiments/03_disk_system/native_diskann/src/main.rs"
diskann_bin="experiments/03_disk_system/native_diskann/target/release/qgraph05_diskann_port"
if [[ ! -x "$diskann_bin" || "$diskann_src" -nt "$diskann_bin" ]]; then
  log "building updated DiskANN-PQ export binary"
  cargo build --release --locked \
    --manifest-path experiments/03_disk_system/native_diskann/Cargo.toml
fi
python3 scripts/write_05_ports_local.py

# These three OG-LVQ indexes were built from byte-identical inputs with an
# export implementation that is byte-for-byte unchanged. Verify every source
# and copied file before exposing it to the current runner.
reuse_datasets=()
for dataset in $DATASETS; do
  case "$dataset" in
    agnews|gist|dbpedia) reuse_datasets+=("$dataset") ;;
  esac
done
if (( ${#reuse_datasets[@]} )); then
  log "auditing archived OG-LVQ indexes before copy-on-write staging"
  python3 scripts/local_runs/audit_stage_03c_og_reuse.py "${reuse_datasets[@]}"
fi

install_index() {
  local dataset="$1" method="$2" run_id="$3"
  local source="$DISK_ROOT/05_disk_system_fair/05C_disk_system_fair/$dataset/$method/hybrid_disk"
  local destination="$ROOT/artifacts/graphs/$dataset/03_system_fair/$method"
  local artifact_root="$OUT_ROOT/runs/$run_id/05C_disk_system_fair/$dataset/artifacts/export"
  local artifacts=("$artifact_root/${method}__hybrid_disk__B2__"*"__w1__r0.json")
  [[ ${#artifacts[@]} -eq 1 && -s "${artifacts[0]}" ]] || {
    log "ERROR: expected one export artifact for $dataset/$method under $artifact_root"
    exit 1
  }
  local artifact="${artifacts[0]}"
  local build_stats="${artifact%.json}.build_stats.json"

  [[ -d "$source" ]] || { log "ERROR: missing exported index $source"; exit 1; }
  mkdir -p "$destination"
  [[ -s "$artifact" ]] && cp --reflink=auto "$artifact" "$destination/export.json"
  [[ -s "$build_stats" ]] && cp --reflink=auto "$build_stats" "$destination/build_stats.json"

  if [[ "$method" == "Ours-Disk" ]]; then
    local graph="$ROOT/artifacts/graphs/$dataset/Ours/${dataset}_Ours_R64_Lbuild400.graph.bin"
    [[ -s "$graph" ]] || { log "ERROR: missing Ours source graph $graph"; exit 1; }
    ln -sfn "../../Ours/$(basename "$graph")" "$destination/source_graph.bin"
    ln -sfn "../../Ours/$(basename "${graph%.bin}.json")" "$destination/source_graph.json"
  elif [[ "$method" == "DiskANN-PQ-Disk" ]]; then
    local graph="$ROOT/artifacts/graphs/$dataset/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    [[ -s "$graph" ]] || { log "ERROR: missing DiskANN source graph $graph"; exit 1; }
    ln -sfn "../../shared_graph/$(basename "$graph")" "$destination/source_graph.bin"
    ln -sfn "../../shared_graph/$(basename "${graph%.bin}.json")" "$destination/source_graph.json"
  elif [[ "$method" == "SymphonyQG-DiskPort" ]]; then
    # SymphonyQG stores adjacency and FastScan edge payload in one row file.
    cp --reflink=auto "$source/node_rows.pages" "$destination/node_rows.pages"
    cp --reflink=auto "$source/rotator.bin" "$destination/rotator.bin"
    cp --reflink=auto "$source/index.meta" "$destination/index.meta"
  elif [[ "$method" == "OG-LVQ-DiskPort" ]]; then
    cp --reflink=auto "$source/graph.pages" "$destination/graph.pages"
    cp --reflink=auto "$source/index.meta" "$destination/index.meta"
  elif [[ "$method" == "Glass-NSG-DiskPort" ]]; then
    cp --reflink=auto "$source/graph.pages" "$destination/graph.pages"
    cp --reflink=auto "$source/index.meta" "$destination/index.meta"
  fi
  log "installed canonical graph view for $dataset/$method under artifacts/graphs"
}

methods_for_dataset() {
  case "$1" in
    agnews)
      # Current-format Ours payload already exists and matches its canonical graph.
      printf '%s\n' "SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"
      ;;
    gist|dbpedia)
      # OG-LVQ is staged from the verified archive; the native export returns immediately.
      printf '%s\n' "Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"
      ;;
    sift10m)
      printf '%s\n' "Ours-Disk,SymphonyQG-DiskPort,OG-LVQ-DiskPort,Glass-NSG-DiskPort,DiskANN-PQ-Disk"
      ;;
    *)
      log "ERROR: unsupported dataset $1"
      exit 1
      ;;
  esac
}

for dataset in $DATASETS; do
  run_id="${RUN_ID_BASE}_${dataset}"
  methods="$(methods_for_dataset "$dataset")"
  log "$dataset export/build start: $methods"
  QG05_FAST=1 QG05_CAPTURE_BUILD_STATS=1 \
    python3 src/disk_bench/run_disk_suite.py \
    --phase export \
    --layers 05c \
    --datasets "$dataset" \
    --methods "$methods" \
    --storage-modes hybrid_disk \
    --workers 1 \
    --repeats 1 \
    --val-queries 0 \
    --run-id "$run_id" \
    --disk-root "$DISK_ROOT" \
    --out-root "$OUT_ROOT"

  IFS=',' read -ra method_list <<< "$methods"
  for method in "${method_list[@]}"; do
    install_index "$dataset" "$method" "$run_id"
  done
  log "$dataset export/build complete"
done

log "ALL GRAPH INDEXES COMPLETE; no queries were run"
