#!/usr/bin/env bash
# Build the canonical R64/Lbuild400 graphs required by suite 05 doctor.
set -euo pipefail

ROOT="${QGRAPH_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT"

BIN="${BIN:-$ROOT/src/graph_core/target/release/run_diskann_fair}"
DATASETS="${DATASETS:-agnews gist dbpedia}"
GRAPH_ROLES="${GRAPH_ROLES:-shared ours}"
THREADS="${THREADS:-32}"
BUILD_ONLY="${BUILD_ONLY:-0}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT/work/05_disk_system_fair/core_graph_build}"
LOG_ROOT="${LOG_ROOT:-$ROOT/logs/05_core_graphs}"

mkdir -p "$BUILD_ROOT" "$LOG_ROOT"
[[ -x "$BIN" ]] || { echo "missing executable: $BIN" >&2; exit 1; }

build_one() {
  local dataset="$1" method="$2" role="$3"
  local split_root="$ROOT/artifacts/query_splits/$dataset/shared"
  local query="$split_root/test_query.fvecs"
  local gt="$split_root/test_gt.ivecs"
  if [[ "$BUILD_ONLY" == "1" ]]; then
    query="$ROOT/data/$dataset/${dataset}_query.fvecs"
    gt="$ROOT/data/$dataset/${dataset}_groundtruth.ivecs"
  fi
  local stage_root="$BUILD_ROOT/$dataset/$role"
  local canonical_root="$ROOT/artifacts/graphs/$dataset"
  local legacy_root="$ROOT/artifacts/indexes/legacy_aliases/$dataset/indexes/02_diskann_fair"
  local source destination source_meta destination_meta

  if [[ "$role" == "shared" ]]; then
    source="$stage_root/$dataset/indexes/02_diskann_fair/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    destination="$canonical_root/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    source_meta="${source%.bin}.json"
    destination_meta="${destination%.bin}.json"
  elif [[ "$role" == "ours" ]]; then
    source="$stage_root/$dataset/indexes/02_diskann_fair/Ours/${dataset}_Ours_R64_Lbuild400.graph.bin"
    destination="$canonical_root/Ours/${dataset}_Ours_R64_Lbuild400.graph.bin"
    source_meta="${source%.bin}.json"
    destination_meta="${destination%.bin}.json"
  elif [[ "$role" == "diskann" ]]; then
    source="$stage_root/$dataset/indexes/02_diskann_fair/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    destination="$canonical_root/03_system_fair/DiskANN-PQ-Disk/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    source_meta="${source%.bin}.json"
    destination_meta="${destination%.bin}.json"
  else
    echo "unsupported graph role: $role" >&2
    exit 1
  fi

  mkdir -p "$canonical_root/shared_graph" "$canonical_root/Ours" "$legacy_root"
  for graph_role in shared_graph Ours; do
    if [[ ! -e "$legacy_root/$graph_role" && ! -L "$legacy_root/$graph_role" ]]; then
      ln -s "$(realpath --relative-to="$legacy_root" "$canonical_root/$graph_role")" "$legacy_root/$graph_role"
    fi
  done

  if [[ "$role" == "ours" && -s "$destination" && ! -s "$destination_meta" ]]; then
    local existing_log="$stage_root/02_diskann_fair/$dataset/logs/Ours/${dataset}_Ours_R64_Lbuild400.log"
    [[ -s "$existing_log" ]] || {
      echo "[$dataset][$role] graph exists but provenance log is missing: $existing_log" >&2
      exit 1
    }
    python3 scripts/write_ours_graph_meta.py \
      --dataset "$dataset" --graph "$destination" --build-log "$existing_log" \
      --output "$destination_meta"
  fi
  if [[ -s "$destination" && -s "$destination_meta" ]]; then
    echo "[$dataset][$role] canonical graph exists; skip"
    return
  fi
  [[ -s "$query" && -s "$gt" ]] || {
    echo "[$dataset][$role] missing non-empty test split under $split_root" >&2
    exit 1
  }

  mkdir -p "$stage_root" "$(dirname "$destination")"
  echo "[$dataset][$role] build start: $(date --iso-8601=seconds)"
  local extra=()
  if [[ "$BUILD_ONLY" == "1" ]]; then
    extra+=(--build-only)
  fi
  if [[ "$role" == "shared" || "$role" == "diskann" ]]; then
    extra+=(--shared-graph)
  fi
  "$BIN" \
    --dataset "$dataset" \
    --data-root "$ROOT/data" \
    --out-root "$stage_root" \
    --methods "$method" \
    --max-degree 64 \
    --build-beam 400 \
    --alpha 1.2 \
    --seed 20260813 \
    --query-coarse-codec int8 \
    --refine-passes 1 \
    --build-prune-cap 256 \
    --build-early-stop-hops 2 \
    --search-list-sizes 10 \
    --repeats 1 \
    --threads "$THREADS" \
    --query-path "$query" \
    --gt-path "$gt" \
    "${extra[@]}" \
    >"$LOG_ROOT/${dataset}_${role}.log" 2>&1

  [[ -s "$source" ]] || {
    echo "[$dataset][$role] build completed without expected graph: $source" >&2
    exit 1
  }
  if [[ "$role" == "ours" && ! -s "$source_meta" ]]; then
    local build_log="$stage_root/02_diskann_fair/$dataset/logs/Ours/${dataset}_Ours_R64_Lbuild400.log"
    python3 scripts/write_ours_graph_meta.py \
      --dataset "$dataset" --graph "$source" --build-log "$build_log" \
      --output "$source_meta"
  fi
  cp --reflink=auto "$source" "$destination"
  if [[ -s "$source_meta" ]]; then
    cp --reflink=auto "$source_meta" "$destination_meta"
  fi
  echo "[$dataset][$role] installed $destination ($(stat -c %s "$destination") bytes)"
}

for dataset in $DATASETS; do
  for role in $GRAPH_ROLES; do
    case "$role" in
      shared) build_one "$dataset" PQ shared ;;
      ours) build_one "$dataset" Ours ours ;;
      diskann) build_one "$dataset" PQ diskann ;;
      *) echo "unsupported GRAPH_ROLES entry: $role" >&2; exit 1 ;;
    esac
  done
done

echo "all requested core graphs are installed"
