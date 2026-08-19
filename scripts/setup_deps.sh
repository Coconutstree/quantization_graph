#!/usr/bin/env bash
# One-click setup for non-pip dependencies used by the three experiments.
#
# What it does:
#   1. Faiss        : clone pinned commit + build libfaiss.a (via setup_faiss.sh)
#   2. SAQ          : clone pinned commit + build test_fixed_candidates
#                     (needs apt deps: libfmt/glog/gflags/gtest, see below)
#   3. SymphonyQG   : clone pinned commit + build Python binding into
#                     $SYMPHONYQG_PYTHONPATH (default /tmp/baseline_python/symphonyqg)
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
CXX_BIN="${CXX_BIN:-g++-11}"
SYMPHONYQG_PYTHONPATH="${SYMPHONYQG_PYTHONPATH:-/tmp/baseline_python/symphonyqg}"

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
  echo "Rust not found. Install via:  conda install -n quantization-graph -c conda-forge rust   (or rustup)"
  echo "If you already have rust in a conda env, activate it and re-run this script."
  exit 1
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
SAQ_BIN="${ROOT}/baselines/builds/saq-gcc11/src/test_fixed_candidates"
if [[ -x "${SAQ_BIN}" ]]; then
  echo "SAQ test_fixed_candidates already built: ${SAQ_BIN}"
else
  step "cloning SAQ (pinned ${SAQ_COMMIT})"
  if [[ ! -d "${SAQ_DIR}/.git" ]]; then
    git clone "${SAQ_REPO}" "${SAQ_DIR}" || die "SAQ clone failed"
  fi
  git -C "${SAQ_DIR}" fetch --all --tags || true
  git -C "${SAQ_DIR}" checkout "${SAQ_COMMIT}" || die "SAQ checkout failed"
  if ! ldconfig -p 2>/dev/null | grep -q libfmt; then
    echo "WARN: libfmt not found. SAQ 需要系统依赖，请先安装："
    echo "      sudo apt install libfmt-dev libgoogle-glog-dev libgflags-dev libgtest-dev"
    echo "      （可用 SAQ_CMAKE_PREFIX_PATH 指定自定义依赖路径）"
  fi
  step "building SAQ test_fixed_candidates"
  cmake -S "${SAQ_DIR}" -B "${ROOT}/baselines/builds/saq-gcc11" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
    -DBUILD_UNIT_TESTS=OFF \
    -DCMAKE_PREFIX_PATH="${SAQ_CMAKE_PREFIX_PATH:-/usr}" || die "SAQ cmake configure failed"
  cmake --build "${ROOT}/baselines/builds/saq-gcc11" --target test_fixed_candidates -j "${JOBS}" || die "SAQ build failed"
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
  if [[ ! -d "${SYM_DIR}/.git" ]]; then
    git clone "${SYM_REPO}" "${SYM_DIR}" || die "SymphonyQG clone failed"
  fi
  git -C "${SYM_DIR}" fetch --all --tags || true
  git -C "${SYM_DIR}" checkout "${SYM_COMMIT}" || die "SymphonyQG checkout failed"
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
echo "下一步：按 README 编译 01/02，然后 OUT_ROOT=results bash scripts/run_master_round2.sh"
