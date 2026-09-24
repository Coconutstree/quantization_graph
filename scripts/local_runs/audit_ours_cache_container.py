"""Compare cache containers only; outputs are never formal benchmark results."""
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import argparse

ROOT = pathlib.Path(__file__).resolve().parents[2]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True,
                        help="Explicit diagnostic binary built with the saved cache bridge")
    options = parser.parse_args()
    base = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair"
    output = base / ("ours_cache_container_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    output.mkdir()
    binary = output / "diagnostic_ours"
    shutil.copy2(options.binary, binary)
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    command = json.loads((base / "single_test_w32_20260911/agnews/Ours-Disk/command.json").read_text())
    command[0] = str(binary)
    (output / "README.md").write_text("# Cache container diagnostic\n\nNot formal results. Same rebuilt binary, bounded / unbounded hash-map / bounded.\nThe alternative is not a reconstruction of the historical Rust HashMap implementation.\nUnbounded cache metadata memory is not fully accounted for.\nSearch code and formal pinned binary are unchanged.\n")
    print(output, flush=True)
    for label, mode in [("01_bounded", "bounded"), ("02_hashmap", "unbounded_map"), ("03_bounded", "bounded")]:
        folder = output / label
        folder.mkdir()
        args = command.copy()
        for key, value in {"--integration-widths": "12", "--native-binary-sha256": sha,
                           "--result-json": str(folder / "result.json"),
                           "--query-trace": str(folder / "queries.jsonl")}.items():
            args[args.index(key) + 1] = value
        env = os.environ.copy()
        if env.get("LD_PRELOAD"):
            raise RuntimeError("Unexpected preload")
        env["QG05_DIAGNOSTIC_CACHE"] = mode
        (folder / "command.json").write_text(json.dumps(args, indent=2))
        (folder / "diagnostic.json").write_text(json.dumps({"formal_ready": False, "cache_container": mode, "binary_sha256": sha, "memory_accounting_complete": False}, indent=2))
        with (folder / "terminal.log").open("w") as log:
            subprocess.run(args, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        data = json.loads((folder / "result.json").read_text())
        print(label, json.dumps(data["summary_rows"]), flush=True)

if __name__ == "__main__":
    main()
