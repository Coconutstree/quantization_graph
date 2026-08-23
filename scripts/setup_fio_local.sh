#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE_ROOT="${ROOT}/baselines/tools/fio-package"
INSTALL_ROOT="${PACKAGE_ROOT}/root"
FIO_BIN="${INSTALL_ROOT}/usr/bin/fio"
LIB_ROOT="${INSTALL_ROOT}/usr/lib/x86_64-linux-gnu"

if [[ -x "${FIO_BIN}" ]]; then
  FIO_LIB="${LIB_ROOT}:${LIB_ROOT}/ceph:${INSTALL_ROOT}/lib/x86_64-linux-gnu"
  if LD_LIBRARY_PATH="${FIO_LIB}" "${FIO_BIN}" --version >/dev/null 2>&1; then
    echo "repository-local fio already ready: ${FIO_BIN}"
    exit 0
  fi
fi

mkdir -p "${PACKAGE_ROOT}"
cd "${PACKAGE_ROOT}"
apt-get download \
  fio libgfapi0 librados2 librbd1 libpmemblk1 libpmem1 \
  libglusterfs0 libgfrpc0 libgfxdr0 ceph-common libndctl6 libdaxctl1
for package in ./*.deb; do
  dpkg-deb -x "${package}" "${INSTALL_ROOT}"
done

FIO_LIB="${LIB_ROOT}:${LIB_ROOT}/ceph:${INSTALL_ROOT}/lib/x86_64-linux-gnu"
LD_LIBRARY_PATH="${FIO_LIB}" "${FIO_BIN}" --version
echo "repository-local fio installed under ${PACKAGE_ROOT}"
