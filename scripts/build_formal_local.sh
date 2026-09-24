#!/usr/bin/env bash
# Build only the native disk experiment ports and their preparation tools.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ROOT="${BUILD_ROOT:-${ROOT}/build/disk}"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
GENERATOR="${CMAKE_GENERATOR:-Ninja}"
JOBS="${JOBS:-$(nproc)}"
LOCAL_PREFIX="${ROOT}/baselines/deps/local"
FORMAL_CXX_BIN="${FORMAL_CXX_BIN:-$(command -v g++-11 || command -v g++)}"
FAISS_BUILD="${ROOT}/baselines/builds/faiss-cmake43"
BLAS_ARGS=()
if [[ -n "${OPENBLAS_LIB:-}" ]]; then
  BLAS_ARGS+=("-DBLAS_LIBRARIES=${OPENBLAS_LIB}" "-DLAPACK_LIBRARIES=${OPENBLAS_LIB}")
fi
build_cmake() {
  local name="$1" source="$2"
  "${CMAKE_BIN}" -S "${source}" -B "${BUILD_ROOT}/${name}" -G "${GENERATOR}" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER="${FORMAL_CXX_BIN}" \
    -DCMAKE_PREFIX_PATH="${LOCAL_PREFIX};${LOCAL_PREFIX}/usr;/usr" \
    "${BLAS_ARGS[@]}"
  "${CMAKE_BIN}" --build "${BUILD_ROOT}/${name}" -j "${JOBS}"
}
if [[ ! -f "${FAISS_BUILD}/faiss/libfaiss.a" ]]; then
  "${CMAKE_BIN}" -S "${ROOT}/baselines/faiss/upstream" -B "${FAISS_BUILD}" -G "${GENERATOR}" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER="${FORMAL_CXX_BIN}" \
    -DFAISS_ENABLE_GPU=OFF -DFAISS_ENABLE_PYTHON=OFF -DFAISS_ENABLE_C_API=OFF \
    -DBUILD_TESTING=OFF -DFAISS_OPT_LEVEL=avx2 "${BLAS_ARGS[@]}"
  "${CMAKE_BIN}" --build "${FAISS_BUILD}" --target faiss -j "${JOBS}"
fi
build_cmake 01_disk_quantizer "${ROOT}/experiments/01_disk_quantizer/tools"
build_cmake native "${ROOT}/src/disk_bench/native"
python3 "${ROOT}/scripts/verify_diskann_baseline.py"
CARGO_FLAGS=()
if [[ "${CARGO_OFFLINE:-0}" == "1" ]]; then CARGO_FLAGS+=(--offline); fi
if command -v cargo >/dev/null 2>&1; then
  CARGO=(cargo)
elif command -v conda >/dev/null 2>&1 && conda run -n rust-build cargo --version >/dev/null 2>&1; then
  CARGO=(conda run -n rust-build cargo)
else
  echo "ERROR: cargo is unavailable; activate the rust-build environment" >&2
  exit 2
fi
CXX="${FORMAL_CXX_BIN}" "${CARGO[@]}" build --release --locked "${CARGO_FLAGS[@]}" \
  --manifest-path "${ROOT}/src/graph_core/Cargo.toml" --bins
"${CARGO[@]}" build --release --locked "${CARGO_FLAGS[@]}" \
  --manifest-path "${ROOT}/experiments/03_disk_system/native_diskann/Cargo.toml"
"${BUILD_ROOT}/native/qgraph05_direct_io_test"
echo "Disk experiment builds completed under ${BUILD_ROOT}"
