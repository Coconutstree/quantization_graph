"""Isolated debugger reproduction; never writes performance-run artifacts."""
import json
from pathlib import Path
import subprocess
import sys
import hashlib

root = Path(__file__).resolve().parents[2]
source = root / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/single_test_w32_20260911/agnews/DiskANN-PQ-Disk/command.json"
command = json.loads(source.read_text())
flags = dict(zip(command[1::2], command[2::2]))
out = root / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair" / (sys.argv[2] if len(sys.argv) > 2 else "diskann_crash_diagnostic")
out.mkdir(exist_ok=True)
flags.update({"--integration-widths": "500,540,580", "--result-json": str(out / "result.json"),
              "--query-trace": str(out / "queries.jsonl")})
args = [command[0]] + [v for pair in flags.items() for v in pair]
if len(sys.argv) > 1:
    args[0] = sys.argv[1]
    with Path(args[0]).open("rb") as stream:
        args[args.index("--native-binary-sha256") + 1] = hashlib.file_digest(stream, "sha256").hexdigest()
    (out / "command.json").write_text(json.dumps(args, indent=2) + "\n")
    with (out / "sanitizer.log").open("w") as log:
        sys.exit(subprocess.call(args, cwd=root, stdout=log, stderr=subprocess.STDOUT))
with (out / "gdb.log").open("w") as log:
    sys.exit(subprocess.call(["gdb", "-q", "-batch", "-ex", "set pagination off",
                             "-ex", "run", "-ex", "bt", "-ex", "info registers",
                             "--args", *args], cwd=root, stdout=log, stderr=subprocess.STDOUT))
