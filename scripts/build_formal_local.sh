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
OPENBLAS_LIB="${OPENBLAS_LIB:-${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/openblas-pthread/libopenblas.so.0}"
FORMAL_CXX_BIN="${FORMAL_CXX_BIN:-/usr/bin/g++}"

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
  -DFAISS_OPT_LEVEL=avx2 \
  -DBLAS_LIBRARIES="${OPENBLAS_LIB}" \
  -DLAPACK_LIBRARIES="${OPENBLAS_LIB}"
"${CMAKE_BIN}" --build "${FAISS_BUILD}" --target faiss -j "${JOBS}"

build_cmake 01_quantizer_fair "${ROOT}/experiments/01_quantizer_fair" -DBLAS_LIBRARIES="${OPENBLAS_LIB}" -DLAPACK_LIBRARIES="${OPENBLAS_LIB}"
build_cmake 03_system_fair "${ROOT}/experiments/03_system_fair" -DBLAS_LIBRARIES="${OPENBLAS_LIB}" -DLAPACK_LIBRARIES="${OPENBLAS_LIB}"
build_cmake 05_disk_system_fair "${ROOT}/experiments/05_disk_system_fair/native" -DBLAS_LIBRARIES="${OPENBLAS_LIB}" -DLAPACK_LIBRARIES="${OPENBLAS_LIB}" -DCMAKE_LIBRARY_PATH="${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu" -DCMAKE_INCLUDE_PATH="${LOCAL_PREFIX}/usr/include" -DOPENSSL_ROOT_DIR="${LOCAL_PREFIX}/usr" -DOPENSSL_CRYPTO_LIBRARY="${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/libcrypto.so" -DOPENSSL_INCLUDE_DIR="${LOCAL_PREFIX}/usr/include" -DCMAKE_PREFIX_PATH="${LOCAL_PREFIX};/usr" -DCMAKE_MODULE_PATH="${LOCAL_PREFIX}/usr/share/glog/cmake" -DUnwind_INCLUDE_DIR="${LOCAL_PREFIX}/usr/include" -DUnwind_LIBRARY="${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/libunwind.so"

if [[ -f "${SAQ_BUILD}/CMakeCache.txt" ]] && \
   ! grep -Fq "CMAKE_CXX_COMPILER:FILEPATH=${SAQ_CXX_BIN:-${FORMAL_CXX_BIN}}" "${SAQ_BUILD}/CMakeCache.txt"; then
  rm -f "${SAQ_BUILD}/CMakeCache.txt"
  rm -rf "${SAQ_BUILD}/CMakeFiles"
fi
"${CMAKE_BIN}" -S "${ROOT}/baselines/saq" -B "${SAQ_BUILD}" \
  -G "${GENERATOR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="${SAQ_CXX_BIN:-${FORMAL_CXX_BIN}}" \
  -DBUILD_UNIT_TESTS=OFF \
  -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-${LOCAL_PREFIX};/usr}" \
  -DCMAKE_MODULE_PATH="${SAQ_CMAKE_MODULE_PATH:-${LOCAL_PREFIX}/usr/share/glog/cmake}" \
  -DUnwind_INCLUDE_DIR="${SAQ_UNWIND_INCLUDE_DIR:-${LOCAL_PREFIX}/usr/include}" \
  -DUnwind_LIBRARY="${SAQ_UNWIND_LIBRARY:-${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/libunwind.so}"
"${CMAKE_BIN}" --build "${SAQ_BUILD}" \
  --target create_index test_qps test_relative_error -j "${JOBS}"

CARGO_OFFLINE_FLAG=""
if [[ "${CARGO_OFFLINE:-0}" == "1" ]]; then
  CARGO_OFFLINE_FLAG="--offline"
fi

if command -v cargo >/dev/null 2>&1; then
  CXX="${FORMAL_CXX_BIN}" cargo build --release --locked ${CARGO_OFFLINE_FLAG} \
    --manifest-path "${ROOT}/experiments/02_diskann_fair/Cargo.toml" \
    --bin qgraph05_shared_graph_port
  cargo build --release --locked ${CARGO_OFFLINE_FLAG} \
    --manifest-path "${ROOT}/baselines/diskann/Cargo.toml" \
    -p diskann-disk --bin qgraph05_diskann_port
elif command -v conda >/dev/null 2>&1 && \
     conda run -n rust-build cargo --version >/dev/null 2>&1; then
  conda run -n rust-build env CXX="${FORMAL_CXX_BIN}" \
    cargo build --release --locked ${CARGO_OFFLINE_FLAG} \
    --manifest-path "${ROOT}/experiments/02_diskann_fair/Cargo.toml" \
    --bin qgraph05_shared_graph_port
  conda run -n rust-build cargo build --release --locked ${CARGO_OFFLINE_FLAG} \
    --manifest-path "${ROOT}/baselines/diskann/Cargo.toml" \
    -p diskann-disk --bin qgraph05_diskann_port
else
  echo "ERROR: cargo is unavailable; activate the rust-build environment" >&2
  exit 2
fi

"${BUILD_ROOT}/05_disk_system_fair/qgraph05_direct_io_test"
echo "repository-local formal builds completed under ${BUILD_ROOT}"
