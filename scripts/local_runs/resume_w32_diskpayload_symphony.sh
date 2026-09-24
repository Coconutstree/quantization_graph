#!/usr/bin/env bash
# Minimal background resume for the 32-worker disk-environment repair:
# 1) rebuild the bad 02/agnews shared FP32 graph,
# 2) run only 05B disk_payload baselines and 05C SymphonyQG at workers=32,
# 3) merge those rows into the published disk_environment CSVs and redraw figures.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

RUN_ID="${RUN_ID:-fix_w32_diskpayload_symphony_$(date +%Y%m%d_%H%M%S)}"
DATASETS="${DATASETS:-agnews,gist}"
WORKERS="${WORKERS:-32}"
DISK_ROOT="${DISK_ROOT:-${ROOT}/work/05_disk_system_fair/disk_root}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/results/archive/legacy_layout_20260918/disk_environment/.formal_runs}"
PORTS="${PORTS:-${ROOT}/src/disk_bench/ports.local.json}"
PUBLISH_ROOT="${PUBLISH_ROOT:-${ROOT}/results/archive/legacy_layout_20260918/disk_environment}"
DISK_PROFILE="${DISK_PROFILE:-auto}"
SEED="${SEED:-20260813}"
SKIP_05B_EXPORT="${SKIP_05B_EXPORT:-0}"
SKIP_05B_VALIDATE="${SKIP_05B_VALIDATE:-0}"
SKIP_05C_VALIDATE="${SKIP_05C_VALIDATE:-0}"
W="${QG05_FAST_WIDTHS:-$(python3 -c 'vals=list(range(10,31)); vals.extend(range(40,101,10)); vals.extend(range(140,581,40)); print(",".join(str(v) for v in vals))')}"

export QG05_FAST=1
export QG05_FAST_WIDTHS="${W}"

log() { echo "[$(date '+%F %T')] $*"; }

graph_nodes() {
  python3 - "$1" <<'PY'
from pathlib import Path
import struct, sys

path = Path(sys.argv[1])
data = path.read_bytes()
if len(data) < 24:
    raise SystemExit("graph too small")
size = struct.unpack_from("<Q", data, 0)[0]
if size > len(data):
    raise SystemExit(f"graph header size {size} exceeds file bytes {len(data)}")
pos = 24
nodes = 0
while pos < size:
    if pos + 4 > size:
        raise SystemExit("truncated graph length")
    degree = struct.unpack_from("<I", data, pos)[0]
    pos += 4 + degree * 4
    if pos > size:
        raise SystemExit("truncated graph neighbors")
    nodes += 1
print(nodes)
PY
}

graph_start_point() {
  python3 - "$1" <<'PY'
from pathlib import Path
import struct, sys

data = Path(sys.argv[1]).read_bytes()
if len(data) < 16:
    raise SystemExit("graph too small")
print(struct.unpack_from("<I", data, 12)[0])
PY
}

graph_additional_points() {
  python3 - "$1" <<'PY'
from pathlib import Path
import struct, sys

data = Path(sys.argv[1]).read_bytes()
if len(data) < 24:
    raise SystemExit("graph too small")
print(struct.unpack_from("<Q", data, 16)[0])
PY
}

merge_rows() {
  python3 - "${RUN_ID}" "${OUT_ROOT}" "${PUBLISH_ROOT}" <<'PY'
import csv
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path

run_id, out_root, publish_root = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
layer_map = {
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

log "RUN_ID=${RUN_ID}"
mkdir -p "${OUT_ROOT}" "${DISK_ROOT}" "${PUBLISH_ROOT}"

AG_GRAPH="results/archive/legacy_layout_20260918/disk_environment/dataset_artifacts/agnews/indexes/02_diskann_fair/shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed${SEED}.graph.bin"
AG_META="${AG_GRAPH%.graph.bin}.graph.json"

rebuild_shared_graph() {
  log "rebuilding 02 agnews shared graph (removing old graph files first)"
  rm -f "${AG_GRAPH}" "${AG_META}"
  src/graph_core/target/release/run_diskann_fair \
    --dataset agnews --methods PQ --shared-graph --max-degree 64 --build-beam 400 \
    --query-coarse-codec int8 --out-root results --repeats 1 --threads 32 \
    --refine-passes 1 --build-prune-cap 256 --build-early-stop-hops 2 \
    --query-path results/archive/legacy_layout_20260918/disk_environment/03_system_fair/agnews/csv/_query_splits/test_query.fvecs \
    --gt-path results/archive/legacy_layout_20260918/disk_environment/03_system_fair/agnews/csv/_query_splits/test_gt.ivecs
}

# The native port contract requires the baseline shared graph to contain at
# least N + additional_points nodes (DiskANN appends the virtual start node and
# an empty sink node, so the canonical shape is N+2 with start_point=N).
# Trimming it to exactly N (base_count) is wrong and breaks 05B/05C export.
check_shared_graph() {
  nodes="$(graph_nodes "${AG_GRAPH}")"
  base_count="$(awk -F= '/^base_count=/{print $2}' "${AG_META}")"
  additional="$(graph_additional_points "${AG_GRAPH}")"
  start_point="$(graph_start_point "${AG_GRAPH}")"
  min_nodes=$((base_count + additional))
  max_nodes=$((base_count + additional + 1))
  log "agnews shared graph nodes=${nodes} base_count=${base_count} additional=${additional} start_point=${start_point} (accepted ${min_nodes}..${max_nodes})"
  if (( nodes < min_nodes || nodes > max_nodes )); then
    return 1
  fi
  if (( start_point >= nodes )); then
    log "fixing invalid start_point=${start_point} -> ${base_count}"
    python3 - "${AG_GRAPH}" "${base_count}" <<'PY'
import struct, sys
from pathlib import Path

graph = Path(sys.argv[1])
base_count = int(sys.argv[2])
data = bytearray(graph.read_bytes())
struct.pack_into("<I", data, 12, base_count)
graph.write_bytes(data)
print(f"fixed start_point={base_count}")
PY
  fi
  return 0
}

if [[ ! -s "${AG_GRAPH}" || ! -s "${AG_META}" ]]; then
  rebuild_shared_graph
else
  log "using existing agnews shared graph"
fi

if ! check_shared_graph; then
  log "ERROR: shared graph node count outside accepted range; rebuilding"
  rebuild_shared_graph
  if ! check_shared_graph; then
    log "ERROR: rebuilt shared graph still invalid"
    exit 2
  fi
fi

log "running 05B disk_payload baselines at workers=${WORKERS}"
if [[ "${SKIP_05B_EXPORT}" != "1" ]]; then
  python3 src/disk_bench/run_disk_suite.py \
    --phase export --run-id "${RUN_ID}" --layers 05b --datasets "${DATASETS}" \
    --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
    --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
    --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${WORKERS}" --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
else
  log "SKIP_05B_EXPORT=1: reusing existing 05B exports for ${DATASETS}"
fi
if [[ "${SKIP_05B_VALIDATE}" != "1" ]]; then
  python3 src/disk_bench/run_disk_suite.py \
    --phase validate --run-id "${RUN_ID}" --layers 05b --datasets "${DATASETS}" \
    --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
    --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
    --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
    --workers "${WORKERS}" --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0
else
  log "SKIP_05B_VALIDATE=1: skipping single-threaded 05B validation sweep"
fi
python3 src/disk_bench/run_disk_suite.py \
  --phase run --run-id "${RUN_ID}" --layers 05b --datasets "${DATASETS}" \
  --methods PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk \
  --storage-modes disk_payload --ports "${PORTS}" --disk-root "${DISK_ROOT}" \
  --disk-profile "${DISK_PROFILE}" --out-root "${OUT_ROOT}" \
  --workers "${WORKERS}" --repeats 1 --seed "${SEED}" --search-dram-budget-gib 2.0

log "running 05C SymphonyQG at workers=${WORKERS}"
python3 src/disk_bench/run_disk_suite.py \
  --phase export --run-id "${RUN_ID}" --layers 05c --datasets "${DATASETS}" \
  --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
  --out-root "${OUT_ROOT}" --workers "${WORKERS}" --repeats 1 \
  --seed "${SEED}" --search-dram-budget-gib 2.0
if [[ "${SKIP_05C_VALIDATE}" != "1" ]]; then
  python3 src/disk_bench/run_disk_suite.py \
    --phase validate --run-id "${RUN_ID}" --layers 05c --datasets "${DATASETS}" \
    --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
    --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
    --out-root "${OUT_ROOT}" --workers "${WORKERS}" --repeats 1 \
    --seed "${SEED}" --search-dram-budget-gib 2.0
else
  log "SKIP_05C_VALIDATE=1: skipping single-threaded 05C validation sweep"
fi
python3 src/disk_bench/run_disk_suite.py \
  --phase run --run-id "${RUN_ID}" --layers 05c --datasets "${DATASETS}" \
  --methods SymphonyQG-DiskPort --storage-modes hybrid_disk \
  --ports "${PORTS}" --disk-root "${DISK_ROOT}" --disk-profile "${DISK_PROFILE}" \
  --out-root "${OUT_ROOT}" --workers "${WORKERS}" --repeats 1 \
  --seed "${SEED}" --search-dram-budget-gib 2.0

log "merging repaired rows into published CSVs"
merge_rows

log "redrawing published figures"
python3 src/disk_bench/plot_05_disk_suite.py \
  --public-root "${PUBLISH_ROOT}" --layers 05b,05c --datasets "${DATASETS}" \
  --workers "${WORKERS}"

log "DONE RUN_ID=${RUN_ID}"
