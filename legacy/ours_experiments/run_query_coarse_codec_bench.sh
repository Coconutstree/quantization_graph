#!/usr/bin/env bash
# End-to-end comparison of query coarse-filter codecs on the Ours-DiskANN
# pipeline: full (baseline) / b1 (1-bit symmetric popcount) / int4 / int8
# (asymmetric quantized queries). The database operand of the first-stage
# coarse gate is fixed to the same 1-bit MSB sidecar for every codec. INT4/INT8
# keep the quantized query for the surviving candidates' 4-bit traversal
# distance; final residual rerank returns to the full query.
#
# The Vamana graph is identical for every codec (the codec only affects the
# query-time traversal), so a single invocation builds the graph once and
# sweeps all codecs with --query-coarse-codecs.
#
# Usage:
#   DATASETS="smoke03 agnews" CODECS="full b1 int4 int8" \
#     SEARCH_SIZES="10,20,40,80,160,320,460" \
#     OUT_ROOT=results/disk_environment/diagnostics/query_coarse_bench_full_fp32_batch \
#     bash legacy/ours_experiments/run_query_coarse_codec_bench.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

PY="${PYTHON:-python3}"
BIN="${BIN:-src/graph_core/target/release/run_diskann_fair}"
DATASETS="${DATASETS:-smoke03 agnews}"
CODECS="${CODECS:-full b1 int4 int8}"
SEARCH_SIZES="${SEARCH_SIZES:-10,20,40,80,160,320,460}"
OUT_ROOT="${OUT_ROOT:-results/disk_environment/diagnostics/query_coarse_bench_full_fp32_batch}"
SPLIT_ROOT="${SPLIT_ROOT:-results/disk_environment}"
THREADS="${THREADS:-64}"
B1_EPS="${B1_EPSILON:-1.9}"

NEEDS_BUILD=0
if [[ ! -x "${BIN}" ]]; then
  NEEDS_BUILD=1
else
  for source in \
    "Ours/core/hnswlib/space_rabitq.h" \
    "src/graph_core/native/rabitq_bridge.cpp" \
    "src/graph_core/src/ours_diskann.rs"; do
    if [[ "${source}" -nt "${BIN}" ]]; then
      NEEDS_BUILD=1
      break
    fi
  done
fi
if (( NEEDS_BUILD )); then
  if ! command -v cargo >/dev/null 2>&1; then
    echo "missing cargo: cannot rebuild stale query-coarse benchmark binary" >&2
    exit 1
  fi
  cargo build --release --manifest-path src/graph_core/Cargo.toml
  if [[ ! -x "${BIN}" ]]; then
    echo "rebuilt benchmark but binary is still missing: ${BIN}" >&2
    exit 1
  fi
fi

mkdir -p "${OUT_ROOT}"
SUMMARY="${OUT_ROOT}/query_coarse_codec_summary.csv"
if [[ ! -s "${SUMMARY}" ]]; then
  {
    echo "dataset,codec,search_list_size,recall,qps,latency_mean_us,paper_checked,paper_would_prune,paper_msb_kernel_calls,paper_remaining_kernel_calls,ffi_calls_per_query,traverse_us,paper_batch_us"
  } > "${SUMMARY}"
fi

for DS in ${DATASETS}; do
  case "${DS}" in
    smoke03) VAL_Q=100 ;;
    agnews)  VAL_Q=200 ;;
    gist|dbpedia) VAL_Q=1000 ;;
    *) VAL_Q=200 ;;
  esac
  "${PY}" - "${DS}" "${VAL_Q}" <<'EOF'
import sys
from pathlib import Path
from legacy.ours_experiments.run_ours import prepare_query_splits

ds, valq = sys.argv[1], int(sys.argv[2])
_, _, test_q, test_gt = prepare_query_splits(
    ds, Path("data"), Path("results/disk_environment"), valq)
print(f"{ds}: splits ready test_q={test_q} test_gt={test_gt}")
EOF
  TEST_Q="${SPLIT_ROOT}/03_system_fair/${DS}/csv/_query_splits/test_query.fvecs"
  TEST_GT="${SPLIT_ROOT}/03_system_fair/${DS}/csv/_query_splits/test_gt.ivecs"
  CODE_LIST="$(echo "${CODECS}" | tr ' ' ',')"
  GRAPH_ARGS=()
  if [[ -n "${GRAPH_FILE:-}" && -f "${GRAPH_FILE}" ]]; then
    GRAPH_ARGS=(--graph-file "${GRAPH_FILE}")
  else
    for candidate in \
      "results/disk_environment/diagnostics/query_coarse_bench"/"${DS}"/indexes/02_diskann_fair/Ours/"${DS}"_Ours_R64_Lbuild400.graph.bin \
      "results/disk_environment/diagnostics/query_coarse_bench_v2"/"${DS}"/indexes/02_diskann_fair/Ours/"${DS}"_Ours_R64_Lbuild400.graph.bin; do
      if [[ -f "${candidate}" ]]; then
        GRAPH_ARGS=(--graph-file "${candidate}")
        break
      fi
    done
  fi
  echo "== ${DS} codecs=${CODE_LIST} graph=${GRAPH_ARGS[*]:-build} =="
  "${BIN}" \
    --dataset "${DS}" --methods Ours \
    --max-degree 64 --build-beam 400 \
    --centroid-count 1 --centroid-train-samples 100000 \
    --refine-passes 2 --repeats 1 --threads "${THREADS}" \
    --search-list-sizes "${SEARCH_SIZES}" \
    --query-coarse-codecs "${CODE_LIST}" \
    --b1-epsilon "${B1_EPS}" \
    --out-root "${OUT_ROOT}" \
    "${GRAPH_ARGS[@]}" \
    --query-path "${TEST_Q}" --gt-path "${TEST_GT}"
  CSV_PATH="${OUT_ROOT}/02_diskann_fair/${DS}/csv/diskann_fair_raw.csv"
  if [[ ! -f "${CSV_PATH}" ]]; then
    echo "missing raw csv: ${CSV_PATH}" >&2
    exit 1
  fi
  "${PY}" - "${DS}" "${CSV_PATH}" "${SUMMARY}" <<'EOF'
import csv
import sys
from pathlib import Path

ds, csv_path, summary = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
rows = {}
if csv_path.exists():
    with csv_path.open() as f:
        for r in csv.DictReader(f):
            try:
                size = int(r.get("search_param_value", -1))
            except ValueError:
                size = -1
            codec = r.get("query_coarse_codec", "").strip('"')
            if size >= 0 and codec:
                rows[(codec, size)] = r

with open(summary, "a", newline="") as f:
    w = csv.writer(f)
    for (codec, size) in sorted(rows):
        m = rows[(codec, size)]
        w.writerow([
            ds,
            codec,
            size,
            m.get("recall", ""),
            m.get("qps", ""),
            m.get("latency_mean_us", ""),
            m.get("paper_checked", ""),
            m.get("paper_would_prune", ""),
            m.get("paper_msb_kernel_calls", ""),
            m.get("paper_remaining_kernel_calls", ""),
            m.get("ffi_calls_per_query", ""),
            m.get("traverse_us", ""),
            m.get("paper_batch_us", ""),
        ])
codecs = sorted({c for (c, _) in rows})
print(f"{ds}: {len(rows)} rows parsed codecs={','.join(codecs)}")
EOF
done

echo "summary written to ${SUMMARY}"
