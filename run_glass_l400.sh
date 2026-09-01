#!/usr/bin/env bash
set -euo pipefail
cd /home/kai3/coco/quantization_graph
export QG05_FAST=1
export QG05_CAPTURE_BUILD_STATS=1
export QG05_FAST_WIDTHS="10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460,500,540,580"
RID="glass_l400_w32_20260901_201237"
python3 experiments/05_disk_system_fair/run_disk_suite.py   --phase export --run-id "${RID}" --layers 05c --datasets agnews   --methods Glass-NSG-DiskPort --storage-modes hybrid_disk   --ports experiments/05_disk_system_fair/ports.local.json   --disk-root work/05_disk_system_fair/disk_root --disk-profile auto   --out-root results/disk_environment/.formal_runs   --workers 32 --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
python3 experiments/05_disk_system_fair/run_disk_suite.py   --phase run --run-id "${RID}" --layers 05c --datasets agnews   --methods Glass-NSG-DiskPort --storage-modes hybrid_disk   --ports experiments/05_disk_system_fair/ports.local.json   --disk-root work/05_disk_system_fair/disk_root --disk-profile auto   --out-root results/disk_environment/.formal_runs   --workers 32 --repeats 1 --seed 20260813 --search-dram-budget-gib 2.0
echo "GLASS_L400_DONE ${RID}"
