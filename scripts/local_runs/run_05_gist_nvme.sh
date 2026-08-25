#!/usr/bin/env bash
set -euo pipefail

cd /home/msy2025/quantization_graph

export PATH=/home/msy2025/.local/bin:/home/msy2025/.cargo/bin:$PATH
export QG_LOCAL=/home/msy2025/quantization_graph/baselines/deps/local
export QG_LOCAL_LIB="$QG_LOCAL/usr/lib/x86_64-linux-gnu"
export LD_LIBRARY_PATH="$QG_LOCAL_LIB:$QG_LOCAL_LIB/openblas-pthread:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="$QG_LOCAL_LIB:$QG_LOCAL_LIB/openblas-pthread:${LIBRARY_PATH:-}"
export CPLUS_INCLUDE_PATH="$QG_LOCAL/usr/include:${CPLUS_INCLUDE_PATH:-}"

export RUN_ID="${RUN_ID:-formal_nvme_gist_32c_$(date +%Y%m%d_%H%M%S)}"
export PORTS=experiments/05_disk_system_fair/ports.local.json
export DISK_ROOT="${DISK_ROOT:-/home/msy2025/qgraph_nvme}"
export PYTHON=python3
export OUT_ROOT=results
export DATASETS=gist

mkdir -p logs "$DISK_ROOT"

{
  date
  echo "RUN_ID=$RUN_ID"
  echo "DISK_ROOT=$DISK_ROOT"

  mkdir -p build/cmake_repro
  cd build/cmake_repro
  cmake ../..
  make -j32
  cd ../..

  python3 scripts/check_datasets.py --datasets gist --data-root data --out-root results

  DATASETS=gist OUT_ROOT=results PYTHON=python3 bash scripts/run_master_round2.sh

  SHARED_GRAPH="results/gist/indexes/02_diskann_fair/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
  if [[ ! -s "" ]]; then
    experiments/02_diskann_fair/target/release/run_diskann_fair \
      --dataset gist --methods PQ --shared-graph --max-degree 64 --build-beam 400 \
      --query-coarse-codec int8 \
      --out-root results --repeats 1 --threads 32 --refine-passes 1 --build-prune-cap 256 --build-early-stop-hops 2 \
      --query-path results/03_system_fair/gist/csv/_query_splits/test_query.fvecs \
      --gt-path results/03_system_fair/gist/csv/_query_splits/test_gt.ivecs
  fi

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase doctor --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase export --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase validate --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase tune --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "$RUN_ID" --layers all --datasets gist \
    --ports "$PORTS" --disk-root "$DISK_ROOT" --disk-profile nvme \
    --workers 1,16 --repeats 5

  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase plot --run-id "$RUN_ID" --layers all --datasets gist

  date
  echo "DONE RUN_ID=$RUN_ID"
} 2>&1 | tee "logs/05_gist_${RUN_ID}.log"
