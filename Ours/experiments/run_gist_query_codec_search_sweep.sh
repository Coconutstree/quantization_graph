#!/usr/bin/env bash
# Reuse the formal GIST1M R64/Lbuild400 graph and compare query codecs over a
# fixed ef sweep. Payload construction is restricted to the physical cores of
# NUMA node 0; after payload encoding, the same process is pinned to CPU 20 for
# all timed single-thread searches. This prevents codec order from being
# confounded by remote first-touch placement while keeping encoding practical.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

BIN="${BIN:-experiments/02_diskann_fair/target/release/run_diskann_fair}"
OUT_ROOT="${OUT_ROOT:-results/query_coarse_bench_gist_1bit_search_numa}"
CODECS="${CODECS:-full,b1,int4,int8}"
SEARCH_SIZES="${SEARCH_SIZES:-20,40,60,80,100,200,300,400,500}"
GRAPH_FILE="${GRAPH_FILE:-results/02_diskann_fair/gist/indexes/Ours/gist_Ours_R64_Lbuild400.graph.bin}"
QUERY_FILE="${QUERY_FILE:-results/03_system_fair/gist/csv/_query_splits/test_query.fvecs}"
GT_FILE="${GT_FILE:-results/03_system_fair/gist/csv/_query_splits/test_gt.ivecs}"
SEARCH_CPU="${SEARCH_CPU:-20}"
# Xeon Gold 6248 host: the first hardware thread of each node-0 physical core.
ENCODE_CPUS="${ENCODE_CPUS:-0,4,8,12,16,20,24,28,32,36,40,44,48,52,56,60,64,68,72,76}"
BUILD_THREADS="${BUILD_THREADS:-20}"

for required in "${BIN}" "${GRAPH_FILE}" "${QUERY_FILE}" "${GT_FILE}"; do
  if [[ ! -e "${required}" ]]; then
    echo "missing required input: ${required}" >&2
    exit 1
  fi
done

LOG="${OUT_ROOT}/02_diskann_fair/gist/logs/Ours/gist_Ours_R64_Lbuild400.log"

taskset -c "${ENCODE_CPUS}" "${BIN}" \
  --dataset gist --methods Ours \
  --max-degree 64 --build-beam 400 \
  --centroid-count 1 --centroid-train-samples 100000 \
  --refine-passes 2 --repeats 1 --threads 1 \
  --build-threads "${BUILD_THREADS}" \
  --search-list-sizes "${SEARCH_SIZES}" \
  --query-coarse-codecs "${CODECS}" \
  --b1-epsilon 1.9 --rerank-candidates 100 \
  --out-root "${OUT_ROOT}" \
  --graph-file "${GRAPH_FILE}" \
  --query-path "${QUERY_FILE}" --gt-path "${GT_FILE}" &
pid=$!

cleanup() {
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

while kill -0 "${pid}" 2>/dev/null; do
  if [[ -f "${LOG}" ]] && rg -q 'progress_stage=payload_encode status=done' "${LOG}"; then
    taskset -pc "${SEARCH_CPU}" "${pid}" >&2
    break
  fi
  sleep 1
done

set +e
wait "${pid}"
status=$?
set -e
trap - INT TERM EXIT
exit "${status}"

