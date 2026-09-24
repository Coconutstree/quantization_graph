"""Run the required 4 KiB direct random-read fio characterization on the disk root."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_FIO_ROOT = REPO_ROOT / "baselines" / "tools" / "fio-package" / "root"
LOCAL_FIO = LOCAL_FIO_ROOT / "usr" / "bin" / "fio"


def resolve_fio() -> tuple[Path | None, dict[str, str]]:
    """Resolve system fio or the repository-local, non-root installation."""
    override = os.environ.get("QGRAPH_FIO")
    if override:
        candidate = Path(override).expanduser().resolve()
    else:
        system = shutil.which("fio")
        candidate = Path(system).resolve() if system else LOCAL_FIO
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None, os.environ.copy()
    env = os.environ.copy()
    if candidate == LOCAL_FIO:
        lib = LOCAL_FIO_ROOT / "usr" / "lib" / "x86_64-linux-gnu"
        paths = (lib, lib / "ceph", LOCAL_FIO_ROOT / "lib" / "x86_64-linux-gnu")
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(
            [*(str(path) for path in paths), *([existing] if existing else [])]
        )
    return candidate, env


def _percentile(job: dict[str, Any], name: str) -> float | None:
    percentiles = job.get("read", {}).get("clat_ns", {}).get("percentile", {})
    aliases = {
        "p95": ("95.000000", "95.000000%"),
        "p99": ("99.000000", "99.000000%"),
    }
    for key in aliases[name]:
        if key in percentiles:
            return float(percentiles[key]) / 1000.0
    return None


def run_fio(
    disk_root: Path,
    output: Path,
    *,
    runtime_s: int = 10,
    size_gib: int = 8,
    depths: tuple[int, ...] = (1, 4, 16, 32, 64, 128),
) -> dict[str, Any]:
    fio, fio_env = resolve_fio()
    if fio is None:
        raise RuntimeError(
            "fio executable is required; run scripts/setup_fio_local.sh or install fio"
        )
    disk_root.mkdir(parents=True, exist_ok=True)
    fd, scratch_name = tempfile.mkstemp(prefix="qgraph05_fio_", suffix=".bin", dir=disk_root)
    os.close(fd)
    scratch = Path(scratch_name)
    results = []
    try:
        for depth in depths:
            command = [
                str(fio),
                f"--name=qgraph05_qd{depth}",
                f"--filename={scratch}",
                "--rw=randread",
                "--bs=4k",
                "--direct=1",
                "--ioengine=libaio",
                f"--iodepth={depth}",
                "--numjobs=1",
                "--thread=1",
                "--time_based=1",
                f"--runtime={runtime_s}",
                "--ramp_time=2",
                f"--size={size_gib}G",
                "--group_reporting=1",
                "--output-format=json",
            ]
            proc = subprocess.run(
                command, capture_output=True, text=True, env=fio_env
            )
            if proc.returncode:
                raise RuntimeError(
                    f"fio qd={depth} failed ({proc.returncode}): {proc.stderr.strip()}"
                )
            raw = json.loads(proc.stdout)
            job = raw["jobs"][0]
            read = job["read"]
            results.append(
                {
                    "queue_depth": depth,
                    "iops": float(read["iops"]),
                    "bandwidth_kib_s": float(read["bw"]),
                    "latency_mean_us": float(read["clat_ns"]["mean"]) / 1000.0,
                    "latency_p95_us": _percentile(job, "p95"),
                    "latency_p99_us": _percentile(job, "p99"),
                }
            )
        report = {
            "schema_version": 1,
            "status": "passed",
            "disk_root": str(disk_root.resolve()),
            "fio_executable": str(fio),
            "ioengine": "libaio",
            "direct": True,
            "block_size": 4096,
            "runtime_s": runtime_s,
            "size_gib": size_gib,
            "results": results,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report
    finally:
        scratch.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-s", type=int, default=10)
    parser.add_argument("--size-gib", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        run_fio(args.disk_root.resolve(), args.output.resolve(), runtime_s=args.runtime_s, size_gib=args.size_gib)
        return 0
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: fio preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
