#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPS_ROOT="${ROOT}/baselines/deps"
LOCAL_PREFIX="${DEPS_ROOT}/local"
for tool in apt-get dpkg-deb; do
  command -v "${tool}" >/dev/null 2>&1 || { echo "missing required tool: ${tool}" >&2; exit 1; }
done

PACKAGES=(
  libaio-dev
  libopenblas-dev
  libopenblas0-pthread
  libgfortran5
  libfmt-dev
  libfmt9
  libgoogle-glog-dev
  libgoogle-glog0v6t64
  libgflags-dev
  libgflags2.2
  libunwind-dev
  libunwind8
  libssl-dev
  libssl3t64
)

need_download=0
for package in "${PACKAGES[@]}"; do
  if ! compgen -G "${DEPS_ROOT}/${package}_*.deb" >/dev/null; then
    need_download=1
    break
  fi
done

mkdir -p "${DEPS_ROOT}" "${LOCAL_PREFIX}"
cd "${DEPS_ROOT}"
if [[ "${need_download}" == "1" ]]; then
  apt-get download "${PACKAGES[@]}"
fi

for package in ./*.deb; do
  dpkg-deb -x "${package}" "${LOCAL_PREFIX}"
done

mkdir -p "${LOCAL_PREFIX}/lib" "${DEPS_ROOT}/lib"
if [[ ! -e "${LOCAL_PREFIX}/include" ]]; then
  ln -s usr/include "${LOCAL_PREFIX}/include"
fi
if [[ ! -e "${LOCAL_PREFIX}/lib/cmake" ]]; then
  ln -s ../usr/lib/x86_64-linux-gnu/cmake "${LOCAL_PREFIX}/lib/cmake"
fi
if [[ ! -e "${DEPS_ROOT}/include" ]]; then
  ln -s local/usr/include "${DEPS_ROOT}/include"
fi
if [[ ! -e "${DEPS_ROOT}/lib/x86_64-linux-gnu" ]]; then
  ln -s ../local/usr/lib/x86_64-linux-gnu "${DEPS_ROOT}/lib/x86_64-linux-gnu"
fi

OPENSSL_ARCH_INCLUDE="${LOCAL_PREFIX}/usr/include/x86_64-linux-gnu/openssl"
OPENSSL_INCLUDE="${LOCAL_PREFIX}/usr/include/openssl"
if [[ -d "${OPENSSL_ARCH_INCLUDE}" && -d "${OPENSSL_INCLUDE}" ]]; then
  for header in opensslconf.h configuration.h; do
    if [[ ! -e "${OPENSSL_INCLUDE}/${header}" && -e "${OPENSSL_ARCH_INCLUDE}/${header}" ]]; then
      ln -s "../x86_64-linux-gnu/openssl/${header}" "${OPENSSL_INCLUDE}/${header}"
    fi
  done
fi

required=(
  "${LOCAL_PREFIX}/usr/include/libaio.h"
  "${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/openblas-pthread/libopenblas.so.0"
  "${LOCAL_PREFIX}/lib/cmake/glog/glog-config.cmake"
  "${LOCAL_PREFIX}/lib/cmake/fmt/fmt-config.cmake"
  "${LOCAL_PREFIX}/lib/cmake/gflags/gflags-config.cmake"
  "${LOCAL_PREFIX}/usr/share/glog/cmake/FindUnwind.cmake"
  "${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu/libcrypto.so"
)
for item in "${required[@]}"; do
  [[ -e "${item}" ]] || { echo "missing expected local dependency: ${item}" >&2; exit 1; }
done

echo "repository-local C/C++ dependencies ready under ${LOCAL_PREFIX}"
