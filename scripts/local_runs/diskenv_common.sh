#!/usr/bin/env bash
# Shared local environment defaults for the 05 disk-environment launchers.

qg05_setup_diskenv() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ROOT="${ROOT:-$(cd "${script_dir}/../.." && pwd)}"
  cd "${ROOT}"

  export QG05_FAST="${QG05_FAST:-1}"
  export QG05_OURS_ABLATIONS="${QG05_OURS_ABLATIONS:-full4-resident/no-gate}"
  export QG05_DEBUG_WORKERS="${QG05_DEBUG_WORKERS:-1 2 4 8 16 32}"

  if [[ -d "${HOME}/.local/bin" ]]; then
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
  if [[ -d "${HOME}/.cargo/bin" ]]; then
    export PATH="${HOME}/.cargo/bin:${PATH}"
  fi

  export QG_LOCAL="${QG_LOCAL:-${ROOT}/baselines/deps/local}"
  local qg_local_lib="${QG_LOCAL}/usr/lib/x86_64-linux-gnu"
  export QG_LOCAL_LIB="${QG_LOCAL_LIB:-${qg_local_lib}}"
  export LD_LIBRARY_PATH="${QG_LOCAL_LIB}:${QG_LOCAL_LIB}/openblas-pthread:${LD_LIBRARY_PATH:-}"
  export LIBRARY_PATH="${QG_LOCAL_LIB}:${QG_LOCAL_LIB}/openblas-pthread:${LIBRARY_PATH:-}"
  export CPLUS_INCLUDE_PATH="${QG_LOCAL}/usr/include:${CPLUS_INCLUDE_PATH:-}"

  DISK_ROOT="${DISK_ROOT:-${ROOT}/work/05_disk_system_fair/disk_root}"
  OUT_ROOT="${OUT_ROOT:-results/archive/legacy_layout_20260918/disk_environment/.formal_runs}"
  PORTS="${PORTS:-src/disk_bench/ports.local.json}"
  DISK_PROFILE="${DISK_PROFILE:-auto}"
}
