#!/usr/bin/env bash
# Build and run the query coarse-filter codec unit tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-/tmp/qcc_test}"

g++ -std=c++17 -O2 -march=native -fopenmp -fPIC \
  -I "${ROOT}" -DHNSWLIB_RABITQ_TESTING \
  "${ROOT}/Ours/tests/query_coarse_codec_test.cpp" -o "${OUT}"
"${OUT}"
