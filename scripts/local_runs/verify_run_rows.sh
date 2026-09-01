#!/usr/bin/env bash
# Lightweight post-phase verification for 05 run outputs:
#   - aggregate row count == expected (methods x widths x repeats)
#   - every row carries the required statistics columns (non-empty, not NaN)
#   - rows carry the expected worker count
#   - 05b test artifacts include the DRAM-vs-disk parity files
# usage: verify_run_rows.sh <run_id> <layer> <datasets_csv> <expected_rows_per_dataset> <workers> <methods_csv>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_ID="$1"
LAYER="$2"
DATASETS="$3"
EXPECTED="$4"
WORKERS="$5"
METHODS_CSV="$6"

case "${LAYER}" in
  05a) LAYER_DIR="05A_disk_quantizer_io" ;;
  05b) LAYER_DIR="05B_diskann_shared_graph" ;;
  05c) LAYER_DIR="05C_disk_system_fair" ;;
  *) echo "ERROR: unknown layer ${LAYER}"; exit 2 ;;
esac

OUT_ROOT="${OUT_ROOT:-${ROOT}/results/disk_environment/.formal_runs}"
FAIL=0
for ds in ${DATASETS//,/ }; do
  CSV="${OUT_ROOT}/runs/${RUN_ID}/${LAYER_DIR}/${ds}/aggregate/formal_test_rows.csv"
  if python3 - "${CSV}" "${EXPECTED}" "${WORKERS}" "${LAYER}" "${METHODS_CSV}" "${ds}" <<'PY'
import csv
import pathlib
import sys

path, expected, workers, layer, methods_csv, dataset = (
    sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6],
)
try:
    with open(path) as f:
        rows = list(csv.DictReader(f))
except FileNotFoundError:
    print(f"FAIL {path}: aggregate CSV missing")
    sys.exit(1)

required = [
    "recall", "qps", "latency_mean_us",
    "io_requests_per_query", "bytes_read_per_query", "distance_compute_us",
]
expected_methods = {m.strip() for m in methods_csv.split(",") if m.strip()}
actual_methods = {r.get("method", "") for r in rows}
repeats = 1
errs = []
if len(rows) != expected:
    errs.append(f"row count {len(rows)} != expected {expected}")
if actual_methods != expected_methods:
    errs.append(f"methods {sorted(actual_methods)} != expected {sorted(expected_methods)}")
bad_w = [r.get("search_width") for r in rows if str(r.get("workers", "")).strip() != str(workers)]
if bad_w:
    errs.append(f"{len(bad_w)} rows carry workers != {workers}")
for r in rows:
    if str(r.get("phase", "")).strip() != "test":
        errs.append(f"non-test row leaked: phase={r.get('phase')}")
        break
    if str(r.get("dataset", "")).strip() != dataset:
        errs.append(f"mixed dataset row: {r.get('dataset')}")
        break
    if float(r.get("search_dram_budget_gib") or 0) != 2.0:
        errs.append(f"row with budget != 2.0: {r.get('search_dram_budget_gib')}")
        break
    want_cache = "c0" if layer == "05a" else "standard"
    if r.get("cache_mode", "").strip() != want_cache:
        errs.append(f"row with cache_mode={r.get('cache_mode')}, expected {want_cache}")
        break
if layer == "05a":
    modes = {r.get("storage_mode", "") for r in rows}
    if not modes <= {"resident", "payload_on_ssd"}:
        errs.append(f"05a unexpected storage modes: {sorted(modes)}")
if layer == "05b":
    hashes = {
        str(r.get("shared_graph_sha256", ""))
        for r in rows
        if r.get("method", "") != "Ours-Disk" and r.get("shared_graph_sha256", "").strip()
    }
    if len(hashes) != 1:
        errs.append(f"05b shared graph hash must be single non-empty, got {sorted(hashes)}")
for i, r in enumerate(rows):
    for k in required:
        v = r.get(k, "").strip()
        if v == "" or v.lower() in ("nan", "none", "null", "-"):
            errs.append(f"row {i} (width={r.get('search_width')}) missing/NaN {k}")
            break

n_methods = len(expected_methods)
widths = expected // (n_methods * repeats) if n_methods else 0
actual_widths = {str(r.get("search_width", "")).strip() for r in rows}
if widths and len(actual_widths) != widths:
    errs.append(f"width coverage {len(actual_widths)} != expected {widths}")

if layer == "05b":
    adir = pathlib.Path(path).parent.parent / "artifacts" / "test"
    for m in ("PQ-DiskANN-Disk", "SQ-DiskANN-Disk", "SAQ-DiskANN-Disk"):
        p = adir / f"{m}__disk_payload__B2__standard__w{workers}__r0.parity.json"
        if not p.exists():
            errs.append(f"missing parity {p.name}")

if layer in ("05b", "05c"):
    # The instrumented ports must report build-time distance computations in
    # every export artifact (PQ > 0; SQ/SAQ legitimately 0; SymphonyQG > 0).
    edir = pathlib.Path(path).parent.parent / "artifacts" / "export"
    export_jsons = sorted(
        p for p in edir.glob("*.json") if not p.name.endswith(".build_stats.json")
    )
    if not export_jsons:
        errs.append("missing export artifact jsons")
    for ej in export_jsons:
        try:
            import json as _json

            with ej.open() as f:
                data = _json.load(f)
            if "build_distance_computations" not in data:
                errs.append(f"export {ej.name} missing build_distance_computations")
            elif not isinstance(data["build_distance_computations"], int):
                errs.append(f"export {ej.name} build_distance_computations not an int")
        except Exception as exc:  # noqa: BLE001
            errs.append(f"export {ej.name} unreadable: {exc}")

if errs:
    print(f"FAIL {path}")
    for e in errs[:20]:
        print(f"  - {e}")
    sys.exit(1)
print(f"PASS {path}: {len(rows)} rows, workers={workers}, required stats present")
PY
  then
    :
  else
    FAIL=1
  fi
done
exit ${FAIL}
