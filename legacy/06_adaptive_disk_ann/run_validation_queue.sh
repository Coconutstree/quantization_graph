#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_ID="${1:-agnews_queue_$(date +%Y%m%d_%H%M%S)}"
OUT="$ROOT/results/disk_environment/06_adaptive_disk_ann/queue/$RUN_ID"
mkdir -p "$OUT"
LOG="$OUT/queue.log"
exec > >(tee -a "$LOG") 2>&1

set -o pipefail
echo "run_id=$RUN_ID"
echo "started=$(date -Is)"

echo "[1/3] Python correctness tests"
python3 -m unittest discover -s "$ROOT/legacy/06_adaptive_disk_ann" -p 'test_*.py'

echo "[2/3] C++ route kernel and sidecar parity"
g++ -std=c++17 -O2 \
  "$ROOT/legacy/06_adaptive_disk_ann/asymmetric_route_kernel.cpp" \
  "$ROOT/legacy/06_adaptive_disk_ann/asymmetric_route_kernel_test.cpp" \
  -o "$OUT/asymmetric_route_kernel_test"
"$OUT/asymmetric_route_kernel_test"
g++ -std=c++17 -O2 \
  "$ROOT/legacy/06_adaptive_disk_ann/route_sidecar.cpp" \
  "$ROOT/legacy/06_adaptive_disk_ann/route_sidecar_test.cpp" \
  -o "$OUT/route_sidecar_test"
"$OUT/route_sidecar_test"

echo "[3/3] Production graph-search gate"
if ! rg -q "loadAdaptiveRouteArtifacts" \
  "$ROOT/Ours/core/hnswlib"; then
  echo "BLOCKED: adaptive sidecar is not wired into Ours C++ graph traversal."
  echo "No Recall/QPS result is emitted; running now would measure legacy Ours."
  echo "ended=$(date -Is)"
  exit 42
fi

echo "READY: production graph-search gate passed"
echo "ended=$(date -Is)"
