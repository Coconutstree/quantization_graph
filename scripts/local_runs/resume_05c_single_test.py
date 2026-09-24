"""Resume a stopped single-test queue using its pinned binaries and existing reports."""
import json
import os
import hashlib
import shutil
import fcntl
from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
root = Path(sys.argv[1]).resolve()
if (root / "REQUIRE_OPTIMIZED_OURS.txt").exists():
    raise RuntimeError("Legacy queue disabled: use per-dataset verified locality layouts and pinned optimized Ours binaries")
lock = (root / "resume.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
run = json.loads((root / "run.json").read_text())
queue = json.loads((root / "queue.json").read_text())
source = ROOT / "build/formal_single_run_gcc11/05_disk_system_fair/qgraph05_symphonyqg_disk_port"
pins = json.loads((root / "binaries.json").read_text())
method = "SymphonyQG-DiskPort"
target = Path(pins[method]["path"])
backup = target.with_suffix(".before_progress_logging")
if not backup.exists():
    previous = dict(pins[method])
    shutil.copy2(target, backup)
    shutil.copy2(source, target)
    with target.open("rb") as stream:
        pins[method]["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    (root / "binaries.json").write_text(json.dumps(pins, indent=2) + "\n")
    (root / "progress_logging_update.json").write_text(json.dumps({
        "previous": previous, "replacement": pins[method],
        "reason": "Width start/completion logging only; AGNews completed artifact retains its original hash",
    }, indent=2) + "\n")
for pin in pins.values():
    with Path(pin["path"]).open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != pin["sha256"]:
            raise RuntimeError("Pinned binary hash mismatch")
watcher = subprocess.Popen([sys.executable, str(ROOT / "scripts/local_runs/export_05c_pending_figures.py"),
                            str(root), "--watch-pid", str(os.getpid())])
for dataset in run["datasets"]:
    row = next((r for r in queue if r["dataset"] == dataset), None)
    if row and row["status"] == "diagnostic_complete":
        continue
    if row is None:
        row = {"dataset": dataset}
        queue.append(row)
    row.setdefault("attempt_history", []).append(dict((k, v) for k, v in row.items() if k != "attempt_history"))
    row.pop("exit_code", None)
    row.pop("finished", None)
    row.update(status="running", resumed=datetime.now().isoformat())
    (root / "queue.json").write_text(json.dumps(queue, indent=2) + "\n")
    command = [sys.executable, str(ROOT / "scripts/local_runs/check_03c_query_cache.py"),
               "--dataset", dataset, "--output", str(root / dataset), "--resume",
               "--split", run["split"], "--workers", str(run["workers"]),
               "--warmup-queries", str(run["warmup_queries"]), "--timeout", "86400",
               "--binary-root", str(root / "binaries"), "--widths", ",".join(map(str, run["widths"]))]
    with (root / f"{dataset}.resume.log").open("a") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    row.update(status="failed" if result.returncode else "diagnostic_complete",
               exit_code=result.returncode, finished=datetime.now().isoformat())
    (root / "queue.json").write_text(json.dumps(queue, indent=2) + "\n")
    if result.returncode:
        sys.exit(result.returncode)
