#!/usr/bin/env python3
"""Run the two canonical MSMARCO graph builds, without query measurements."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work/05_disk_system_fair/msmarco_graph_build_20260915"
LOG = ROOT / "logs/msmarco_graph_build_20260915"


def save(name, value):
    target = LOG / name
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(target)


def main():
    LOG.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    lock = (WORK / "queue.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = {"pid": os.getpid(), "status": "preflight", "started": time.time()}
    save("state.json", state)
    try:
        inputs = {}
        for kind, suffix in [("base", "fvecs"), ("query", "fvecs"), ("groundtruth", "ivecs")]:
            path = ROOT / f"data/msmarco/msmarco_{kind}.{suffix}"
            stat = path.stat()
            with path.open("rb") as stream:
                dim = struct.unpack("<i", stream.read(4))[0]
                assert dim > 0 and stat.st_size % (4 * (dim + 1)) == 0
                count = stat.st_size // (4 * (dim + 1))
                for row in {0, count // 2, count - 1}:
                    stream.seek(row * 4 * (dim + 1))
                    assert struct.unpack("<i", stream.read(4))[0] == dim
            inputs[kind] = dict(path=str(path), rows=count, dimension=dim,
                                bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        assert inputs["base"]["rows"] == 113520750
        assert inputs["base"]["dimension"] == inputs["query"]["dimension"] == 1024
        assert inputs["query"]["rows"] == inputs["groundtruth"]["rows"]
        available = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        if available < 1100 * 1024**3 or shutil.disk_usage(WORK).free < 250 * 1024**3:
            raise RuntimeError("Insufficient headroom: need 1100 GiB available RAM and 250 GiB disk")
        binary = WORK / "run_diskann_fair"
        script = WORK / "build_05_core_graphs.sh"
        if not binary.exists():
            shutil.copy2(ROOT / "src/graph_core/target/release/run_diskann_fair", binary)
        source_script = ROOT / "scripts/local_runs/build_05_core_graphs.sh"
        shutil.copy2(source_script, script)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        save("manifest.json", dict(inputs=inputs, input_validation="geometry and sampled headers; loader validates all rows",
             binary=str(binary), binary_sha256=digest, metric="squared_l2",
             official_groundtruth_metric_validated=False, build_only=True,
             parameters=dict(R=64, Lbuild=400, alpha=1.2, seed=20260813, build_threads=64,
                             refine_passes=1, prune_cap=256, early_stop_hops=2),
             order=["shared", "ours"]))
        for role in ["shared", "ours"]:
            state.update(status="running", role=role, role_started=time.time())
            save("state.json", state)
            env = dict(os.environ, QGRAPH_ROOT=str(ROOT), BIN=str(binary), DATASETS="msmarco", GRAPH_ROLES=role,
                       BUILD_ONLY="1", BUILD_ROOT=str(WORK / "staging"), LOG_ROOT=str(LOG), THREADS="32")
            subprocess.run(["bash", str(script)], cwd=ROOT, env=env, check=True)
        state.update(status="completed", finished=time.time())
        save("state.json", state)
    except BaseException as exc:
        state.update(status="failed", error=str(exc), finished=time.time())
        save("state.json", state)
        raise


if __name__ == "__main__":
    main()
