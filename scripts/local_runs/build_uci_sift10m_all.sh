#!/usr/bin/env bash
# Resume-safe UCI SIFT10M preparation: vectors, exact GT, split, candidates,
# SAQ preprocessing, and the canonical shared/Ours graph indexes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

ARCHIVE="$ROOT/downloads/uci_sift10m/sift10m.zip"
LOG_ROOT="$ROOT/logs/uci_sift10m"
WORK_ROOT="$ROOT/work/uci_sift10m"
DATASET_ROOT="$ROOT/data/sift10m"
GT_BIN="$WORK_ROOT/exact_l2_top1000.bin"
GT_TOOL="$ROOT/baselines/diskann/target/release/compute_groundtruth"
THREADS="${THREADS:-80}"
SAQ_PYTHON="${SAQ_PYTHON:-$HOME/miniconda3/envs/exrabitq/bin/python}"
SAQ_BUILD_DIR="${SAQ_BUILD_DIR:-$ROOT/baselines/saq/build_gcc11_uci}"
mkdir -p "$LOG_ROOT" "$WORK_ROOT" "$DATASET_ROOT"

log() { printf '[uci-sift10m] %s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "$LOG_ROOT/pipeline.status"; }

log "waiting for official archive download"
while tmux has-session -t uci_sift10m_download 2>/dev/null; do
  sleep 60
done
[[ -s "$ARCHIVE" ]] || { log "ERROR: missing archive $ARCHIVE"; exit 1; }
unzip -tq "$ARCHIVE" >>"$LOG_ROOT/archive-test.log" 2>&1
log "archive verified"

python3 -u scripts/prepare_uci_sift10m.py prepare \
  --archive "$ARCHIVE" --out-dir "$DATASET_ROOT" \
  >"$LOG_ROOT/prepare-vectors.log" 2>&1
log "official vectors converted"

if [[ ! -x "$GT_TOOL" ]]; then
  log "building DiskANN exact ground-truth tool"
  cargo build --release --manifest-path baselines/diskann/Cargo.toml \
    -p diskann-tools --bin compute_groundtruth \
    >"$LOG_ROOT/build-groundtruth-tool.log" 2>&1
fi

if [[ ! -s "$GT_BIN" ]]; then
  log "computing exact squared-L2 top-1000 ground truth with RAYON_NUM_THREADS=$THREADS"
  RAYON_NUM_THREADS="$THREADS" "$GT_TOOL" \
    --data-type uint8 --dist-fn l2 \
    --base-file "$DATASET_ROOT/sift10m_base.u8bin" \
    --query-file "$DATASET_ROOT/sift10m_query.u8bin" \
    --gt-file "$GT_BIN" --recall-at 1000 \
    >"$LOG_ROOT/exact-groundtruth.log" 2>&1
fi
python3 -u scripts/prepare_uci_sift10m.py convert-gt \
  --out-dir "$DATASET_ROOT" --diskann-gt "$GT_BIN" \
  >"$LOG_ROOT/convert-groundtruth.log" 2>&1
log "exact ground truth converted and validated"

python3 - <<'PY' >"$LOG_ROOT/query-split.log" 2>&1
from pathlib import Path
from importlib import import_module
import sys

sys.path.insert(0, str(Path.cwd()))
prepare_query_splits = import_module(
    "experiments.05_disk_system_fair.common"
).prepare_query_splits

paths = prepare_query_splits(
    "sift10m", Path("data"), Path("results/disk_environment"), 200
)
print(paths)
PY
log "query split ready (validation=200, test=9800)"

if [[ ! -s work/01_quantizer_fair/sift10m/fixed_candidates_k1000.bin ]]; then
  log "writing fixed top-1000 candidates directly from exact top-1000 GT"
  python3 -u scripts/prepare_uci_sift10m.py fixed-candidates \
    --out-dir "$DATASET_ROOT" --candidate-root work \
    >"$LOG_ROOT/fixed-candidates.log" 2>&1
fi
log "fixed candidates ready"

SAQ_DIR="$ROOT/baselines/saq/data/sift10m"
mkdir -p "$SAQ_DIR"
ln -sfn "$DATASET_ROOT/sift10m_base.fvecs" "$SAQ_DIR/sift10m_base.fvecs"
ln -sfn "$DATASET_ROOT/sift10m_query.fvecs" "$SAQ_DIR/sift10m_query.fvecs"
ln -sfn "$DATASET_ROOT/sift10m_groundtruth.ivecs" "$SAQ_DIR/sift10m_groundtruth.ivecs"
if [[ ! -s "$SAQ_DIR/ivf4096_b4_caq_adj_seg_pca.index" ]]; then
  log "building SAQ preprocessing and index"
  [[ -x "$SAQ_PYTHON" ]] || { log "ERROR: missing SAQ Python $SAQ_PYTHON"; exit 1; }
  "$SAQ_PYTHON" -c 'import faiss' || { log "ERROR: Faiss unavailable in $SAQ_PYTHON"; exit 1; }
  if [[ ! -x baselines/saq/bin/create_index ]]; then
    cmake -S baselines/saq -B "$SAQ_BUILD_DIR" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_COMPILER="${SAQ_CXX:-/usr/bin/g++-11}" \
      -DBUILD_UNIT_TESTS=OFF \
      -DCMAKE_PREFIX_PATH="$ROOT/baselines/deps/local;/usr" \
      -DCMAKE_MODULE_PATH="$ROOT/baselines/deps/local/usr/share/glog/cmake" \
      -DUnwind_INCLUDE_DIR="$ROOT/baselines/deps/local/usr/include" \
      -DUnwind_LIBRARY="$ROOT/baselines/deps/local/usr/lib/x86_64-linux-gnu/libunwind.so" \
      >"$LOG_ROOT/saq-build.log" 2>&1
    cmake --build "$SAQ_BUILD_DIR" --target create_index -j 16 \
      >>"$LOG_ROOT/saq-build.log" 2>&1
  fi
  (
    cd baselines/saq
    PYTHONPATH=python "$SAQ_PYTHON" python/ivf.py sift10m 4096
    PYTHONPATH=python "$SAQ_PYTHON" python/pca.py sift10m
    ./bin/create_index -dataset sift10m -K 4096 -B 4 -num_threads "$THREADS"
  ) >"$LOG_ROOT/saq.log" 2>&1
fi
log "SAQ artifacts ready"

log "building canonical shared and Ours graphs"
DATASETS=sift10m THREADS="$THREADS" \
  bash scripts/local_runs/build_05_core_graphs.sh \
  >"$LOG_ROOT/graphs.log" 2>&1
log "canonical graphs ready"

python3 -u scripts/prepare_uci_sift10m.py manifest \
  --archive "$ARCHIVE" --out-dir "$DATASET_ROOT" \
  >"$LOG_ROOT/manifest.log" 2>&1
touch "$WORK_ROOT/COMPLETE"
log "ALL COMPLETE"
