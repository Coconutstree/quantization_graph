#!/usr/bin/env bash
# One-click setup for non-pip dependencies used by the three experiments.
#
# What it does:
#   1. Faiss        : clone pinned commit + build libfaiss.a (via setup_faiss.sh)
#   2. SAQ          : clone pinned commit + build create_index
#                     (uses repository-local fmt/glog; gflags remains a system lib)
#   3. SymphonyQG   : clone pinned commit + build Python binding into
#                     $SYMPHONYQG_PYTHONPATH (default baselines/symphonyqg/python)
#   4. Datasets     : optional - download public datasets (--with-data), or
#                     drop your own (possibly private) data into data/<dataset>/
#
# Prerequisite tools: git, cmake>=3.20, g++ (C++17+OpenMP), Rust (cargo/rustc),
# python3 with numpy/pybind11 (pip install -r requirements.txt first).
# Network is required for cloning/downloads.
#
# Usage:
#   bash scripts/setup_deps.sh               # baselines only (no datasets)
#   bash scripts/setup_deps.sh --with-data   # + download public datasets
#   # 私有数据集：自行放入 data/<dataset>/（见 README 数据集说明）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}" || exit 1
JOBS="${JOBS:-$(nproc)}"
CXX_BIN="${CXX_BIN:-g++}"
if [[ "${CXX_BIN}" != /* ]]; then
  CXX_BIN="$(command -v "${CXX_BIN}")"
fi
CMAKE_GENERATOR="${CMAKE_GENERATOR:-Ninja}"
SYMPHONYQG_PYTHONPATH="${SYMPHONYQG_PYTHONPATH:-${ROOT}/baselines/symphonyqg/python}"
LOCAL_PREFIX="${ROOT}/baselines/deps/local"

FAISS_COMMIT="${FAISS_COMMIT:-a424dcb809fd725c44dd976d9063febd4837d16a}"
SAQ_COMMIT="${SAQ_COMMIT:-2163ebcedd0ad9c9f4de326e6ca7a860f9eafe52}"
SYM_COMMIT="${SYM_COMMIT:-6124ddb34ee4d176edea1bd7ad38d1672343df28}"
SAQ_REPO="${SAQ_REPO:-https://github.com/howarlii/saq}"
SYM_REPO="${SYM_REPO:-https://github.com/gouyt13/SymphonyQG}"

step() { echo; echo "===== [setup_deps] $* ====="; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ---------- tool checks ----------
step "checking toolchain"
for tool in git cmake g++ python3; do
  command -v "$tool" >/dev/null 2>&1 || die "missing: $tool (install via conda env: conda env create -f environment.yml, or apt: cmake g++ git python3)"
done
if ! command -v cargo >/dev/null 2>&1 && ! command -v rustc >/dev/null 2>&1; then
  if command -v conda >/dev/null 2>&1 && \
     conda run -n rust-build cargo --version >/dev/null 2>&1; then
    echo "Rust toolchain: conda environment rust-build"
  else
    echo "WARN: Rust is unavailable; dependency setup can continue, but the 02 runner will not build."
  fi
fi

# ---------- repository-local C/C++ dependencies ----------
if [[ "${SKIP_CPP_DEPS:-0}" != "1" ]]; then
  step "preparing repository-local C/C++ dependencies"
  bash scripts/setup_cpp_deps_local.sh || die "C/C++ dependency setup failed"
fi

# ---------- 1. Faiss ----------
FAISS_LIB="${ROOT}/baselines/builds/faiss-cmake43/faiss/libfaiss.a"
if [[ -f "${FAISS_LIB}" ]]; then
  echo "Faiss already built: ${FAISS_LIB}"
else
  step "building Faiss (pinned ${FAISS_COMMIT})"
  FAISS_COMMIT="${FAISS_COMMIT}" bash scripts/setup_faiss.sh || die "Faiss build failed"
fi

# ---------- 2. SAQ ----------
SAQ_DIR="${ROOT}/baselines/saq"
SAQ_BIN="${ROOT}/baselines/saq/bin/create_index"
SAQ_LOCAL_RUNTIME=0
if [[ -x "${SAQ_BIN}" ]] && command -v readelf >/dev/null 2>&1 && \
   readelf -d "${SAQ_BIN}" 2>/dev/null | grep -Fq "${LOCAL_PREFIX}/lib"; then
  SAQ_LOCAL_RUNTIME=1
fi
if [[ "${SAQ_LOCAL_RUNTIME}" == "1" ]]; then
  echo "SAQ create_index already built: ${SAQ_BIN}"
else
  step "cloning SAQ (pinned ${SAQ_COMMIT})"
  if [[ ! -f "${SAQ_DIR}/CMakeLists.txt" ]]; then
    git clone "${SAQ_REPO}" "${SAQ_DIR}" || die "SAQ clone failed"
  fi
  if [[ -d "${SAQ_DIR}/.git" ]]; then
    git -C "${SAQ_DIR}" fetch --all --tags || true
    git -C "${SAQ_DIR}" checkout "${SAQ_COMMIT}" || die "SAQ checkout failed"
  else
    echo "Using repository-local SAQ source archive (${SAQ_COMMIT}) at ${SAQ_DIR}"
  fi
  if [[ ! -f "${LOCAL_PREFIX}/lib/cmake/glog/glog-config.cmake" || \
        ! -f "${LOCAL_PREFIX}/lib/cmake/fmt/fmt-config.cmake" ]]; then
    die "repository-local SAQ dependencies are missing under ${LOCAL_PREFIX}"
  fi
  step "building SAQ create_index"
  SAQ_BUILD_DIR="${ROOT}/baselines/builds/saq-gcc11"
  if [[ -f "${SAQ_BUILD_DIR}/CMakeCache.txt" ]] && \
     ! grep -Fq "CMAKE_CXX_COMPILER:FILEPATH=${CXX_BIN}" "${SAQ_BUILD_DIR}/CMakeCache.txt"; then
    rm -f "${SAQ_BUILD_DIR}/CMakeCache.txt"
    rm -rf "${SAQ_BUILD_DIR}/CMakeFiles"
  fi
  cmake -S "${SAQ_DIR}" -B "${SAQ_BUILD_DIR}" \
    -G "${CMAKE_GENERATOR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
    -DBUILD_UNIT_TESTS=OFF \
    -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-${LOCAL_PREFIX};/usr}" \
    -DCMAKE_MODULE_PATH="${SAQ_CMAKE_MODULE_PATH:-${LOCAL_PREFIX}/usr/share/glog/cmake}" \
    -DUnwind_INCLUDE_DIR="${SAQ_UNWIND_INCLUDE_DIR:-${LOCAL_PREFIX}/usr/include}" \
    -DUnwind_LIBRARY="${SAQ_UNWIND_LIBRARY:-${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/libunwind.so}" || die "SAQ cmake configure failed"
  cmake --build "${SAQ_BUILD_DIR}" --target create_index test_qps test_relative_error -j "${JOBS}" || die "SAQ build failed"
fi
echo "NOTE: SAQ 实验还需 baselines/saq/data/<dataset>（PCA 后的库/查询/质心）。"
echo "      该数据由你的数据集生成（见 BASELINE_EXPERIMENT_PLAN_MS_V2.md 的 SAQ 数据准备），"
echo "      私有数据需你自己准备后放入对应目录。"

# ---------- 3. SymphonyQG ----------
SYM_DIR="${ROOT}/baselines/symphonyqg"
SYM_SO="${SYMPHONYQG_PYTHONPATH}/symphonyqg"*.so
if compgen -G "${SYM_SO}" >/dev/null; then
  echo "SymphonyQG binding already installed: ${SYMPHONYQG_PYTHONPATH}"
else
  step "cloning SymphonyQG (pinned ${SYM_COMMIT})"
  if [[ ! -f "${SYM_DIR}/CMakeLists.txt" ]]; then
    git clone "${SYM_REPO}" "${SYM_DIR}" || die "SymphonyQG clone failed"
  fi
  if [[ -d "${SYM_DIR}/.git" ]]; then
    git -C "${SYM_DIR}" fetch --all --tags || true
    git -C "${SYM_DIR}" checkout "${SYM_COMMIT}" || die "SymphonyQG checkout failed"
  else
    echo "Using repository-local SymphonyQG source archive (${SYM_COMMIT}) at ${SYM_DIR}"
  fi
  step "building SymphonyQG Python binding"
  mkdir -p "${SYMPHONYQG_PYTHONPATH}"
  CXX="${CXX_BIN}" python3 -m pip install --no-deps --target "${SYMPHONYQG_PYTHONPATH}" "${SYM_DIR}/python" \
    || die "SymphonyQG pip install failed"
fi
echo "export SYMPHONYQG_PYTHONPATH=${SYMPHONYQG_PYTHONPATH}   # add to your shell"

# ---------- 4. Datasets (optional) ----------
WITH_DATA=0
for arg in "$@"; do
  [[ "$arg" == "--with-data" ]] && WITH_DATA=1
done
if [[ "${WITH_DATA}" == "1" ]]; then
  step "downloading public datasets"
  bash scripts/download_data.sh || die "dataset download failed"
else
  echo "SKIP datasets：公开数据集用 'bash scripts/setup_deps.sh --with-data' 下载；"
  echo "      私有数据集请自行放入 data/<dataset>/（<dataset>_base.fvecs / _query.fvecs / _groundtruth.ivecs）。"
fi

step "ALL DONE (setup_deps)"
echo "下一步：bash scripts/build_formal_local.sh"
echo "05 磁盘实验前再运行：python experiments/05_disk_system_fair/run_disk_suite.py --phase doctor --layer all --disk-root <NVME>"
