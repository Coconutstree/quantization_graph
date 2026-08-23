#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="${BUILD_ROOT:-${ROOT}/build/formal_local}"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
GENERATOR="${CMAKE_GENERATOR:-Ninja}"
JOBS="${JOBS:-$(nproc)}"
FAISS_BUILD="${ROOT}/baselines/builds/faiss-cmake43"
SAQ_BUILD="${ROOT}/baselines/builds/saq-gcc11"
LOCAL_PREFIX="${ROOT}/baselines/deps/local"
FORMAL_CXX_BIN="${FORMAL_CXX_BIN:-/usr/bin/g++-11}"

build_cmake() {
  local name="$1"
  local source="$2"
  shift 2
  "${CMAKE_BIN}" -S "${source}" -B "${BUILD_ROOT}/${name}" \
    -G "${GENERATOR}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_COMPILER="${FORMAL_CXX_BIN}" "$@"
  "${CMAKE_BIN}" --build "${BUILD_ROOT}/${name}" -j "${JOBS}"
}

"${CMAKE_BIN}" -S "${ROOT}/baselines/faiss/upstream" -B "${FAISS_BUILD}" \
  -G "${GENERATOR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="${FAISS_CXX_BIN:-${FORMAL_CXX_BIN}}" \
  -DFAISS_ENABLE_GPU=OFF \
  -DFAISS_ENABLE_PYTHON=OFF \
  -DFAISS_ENABLE_C_API=OFF \
  -DBUILD_TESTING=OFF \
  -DFAISS_OPT_LEVEL=avx2
"${CMAKE_BIN}" --build "${FAISS_BUILD}" --target faiss -j "${JOBS}"

build_cmake 01_quantizer_fair "${ROOT}/experiments/01_quantizer_fair"
build_cmake 03_system_fair "${ROOT}/experiments/03_system_fair"
build_cmake 05_disk_system_fair "${ROOT}/experiments/05_disk_system_fair/native"

"${CMAKE_BIN}" -S "${ROOT}/baselines/saq" -B "${SAQ_BUILD}" \
  -G "${GENERATOR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="${SAQ_CXX_BIN:-${FORMAL_CXX_BIN}}" \
  -DBUILD_UNIT_TESTS=OFF \
  -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-${LOCAL_PREFIX};/usr}"
"${CMAKE_BIN}" --build "${SAQ_BUILD}" \
  --target test_fixed_candidates -j "${JOBS}"

if command -v cargo >/dev/null 2>&1; then
  CXX="${FORMAL_CXX_BIN}" cargo build --release --locked --offline \
    --manifest-path "${ROOT}/experiments/02_diskann_fair/Cargo.toml" \
    --bin qgraph05_shared_graph_port
  cargo build --release --locked --offline \
    --manifest-path "${ROOT}/baselines/diskann/Cargo.toml" \
    -p diskann-disk --bin qgraph05_diskann_port
elif command -v conda >/dev/null 2>&1 && \
     conda run -n rust-build cargo --version >/dev/null 2>&1; then
  conda run -n rust-build env CXX="${FORMAL_CXX_BIN}" \
    cargo build --release --locked --offline \
    --manifest-path "${ROOT}/experiments/02_diskann_fair/Cargo.toml" \
    --bin qgraph05_shared_graph_port
  conda run -n rust-build cargo build --release --locked --offline \
    --manifest-path "${ROOT}/baselines/diskann/Cargo.toml" \
    -p diskann-disk --bin qgraph05_diskann_port
else
  echo "ERROR: cargo is unavailable; activate the rust-build environment" >&2
  exit 2
fi

"${BUILD_ROOT}/05_disk_system_fair/qgraph05_direct_io_test"
echo "repository-local formal builds completed under ${BUILD_ROOT}"
