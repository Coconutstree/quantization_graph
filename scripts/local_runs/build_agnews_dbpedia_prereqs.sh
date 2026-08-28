#!/usr/bin/env bash
# Build disk-suite prerequisites for agnews and dbpedia (gist already restored).
set -uo pipefail
REPO=/home/msy2025/quantization_graph
cd "$REPO"
export LD_LIBRARY_PATH="$REPO/baselines/deps/local/usr/lib/x86_64-linux-gnu:$REPO/baselines/deps/local/usr/lib/x86_64-linux-gnu/openblas-pthread:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="$REPO/baselines/deps/local/usr/lib/x86_64-linux-gnu:$REPO/baselines/deps/local/usr/lib/x86_64-linux-gnu/openblas-pthread:${LIBRARY_PATH:-}"
export CPLUS_INCLUDE_PATH="$REPO/baselines/deps/local/usr/include:${CPLUS_INCLUDE_PATH:-}"
LOG="$REPO/logs/prereqs_agnews_dbpedia.log"
log() { echo "[prereq] $*" >> "$LOG"; echo "[prereq] $*"; }

BIN01_CAND="$REPO/build/formal_local/01_quantizer_fair/faiss_hard_negative_candidates"
BIN02="$REPO/experiments/02_diskann_fair/target/release/run_diskann_fair"

step_candidates() {
  local ds="$1"
  log "== $ds: 01 fixed candidates =="
  if [[ -f "work/01_quantizer_fair/$ds/fixed_candidates_k1000.bin" && -f "work/01_quantizer_fair/$ds/fixed_candidates_k1000.meta.json" ]]; then
    log "$ds candidates already exist; skip"
    return 0
  fi
  "$BIN01_CAND" --dataset "$ds" --data-root "$REPO/data" --out-root "$REPO/results" \
    --candidate-root work --candidate-size 1000 --search-k 2000 --hnsw-M 32 \
    --efConstruction 200 --efSearch 2000 --seed 20260813 --force > "logs/prereq_${ds}_candidates.log" 2>&1
  local rc=$?
  if [[ $rc -ne 0 ]]; then log "$ds candidates FAILED rc=$rc"; return $rc; fi
  log "$ds candidates done"
}

step_saq() {
  local ds="$1"
  log "== $ds: SAQ data (ivf/pca/create_index) =="
  if [[ -f "baselines/saq/data/$ds/ivf4096_b4_caq_adj_seg_pca.index" && -f "baselines/saq/data/$ds/${ds}_query_pca.fvecs" ]]; then
    log "$ds SAQ data already exists; skip"
    return 0
  fi
  mkdir -p "baselines/saq/data/$ds"
  ln -sf "$REPO/data/$ds/${ds}_base.fvecs" "baselines/saq/data/$ds/${ds}_base.fvecs"
  ln -sf "$REPO/data/$ds/${ds}_query.fvecs" "baselines/saq/data/$ds/${ds}_query.fvecs"
  ln -sf "$REPO/data/$ds/${ds}_groundtruth.ivecs" "baselines/saq/data/$ds/${ds}_groundtruth.ivecs"
  cd "$REPO/baselines/saq" || return 1
  PYTHONPATH=python python3 python/ivf.py "$ds" 4096 > "$REPO/logs/prereq_${ds}_saq_ivf.log" 2>&1
  local rc1=$?
  PYTHONPATH=python python3 python/pca.py "$ds" > "$REPO/logs/prereq_${ds}_saq_pca.log" 2>&1
  local rc2=$?
  ./bin/create_index -dataset "$ds" -K 4096 -B 4 -num_threads 32 > "$REPO/logs/prereq_${ds}_saq_index.log" 2>&1
  local rc3=$?
  cd "$REPO" || return 1
  if [[ $rc1 -ne 0 || $rc2 -ne 0 || $rc3 -ne 0 ]]; then
    log "$ds SAQ FAILED rc=$rc1,$rc2,$rc3"
    return 1
  fi
  log "$ds SAQ data done"
}

step_graphs() {
  local ds="$1"
  log "== $ds: 02 shared + Ours graphs =="
  local shared="$REPO/results/$ds/indexes/02_diskann_fair/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
  local ours="$REPO/results/$ds/indexes/02_diskann_fair/Ours/${ds}_Ours_R64_Lbuild400.graph.bin"
  if [[ ! -f "$shared" ]]; then
    "$BIN02" --dataset "$ds" --methods PQ --shared-graph --max-degree 64 --build-beam 400 \
      --query-coarse-codec int8 --out-root results --repeats 1 --threads 32 --refine-passes 1 \
      --build-prune-cap 256 --build-early-stop-hops 2 \
      --query-path "results/03_system_fair/$ds/csv/_query_splits/test_query.fvecs" \
      --gt-path "results/03_system_fair/$ds/csv/_query_splits/test_gt.ivecs" > "logs/prereq_${ds}_shared_graph.log" 2>&1
    local rc=$?
    if [[ $rc -ne 0 ]]; then log "$ds shared graph FAILED rc=$rc"; return $rc; fi
    log "$ds shared graph done"
  else
    log "$ds shared graph exists; skip"
  fi
  if [[ ! -f "$ours" ]]; then
    "$BIN02" --dataset "$ds" --methods Ours --max-degree 64 --build-beam 400 \
      --query-coarse-codec int8 --out-root results --repeats 1 --threads 32 --refine-passes 1 \
      --build-prune-cap 256 --build-early-stop-hops 2 \
      --query-path "results/03_system_fair/$ds/csv/_query_splits/test_query.fvecs" \
      --gt-path "results/03_system_fair/$ds/csv/_query_splits/test_gt.ivecs" > "logs/prereq_${ds}_ours_graph.log" 2>&1
    local rc=$?
    if [[ $rc -ne 0 ]]; then log "$ds Ours graph FAILED rc=$rc"; return $rc; fi
    log "$ds Ours graph done"
  else
    log "$ds Ours graph exists; skip"
  fi
}

step_oglvq() {
  local ds="$1" valq="$2"
  log "== $ds: 03 OG-LVQ index (LVQ4_R64_W400) =="
  local idx="$REPO/results/03_system_fair/$ds/indexes/OG-LVQ/LVQ4_R64_W400"
  if [[ -d "$idx" ]]; then
    log "$ds OG-LVQ index exists; skip"
    return 0
  fi
  mkdir -p "results/03_system_fair/$ds/csv/tuning"
  python3 - "$ds" <<PYCFG
import json, sys
from pathlib import Path
ds = sys.argv[1]
tuning = Path(f"results/03_system_fair/{ds}/csv/tuning")
tuning.mkdir(parents=True, exist_ok=True)
cfg = {
    "config_id": "LVQ4_R64_W400",
    "R": 64, "W": 400, "alpha": 1.2, "primary": 4, "residual": 0,
    "selection_note": "fixed R=64/W=400",
}
(tuning / "OG-LVQ_selected_config.json").write_text(json.dumps({"method": "OG-LVQ", "selected_config": cfg, "status": "ok", "recall_target": 0.95, "fixed": True}, indent=2) + "\n")
print("fixed OG-LVQ config written")
PYCFG
  python3 -u experiments/03_system_fair/run_system_fair.py \
    --dataset "$ds" --systems OG-LVQ --run --repeats 1 --threads 32 --val-queries "$valq" --out-root results > "logs/prereq_${ds}_oglvq.log" 2>&1
  local rc=$?
  if [[ $rc -ne 0 ]]; then log "$ds OG-LVQ FAILED rc=$rc"; return $rc; fi
  log "$ds OG-LVQ index done"
}

log "PREREQS START (agnews then dbpedia)"
step_candidates agnews; step_saq agnews; step_graphs agnews; step_oglvq agnews 200
step_candidates dbpedia; step_saq dbpedia; step_graphs dbpedia; step_oglvq dbpedia 1000
log "PREREQS ALL DONE"
