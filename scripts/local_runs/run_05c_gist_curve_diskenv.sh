#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

RUN_ID="${RUN_ID:-curve_gist_05c_w1_$(date +%Y%m%d_%H%M%S)}"
DISK_ROOT="${DISK_ROOT:-/home/msy2025/qgraph_nvme}"
OUT_ROOT="${OUT_ROOT:-results/disk_environment/.formal_runs}"
PORTS="${PORTS:-experiments/05_disk_system_fair/ports.local.json}"
WIDTHS="${QG05_FAST_WIDTHS:-10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99,100,140,180,220,260,300,340,380,420,460,500,540,580}"
VAL_QUERIES="${VAL_QUERIES:-900}"
METHODS="${METHODS:-Ours-Disk}"
WORKERS="${WORKERS:-1}"
LOG="logs/05c_gist_curve_diskenv_${RUN_ID}.log"

mkdir -p logs "$DISK_ROOT" "$OUT_ROOT" results/disk_environment/03_system_fair/gist/csv results/disk_environment/03_system_fair/gist/logs results/disk_environment/03_system_fair/gist/figures

{
  date
  echo "RUN_ID=$RUN_ID"
  echo "OUT_ROOT=$OUT_ROOT"
  echo "PUBLISH_ROOT=results/disk_environment"
  echo "DISK_ROOT=$DISK_ROOT"
  echo "METHODS=$METHODS"
  echo "WORKERS=$WORKERS"
  echo "QG05_FAST_WIDTHS=$WIDTHS"
  echo "VAL_QUERIES=$VAL_QUERIES"

  QG05_FAST=1 QG05_FAST_WIDTHS="$WIDTHS"     python3 experiments/05_disk_system_fair/run_disk_suite.py       --phase run       --layers 05c       --datasets gist       --methods "$METHODS"       --workers "$WORKERS"       --repeats 1       --val-queries "$VAL_QUERIES"       --disk-root "$DISK_ROOT"       --ports "$PORTS"       --out-root "$OUT_ROOT"       --run-id "$RUN_ID"

  QG05_FAST=1     python3 experiments/05_disk_system_fair/run_disk_suite.py       --phase plot       --layers 05c       --datasets gist       --methods "$METHODS"       --out-root "$OUT_ROOT"       --run-id "$RUN_ID"

  date
  echo "DONE_RUN: $OUT_ROOT/runs/$RUN_ID"
  echo "DONE_PUBLISH: results/disk_environment/03_system_fair/gist"
} 2>&1 | tee "$LOG"
