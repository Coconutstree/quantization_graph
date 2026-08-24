#!/usr/bin/env bash
# Fetch Faiss at the pinned commit and build the static library used by
# experiments/01_quantizer_fair. Expected paths (see that CMakeLists):
#   baselines/faiss/upstream            (source tree)
#   baselines/builds/faiss-cmake43      (build tree with faiss/libfaiss.a)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/baselines/faiss/upstream"
BUILD="${ROOT}/baselines/builds/faiss-cmake43"
COMMIT="${FAISS_COMMIT:-a424dcb809fd725c44dd976d9063febd4837d16a}"
REPO_URL="${FAISS_REPO:-https://github.com/facebookresearch/faiss}"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
GENERATOR="${CMAKE_GENERATOR:-Ninja}"
CXX_BIN="${CXX_BIN:-g++}"
JOBS="${JOBS:-$(nproc)}"
LOCAL_PREFIX="${ROOT}/baselines/deps/local"
OPENBLAS_LIB="${OPENBLAS_LIB:-${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/openblas-pthread/libopenblas.so.0}"

if [[ ! -f "${SRC}/CMakeLists.txt" ]]; then
  git clone "${REPO_URL}" "${SRC}"
fi
if [[ -d "${SRC}/.git" ]]; then
  git -C "${SRC}" fetch --all --tags
  git -C "${SRC}" checkout "${COMMIT}"
else
  echo "Using repository-local Faiss source archive (${COMMIT}) at ${SRC}"
fi

"${CMAKE_BIN}" -S "${SRC}" -B "${BUILD}" \
  -G "${GENERATOR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
  -DFAISS_ENABLE_GPU=OFF \
  -DFAISS_ENABLE_PYTHON=OFF \
  -DBUILD_TESTING=OFF \
  -DFAISS_OPT_LEVEL=avx2 \
  -DBLAS_LIBRARIES="${OPENBLAS_LIB}" \
  -DLAPACK_LIBRARIES="${OPENBLAS_LIB}"
"${CMAKE_BIN}" --build "${BUILD}" -j "${JOBS}"
