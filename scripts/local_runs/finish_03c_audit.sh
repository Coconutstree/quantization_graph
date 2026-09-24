#!/usr/bin/env bash
set -euo pipefail
cd /home/kai3/coco/quantization_graph
while tmux has-session -t system_03_graph_queue 2>/dev/null; do
  sleep 30
done
python3 scripts/local_runs/audit_03c_all.py
python3 scripts/local_runs/audit_03c_readers.py
