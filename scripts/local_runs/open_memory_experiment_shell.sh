#!/usr/bin/env bash
# Run as kai3 on the host. sudo is used only to create a transient service.
set -euo pipefail
script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
repo_path=$(dirname -- "$(dirname -- "$(dirname -- "$script_path")")")

if [[ ${1:-} != --delegated-child ]]; then
    [[ $(id -un) == kai3 && $(id -u) != 0 ]] || {
        echo 'Run this script as kai3, without prefixing the script with sudo.' >&2
        exit 1
    }
    exec sudo /usr/bin/systemd-run \
        --unit="qgraph-memory-kai3-$(date +%s)-$$" \
        --uid=kai3 --gid=kai3 --pty --wait --collect \
        --property=Delegate=yes --property=MemoryAccounting=yes \
        --property=UMask=0077 \
        -- /bin/bash "$script_path" --delegated-child "$(command -v python3)"
fi

[[ $(id -un) == kai3 && $(id -u) != 0 ]] || exit 1
experiment_python=${2:?Missing Python interpreter path}
cg_relative=$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup)
[[ $cg_relative == /system.slice/qgraph-memory-kai3-*.service ]] || {
    echo 'Not inside the dedicated transient service; refusing cgroup changes.' >&2
    exit 1
}
cg_parent="/sys/fs/cgroup${cg_relative}"
[[ -w $cg_parent/cgroup.subtree_control ]] || {
    echo 'The service cgroup was not delegated or the mount is read-only.' >&2
    exit 1
}
# Leave the delegated parent empty before enabling controllers for children.
mkdir -- "$cg_parent/supervisor"
printf '%s\n' "$$" > "$cg_parent/supervisor/cgroup.procs"
printf '+memory\n' > "$cg_parent/cgroup.subtree_control"
export QG05_CGROUP_PARENT="$cg_parent"
cd -- "$repo_path"
evidence_dir=$(mktemp -d "$repo_path/results/cgroup_setup_XXXXXXXX")
"$experiment_python" src/disk_bench/memory_runner.py --check \
    --cgroup-parent "$cg_parent" --budget-gib 1 | tee "$evidence_dir/environment.json"
# Verify an actual child can enter the capped group; no ANN benchmark is run.
"$experiment_python" src/disk_bench/memory_runner.py \
    --cgroup-parent "$cg_parent" --budget-gib 1 \
    --output "$evidence_dir/launch_smoke" -- /usr/bin/python3 -c \
    'import pathlib; data=bytearray(16*1024*1024); print(pathlib.Path("/proc/self/cgroup").read_text()); print(len(data))' \
    | tee "$evidence_dir/launch_smoke_summary.json"
printf '\nDedicated experiment shell ready.\nQG05_CGROUP_PARENT=%s\nEvidence: %s\n' "$cg_parent" "$evidence_dir"
printf 'Launch memory_runner-controlled experiments from this shell. Plain commands are NOT capped.\nExit this shell only after experiments finish; exiting stops the transient service.\n'
export PS1='[qgraph-memory] \u@\h:\w\$ '
exec /bin/bash --noprofile --norc -i
