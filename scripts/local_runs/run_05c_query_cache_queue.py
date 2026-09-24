"""Serial background diagnostic queue; no graph builds or formal acceptance."""
import json
import argparse
import shutil
import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--warmup-queries", type=int, default=0)
    parser.add_argument("--cpp-binary-root", type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    binaries = out / "binaries"
    binaries.mkdir()
    from check_03c_query_cache import METHODS
    pinned = {}
    for method in METHODS:
        recorded = ROOT / "artifacts/graphs/reader_audit/agnews" / method / "command.json"
        source = Path(json.loads(recorded.read_text())[0])
        if args.cpp_binary_root and method not in ("Ours-Disk", "DiskANN-PQ-Disk"):
            source = args.cpp_binary_root.resolve() / source.name
        target = binaries / source.name
        shutil.copy2(source, target)
        with target.open("rb") as stream:
            pinned[method] = {"path": str(target), "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
    (out / "binaries.json").write_text(json.dumps(pinned, indent=2) + "\n")
    widths = list(range(1, 31)) + list(range(40, 101, 10)) + list(range(140, 581, 40))
    assert len(widths) == 49
    (out / "run.json").write_text(json.dumps({
        "acceptance": "pending", "formal_ready": False, "split": args.split,
        "datasets": ["agnews", "gist", "dbpedia", "sift10m"], "methods": METHODS,
        "workers": 32, "cache_mode": "c0", "query_cache_bytes_per_worker": 4194304,
        "repeats": 1, "warmup_queries": args.warmup_queries, "widths": widths,
        "remaining_gate": "complete workspace memory accounting and implementation parity",
    }, indent=2) + "\n")
    rows = []
    for dataset in ("agnews", "gist", "dbpedia", "sift10m"):
        row = {"dataset": dataset, "status": "running", "started": datetime.now().isoformat()}
        rows.append(row)
        (out / "queue.json").write_text(json.dumps(rows, indent=2) + "\n")
        command = [sys.executable, str(ROOT / "scripts/local_runs/check_03c_query_cache.py"),
                   "--dataset", dataset, "--output", str(out / dataset),
                   "--split", args.split, "--workers", "32", "--timeout", "86400",
                   "--warmup-queries", str(args.warmup_queries), "--binary-root", str(binaries),
                   "--widths", ",".join(map(str, widths))]
        with (out / f"{dataset}.log").open("x") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        row.update(status="diagnostic_complete" if result.returncode == 0 else "failed",
                   exit_code=result.returncode, finished=datetime.now().isoformat())
        (out / "queue.json").write_text(json.dumps(rows, indent=2) + "\n")
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
