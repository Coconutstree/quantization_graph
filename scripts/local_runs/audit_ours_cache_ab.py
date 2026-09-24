"""Isolated current-binary cache comparison; never changes formal outputs."""
import hashlib
import json
import pathlib
import subprocess
import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/single_test_w32_20260911/agnews/Ours-Disk/command.json"

def main():
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair" / ("ours_cache_audit_" + stamp)
    output.mkdir()
    command = json.loads(SOURCE.read_text())
    actual = hashlib.sha256(pathlib.Path(command[0]).read_bytes()).hexdigest()
    assert actual == command[command.index("--native-binary-sha256") + 1]
    print(output, flush=True)
    for label, mode in [("01_c0", "c0"), ("02_standard", "standard"), ("03_c0", "c0")]:
        folder = output / label
        folder.mkdir()
        args = command.copy()
        for key, value in {"--integration-widths": "12", "--cache-mode": mode,
                           "--result-json": str(folder / "result.json"),
                           "--query-trace": str(folder / "queries.jsonl")}.items():
            args[args.index(key) + 1] = value
        (folder / "command.json").write_text(json.dumps(args, indent=2))
        with (folder / "terminal.log").open("w") as log:
            result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        print(label, "exit", result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
        data = json.loads((folder / "result.json").read_text())
        print(json.dumps({"cache_bytes": data.get("cache_bytes"), "rows": data["summary_rows"]}), flush=True)

if __name__ == "__main__":
    main()
