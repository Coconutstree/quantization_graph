#!/usr/bin/env bash
# After the current w32 agnews run finishes:
#  1) preserve the w32 agnews figure set,
#  2) sweep agnews 05B/05C at workers 16,8,4,2,1 (reusing the w32 exports),
#     saving an independent figure set per worker count (figures_w{W}),
#  3) run gist+dbpedia disk A/B/C at workers=32 with the same parameters as agnews.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${ROOT}/scripts/local_runs/diskenv_common.sh"
qg05_setup_diskenv

MAIN_RUN_ID="${MAIN_RUN_ID:-fix_w32_diskpayload_symphony_20260831_140957}"
MAIN_PID="${MAIN_PID:-2298083}"
MAIN_LOG="${MAIN_LOG:-${ROOT}/logs/fix_agnews_only_20260831_140957.log}"
SWEEP_WORKERS="${SWEEP_WORKERS:-16 8 4 2 1}"
GDB_DATASETS="${GDB_DATASETS:-gist,dbpedia}"
SEED="${SEED:-20260813}"
PUBLISH_ROOT="${PUBLISH_ROOT:-${ROOT}/results/disk_environment}"

export QG05_FAST=1
export QG05_CAPTURE_BUILD_STATS="${QG05_CAPTURE_BUILD_STATS:-1}"
W_BASE="$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')"
W_05A="${W_BASE},1000"
export QG05_FAST_WIDTHS="${W_BASE}"

log() { echo "[$(date '+%F %T')] $*"; }

merge_run_rows() {
  python3 - "$1" "${OUT_ROOT}" "${PUBLISH_ROOT}" <<'PY'
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

run_id, out_root, publish_root = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
layer_map = {
    "05A_disk_quantizer_io": "01_quantizer_fair",
    "05B_diskann_shared_graph": "02_diskann_fair",
    "05C_disk_system_fair": "03_system_fair",
}
key_fields = (
    "layer", "dataset", "method", "storage_mode", "phase", "repeat_id",
    "workers", "search_dram_budget_gib", "config_id", "search_param",
    "search_width", "beam_width", "cache_mode", "ablation",
)

def read_csv(path):
    if not path.exists():
        return [], []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)

def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

def merge_one(src, dst):
    src_fields, src_rows = read_csv(src)
    if not src_rows:
        return 0
    dst_fields, dst_rows = read_csv(dst)
    fields = dst_fields or src_fields
    for field in src_fields:
        if field not in fields:
            fields.append(field)
    indexed = {tuple(row.get(k, "") for k in key_fields): row for row in dst_rows}
    for row in src_rows:
        indexed[tuple(row.get(k, "") for k in key_fields)] = row
    merged = list(indexed.values())
    merged.sort(key=lambda r: (
        r.get("layer", ""), r.get("dataset", ""), r.get("method", ""),
        r.get("storage_mode", ""), int(float(r.get("workers") or 0)),
        float(r.get("recall") or 0), int(float(r.get("search_width") or 0)),
    ))
    write_csv(dst, fields, merged)
    return len(src_rows)

def pareto(rows):
    by_recall = {}
    for row in rows:
        recall = round(float(row["recall"]), 10)
        current = by_recall.get(recall)
        if current is None or float(row["qps"]) > float(current["qps"]):
            by_recall[recall] = row
    ordered = sorted(by_recall.values(), key=lambda r: float(r["recall"]), reverse=True)
    frontier = []
    best = -math.inf
    for row in ordered:
        qps = float(row["qps"])
        if qps > best:
            frontier.append(row)
            best = qps
    return list(reversed(frontier))

for run_layer, public_layer in layer_map.items():
    for src in (out_root / "runs" / run_id / run_layer).glob("*/aggregate/formal_test_rows.csv"):
        dataset = src.parents[1].name
        public_csv = publish_root / public_layer / dataset / "csv"
        copied = merge_one(src, public_csv / "formal_test_rows.csv")
        merge_one(src, public_csv / "formal_test_median.csv")
        fields, rows = read_csv(public_csv / "formal_test_rows.csv")
        groups = defaultdict(list)
        for row in rows:
            groups[(row.get("method", ""), row.get("storage_mode", ""), row.get("workers", ""), row.get("ablation", ""))].append(row)
        frontier = []
        for group in groups.values():
            frontier.extend(pareto(group))
        write_csv(public_csv / "formal_test_frontier.csv", fields, frontier)
        print(f"merged {copied} rows into {public_csv}")
PY
}

save_figures() {
  local W="$1"
  for layer_dir in 02_diskann_fair 03_system_fair; do
    local src="${PUBLISH_ROOT}/${layer_dir}/agnews/figures"
    local dst="${PUBLISH_ROOT}/${layer_dir}/agnews/figures_w${W}"
    if [[ -d "${src}" ]]; then
      mkdir -p "${dst}"
      cp -f "${src}"/* "${dst}/"
      log "saved agnews ${layer_dir} figures -> ${dst}"
    fi
  done
}

plot_agnews() {
  local W="$1"
  python3 experiments/05_disk_system_fair/plot_05_disk_suite.py \
    --public-root "${PUBLISH_ROOT}" --layers 05b,05c --datasets agnews --workers "${W}"
}

check_build_stats() {
  local layer_dir="$1" dataset="$2" min_count="$3"
  local n=0
  for f in "${OUT_ROOT}/runs/${MAIN_RUN_ID}/${layer_dir}/${dataset}/artifacts/export/"*.build_stats.json; do
    if [[ -f "${f}" ]]; then n=$((n + 1)); fi
  done
  log "build_stats files for ${layer_dir}/${dataset}: ${n} (expected >= ${min_count})"
  if [[ "${n}" -lt "${min_count}" ]]; then
    log "ERROR: missing build statistics for ${layer_dir}/${dataset}"
    exit 3
  fi
}

# ---- wait for the current w32 agnews run to finish ----
log "waiting for current w32 agnews run (pid=${MAIN_PID}) to finish..."
for _ in $(seq 1 720); do
  if grep -q "DONE RUN_ID=${MAIN_RUN_ID}" "${MAIN_LOG}" 2>/dev/null; then
    log "current w32 agnews run finished."
    break
  fi
  if ! kill -0 "${MAIN_PID}" 2>/dev/null; then
    log "ERROR: current w32 run died without DONE marker; aborting."
    tail -n 40 "${MAIN_LOG}"
    exit 2
  fi
  sleep 60
done

if ! grep -q "DONE RUN_ID=${MAIN_RUN_ID}" "${MAIN_LOG}" 2>/dev/null; then
  log "ERROR: timed out waiting for current w32 run; aborting."
  exit 2
fi

# ---- preserve the w32 agnews figure set before any sweep plot overwrites it ----
if [[ ! -d "${PUBLISH_ROOT}/02_diskann_fair/agnews/figures_w32" ]]; then
  save_figures 32
fi

# ---- gap fix: re-run agnews exports with build-stat capture so peak RSS /
#      read bytes / wall time are recorded (build_stats.json per method) ----
log "=== re-running agnews 05B exports with QG05_CAPTURE_BUILD_STATS=1 ==="
python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "${MAIN_RUN_ID}" --layers 05b --datasets agnews \
  --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
  --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
  --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
check_build_stats 05B_diskann_shared_graph agnews 3

log "=== re-running agnews 05C SymphonyQG export with QG05_CAPTURE_BUILD_STATS=1 ==="
python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "${MAIN_RUN_ID}" --layers 05c --datasets agnews \
  --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
  --out-root "${OUT_ROOT}" --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
check_build_stats 05C_disk_system_fair agnews 1

# ---- agnews worker sweep: 16, 8, 4, 2, 1 ----
for W in ${SWEEP_WORKERS}; do
  log "=== agnews workers=${W}: 05B test run (reusing exports) ==="
  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${MAIN_RUN_ID}" --layers 05b --datasets agnews \
    --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
    --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
    --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${W}" --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
  scripts/local_runs/verify_run_rows.sh "${MAIN_RUN_ID}" 05b agnews 120 "${W}" \
    "PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk"

  log "=== agnews workers=${W}: 05C SymphonyQG test run (reusing exports) ==="
  python3 experiments/05_disk_system_fair/run_disk_suite.py \
    --phase run --run-id "${MAIN_RUN_ID}" --layers 05c --datasets agnews \
    --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
    --out-root "${OUT_ROOT}" --workers "${W}" --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
  scripts/local_runs/verify_run_rows.sh "${MAIN_RUN_ID}" 05c agnews 40 "${W}" \
    "SymphonyQG-DiskPort"

  log "=== agnews workers=${W}: merge + plot + save figures ==="
  merge_run_rows "${MAIN_RUN_ID}"
  plot_agnews "${W}"
  save_figures "${W}"
done

# restore the canonical figures dir to the w32 view
plot_agnews 32

# ---- gist + dbpedia disk A/B/C at workers=32 ----
GDB_RUN_BASE="fix_gist_dbpedia_w32_$(date +%Y%m%d_%H%M%S)"
log "gist/dbpedia ABC run base: ${GDB_RUN_BASE}"

log "=== gist/dbpedia 05A export+run (workers=32) ==="
QG05_FAST_WIDTHS="${W_05A}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "${GDB_RUN_BASE}_05a" --layers 05a --datasets "${GDB_DATASETS}" \
  --methods PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1 \
  --storage-modes resident,payload_on_ssd --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
  --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
QG05_FAST_WIDTHS="${W_05A}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "${GDB_RUN_BASE}_05a" --layers 05a --datasets "${GDB_DATASETS}" \
  --methods PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1 \
  --storage-modes resident,payload_on_ssd --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
  --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
scripts/local_runs/verify_run_rows.sh "${GDB_RUN_BASE}_05a" 05a "${GDB_DATASETS}" 164 32 \
  "PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1"

log "=== gist/dbpedia 05B export+run (workers=32) ==="
QG05_FAST_WIDTHS="${W_BASE}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "${GDB_RUN_BASE}_05b" --layers 05b --datasets "${GDB_DATASETS}" \
  --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
  --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
  --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
QG05_FAST_WIDTHS="${W_BASE}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "${GDB_RUN_BASE}_05b" --layers 05b --datasets "${GDB_DATASETS}" \
  --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
  --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
  --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
scripts/local_runs/verify_run_rows.sh "${GDB_RUN_BASE}_05b" 05b "${GDB_DATASETS}" 120 32 \
  "PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk"

log "=== gist/dbpedia 05C SymphonyQG export+run (workers=32) ==="
QG05_FAST_WIDTHS="${W_BASE}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase export --run-id "${GDB_RUN_BASE}_05c" --layers 05c --datasets "${GDB_DATASETS}" \
  --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
  --out-root "${OUT_ROOT}" --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
QG05_FAST_WIDTHS="${W_BASE}" python3 experiments/05_disk_system_fair/run_disk_suite.py \
  --phase run --run-id "${GDB_RUN_BASE}_05c" --layers 05c --datasets "${GDB_DATASETS}" \
  --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
  --out-root "${OUT_ROOT}" --workers 32 --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
scripts/local_runs/verify_run_rows.sh "${GDB_RUN_BASE}_05c" 05c "${GDB_DATASETS}" 40 32 \
  "SymphonyQG-DiskPort"

log "=== gist/dbpedia merge + plot (workers=32) ==="
merge_run_rows "${GDB_RUN_BASE}_05a"
merge_run_rows "${GDB_RUN_BASE}_05b"
merge_run_rows "${GDB_RUN_BASE}_05c"
python3 experiments/05_disk_system_fair/plot_05_disk_suite.py \
  --public-root "${PUBLISH_ROOT}" --layers 05a,05b,05c --datasets "${GDB_DATASETS}" --workers 32

log "ALL DONE"
