"""Profile the pinned Ours executable without changing search code."""
import datetime
import hashlib
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

def main():
    base = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair"
    output = base / ("ours_aio_timing_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    output.mkdir()
    source = ROOT / "scripts/local_runs/audit_aio_timing.cpp"
    library = output / "aio_timing.so"
    subprocess.run(["g++", "-std=c++17", "-O2", "-shared", "-fPIC", str(source), "-ldl", "-o", str(library)], check=True)
    args = json.loads((base / "single_test_w32_20260911/agnews/Ours-Disk/command.json").read_text())
    sha = hashlib.sha256(pathlib.Path(args[0]).read_bytes()).hexdigest()
    assert sha == args[args.index("--native-binary-sha256") + 1]
    for key, value in {"--integration-widths": "12", "--result-json": str(output / "result.json"), "--query-trace": str(output / "queries.jsonl")}.items():
        args[args.index(key) + 1] = value
    env = os.environ.copy()
    if env.get("LD_PRELOAD"):
        raise RuntimeError("Existing LD_PRELOAD would confound diagnostic")
    env.update(LD_PRELOAD=str(library), QG_AIO_TIMING_OUTPUT=str(output / "aio_timing.csv"))
    (output / "command.json").write_text(json.dumps(args, indent=2))
    (output / "probe.json").write_text(json.dumps({"LD_PRELOAD": str(library), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "probe_sha256": hashlib.sha256(library.read_bytes()).hexdigest(), "scope": "whole process including startup, 100 warmup queries, 800 measured queries and teardown; thread-wall times are summed across threads"}, indent=2))
    print(output, flush=True)
    with (output / "terminal.log").open("w") as log:
        subprocess.run(args, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    print((output / "aio_timing.csv").read_text(), flush=True)
    print(json.dumps(json.loads((output / "result.json").read_text())["summary_rows"]), flush=True)

if __name__ == "__main__":
    main()
