#!/usr/bin/env python3
"""Re-measure the two changed methods, then assemble and re-render each dataset's 03 figure.

Why this exists: the run configuration of a canonical `<ds>_aligned_20260921_03_ram4` run freezes
its method set, so re-measuring only Ours-Disk and DiskANN-PQ-Disk needs a dedicated pair run-id.
Glass-NSG and SymphonyQG are not re-measured (their binaries are unchanged); the assembly step
restores their previously admitted rows and records that reuse in the table manifest.

Usage (host execution; numactl must be on PATH - this script sets it):
    python scripts/run_03_pair_and_render.py gist agnews dbpedia
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pair_command(dataset: str) -> list[str]:
    source = ROOT / f"results/diagnostics/aligned_20260921/{dataset}_aligned_20260921_03_ram4/command.json"
    cmd = json.loads(source.read_text())
    cmd[cmd.index("--methods") + 1] = "Ours-Disk,DiskANN-PQ-Disk"
    cmd[cmd.index("--run-id") + 1] = f"{dataset}_aligned_20260921_03_pair"
    return cmd


def main() -> int:
    datasets = sys.argv[1:] or ["gist", "agnews", "dbpedia"]
    env = dict(os.environ)
    env["PATH"] = str(ROOT / "work/tools/numactl/root/usr/bin") + os.pathsep + env["PATH"]
    logs = ROOT / "results/diagnostics/aligned_20260921"
    logs.mkdir(parents=True, exist_ok=True)
    for dataset in datasets:
        print(f"=== {dataset}: pair measurement", flush=True)
        with (logs / f"pair_{dataset}.log").open("a") as stream:
            subprocess.run(pair_command(dataset), cwd=ROOT, env=env,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
        print(f"=== {dataset}: assemble table", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/assemble_reused_03_table.py"),
                        "--dataset", dataset,
                        "--pair-run-id", f"{dataset}_aligned_20260921_03_pair"], cwd=ROOT, check=True)
        print(f"=== {dataset}: render figure", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/render_03_dataset.py"),
                        "--dataset", dataset], cwd=ROOT, check=True)
    print("all datasets done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
