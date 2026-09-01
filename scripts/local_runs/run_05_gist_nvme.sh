#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

export RUN_ID="${RUN_ID:-formal_diskenv_gist_32c_$(date +%Y%m%d_%H%M%S)}"
export PYTHON=python3
export DATASETS=gist

mkdir -p logs "$DISK_ROOT"

{
  date
  echo "RUN_ID=$RUN_ID"
  echo "DISK_ROOT=$DISK_ROOT"
  echo "DISK_PROFILE=$DISK_PROFILE"

  mkdir -p build/cmake_repro
  cd build/cmake_repro
  cmake ../..
  make -j32
  cd ../..

  python3 scripts/check_datasets.py --datasets gist --data-root data --out-root results

  DATASETS=gist OUT_ROOT=results/memory_environment PYTHON=python3 bash scripts/run_master_round2.sh

  SHARED_GRAPH="results/memory_environment/dataset_artifacts/gist/indexes/02_diskann_fair/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
  if [[ ! -s "$SHARED_GRAPH" ]]; then
    experiments/02_diskann_fair/target/release/run_diskann_fair \
      --dataset gist --methods PQ --shared-graph --max-degree 64 --build-beam 400 \
      --query-coarse-codec int8 \
      --out-root results --repeats 1 --threads 32 --refine-passes 1 --build-prune-cap 256 --build-early-stop-hops 2 \
      --query-path results/memory_environment/03_system_fair/gist/csv/_query_splits/test_query.fvecs \
      --gt-path results/memory_environment/03_system_fair/gist/csv/_query_splits/test_gt.ivecs
  fi

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase doctor --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile "$DISK_PROFILE"

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile "$DISK_PROFILE" --out-root "$OUT_ROOT"

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile "$DISK_PROFILE" --out-root "$OUT_ROOT"

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase tune --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile "$DISK_PROFILE" --out-root "$OUT_ROOT"

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile "$DISK_PROFILE" --out-root "$OUT_ROOT" \
    --workers 1,16 --repeats 5

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "$RUN_ID" --layers all --datasets gist --out-root "$OUT_ROOT"

  date
  echo "DONE RUN_ID=$RUN_ID"
} 2>&1 | tee "logs/05_gist_${RUN_ID}.log"
