"""Worker script for the NGT-QG adapter (official Yahoo Japan NGT qbg CLI)."""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np


_REPO = Path(__file__).resolve().parents[4]

NGT_BIN = os.environ.get("NGT_BIN", str(_REPO / "baselines/builds/ngt-gcc11/bin/ngt/ngt"))
QBG_BIN = os.environ.get("QBG_BIN", str(_REPO / "baselines/builds/ngt-gcc11/bin/qbg/qbg"))
LIB_DIR = os.environ.get("NGT_LIB_DIR", str(_REPO / "baselines/builds/ngt-gcc11/lib/NGT"))


def base_env():
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LIB_DIR + ":" + env.get("LD_LIBRARY_PATH", "")
    env["OMP_NUM_THREADS"] = os.environ.get("OMP_NUM_THREADS", "64")
    return env


def peak_rss_mb():
    self_max = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child_max = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return max(self_max, child_max) / 1024.0


def fvecs_to_tsv(fvecs_path: Path, tsv_path: Path, chunk: int = 20000) -> None:
    if tsv_path.exists():
        return
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    with fvecs_path.open("rb") as f:
        dim = int(np.fromfile(f, dtype=np.int32, count=1)[0])
        raw = np.fromfile(f, dtype=np.float32)
    n = (raw.size + 1) // (dim + 1)
    mask = np.ones(raw.size, dtype=bool)
    if n > 1:
        mask[np.arange(1, n) * (dim + 1) - 1] = False
    arr = raw[mask].reshape(n, dim)
    with tsv_path.open("w") as out:
        for start in range(0, len(arr), chunk):
            np.savetxt(out, arr[start : start + chunk], fmt="%.6g", delimiter=" ")


def run(cmd, timeout=64800):
    proc = subprocess.run(
        [str(c) for c in cmd],
        env=base_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    return proc


def read_ivecs(path: Path):
    rows = []
    with path.open("rb") as f:
        while True:
            head = f.read(4)
            if not head:
                break
            width = int(np.frombuffer(head, dtype=np.int32)[0])
            rows.append(
                np.frombuffer(f.read(width * 4), dtype=np.int32).astype(np.int64)
            )
    return np.vstack(rows) if rows else None


def parse_search_output(text: str):
    """Parse qbg search-qg -o e output into per-(epsilon, p) measurements.

    NGT tsv-created indexes use 1-based object ids; ids are converted to
    0-based here so they can be compared against the .ivecs ground truth.
    """
    per_key: dict[tuple, dict] = {}
    current_eps: str | None = None
    current_p: str | None = None
    current_ids: list[int] = []
    current_times: list[float] = []

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# Epsilon*="):
            current_eps = line.split("=", 1)[1].strip()
        elif line.startswith("# Result Expansion*="):
            current_p = line.split("=", 1)[1].strip()
            key = (current_eps, current_p)
            if key not in per_key:
                per_key[key] = {"ids": [], "times": []}
        elif line.startswith("# Query Time (msec)="):
            if current_eps is not None and current_p is not None:
                per_key[(current_eps, current_p)]["times"].append(
                    float(line.split("=", 1)[1])
                )
        elif line and line[0].isdigit() and "\t" in line:
            rank, id_, dist = line.split("\t")
            if current_eps is not None and current_p is not None:
                per_key[(current_eps, current_p)]["ids"].append(int(id_) - 1)
    return per_key


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["build", "search"], required=True)
    ap.add_argument("--base", type=Path)
    ap.add_argument("--query", type=Path)
    ap.add_argument("--gt", type=Path)
    ap.add_argument("--index", type=Path)
    ap.add_argument("--tsv-cache", type=Path)
    ap.add_argument("--anng-edges", type=int, default=32)
    ap.add_argument("--qg-edges", type=int, default=32)
    ap.add_argument("--subdim", type=int, default=32)
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--epsilon-ranges", type=str, default="0.01:0.14:0.01,0.2:1.2:0.2")
    ap.add_argument("--expansion", type=float, default=3.0)
    ap.add_argument(
        "--p-ranges",
        type=str,
        default="1:50:5,50:500:50,500:5000:500,5000:50000:5000",
    )
    ap.add_argument("--dim", type=int, default=1536)
    args = ap.parse_args()

    if args.mode == "build":
        t0 = time.perf_counter()
        base_tsv = args.tsv_cache / (args.base.stem + ".tsv")
        fvecs_to_tsv(args.base, base_tsv)
        if args.index.exists():
            shutil.rmtree(args.index)
        args.index.mkdir(parents=True, exist_ok=True)
        proc = run(
            [
                NGT_BIN,
                "create",
                "-d", str(args.dim),
                "-o", "f",
                "-D", "2",
                "-g", "a",
                "-E", str(args.anng_edges),
                "-S", str(args.anng_edges),
                "-p", str(args.threads),
                str(args.index),
                str(base_tsv),
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ngt create failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        proc = run(
            [
                QBG_BIN,
                "create-qg",
                "-Q", str(args.subdim),
                "-p", str(args.threads),
                str(args.index),
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"qbg create-qg failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        proc = run(
            [
                QBG_BIN,
                "build-qg",
                "-Q", str(args.subdim),
                "-E", str(args.qg_edges),
                "-p", str(args.threads),
                str(args.index),
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"qbg build-qg failed rc={proc.returncode}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        build_ms = (time.perf_counter() - t0) * 1000.0
        size_mb = sum(p.stat().st_size for p in args.index.rglob("*") if p.is_file()) / (
            1024 * 1024
        )
        print(
            "\n" +
            json.dumps(
                {
                    "status": "ok",
                    "build_time_ms": build_ms,
                    "index_size_mb": size_mb,
                    "peak_rss_mb": peak_rss_mb(),
                }
            ),
            flush=True,
        )
        return 0

    query_tsv = args.tsv_cache / (args.query.stem + ".tsv")
    fvecs_to_tsv(args.query, query_tsv)
    gt = read_ivecs(args.gt)
    nq = len(gt)
    outputs = []
    for p_range in args.p_ranges.split(","):
        proc = run(
            [
                QBG_BIN,
                "search-qg",
                "-i", "g",
                "-n", str(args.k),
                "-e", "0.1",
                "-p", p_range,
                "-o", "e",
                str(args.index),
                str(query_tsv),
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"qbg search-qg failed rc={proc.returncode} p_range={p_range}; tail:\n{(proc.stdout or '')[-3000:]}"
            )
        outputs.append(proc.stdout)
    parsed: dict[tuple, dict] = {}
    for text in outputs:
        for key, data in parse_search_output(text).items():
            parsed.setdefault(key, {"ids": [], "times": []})
            parsed[key]["ids"].extend(data["ids"])
            parsed[key]["times"].extend(data["times"])

    for (eps, p), data in parsed.items():
        ids = data["ids"]
        times_ms = data["times"]
        if len(ids) < nq * args.k or len(times_ms) < nq:
            # partial output: only report complete data
            continue
        id_rows = [
            ids[i * args.k : (i + 1) * args.k] for i in range(nq)
        ]
        rec = 0.0
        for truth, row in zip(gt, id_rows):
            s = set(int(x) for x in truth[: args.k])
            rec += sum(1 for x in row[: args.k] if int(x) in s)
        rec /= nq * args.k
        arr_ms = np.asarray(times_ms, dtype=np.float64)
        print(
            "\n" +
            json.dumps(
                {
                    "status": "ok",
                    "search_param": f"p={float(p):.4g}",
                    "epsilon": float(eps),
                    "result_expansion": float(p),
                    "recall": rec,
                    "qps": nq * 1000.0 / arr_ms.sum() if arr_ms.sum() > 0 else 0.0,
                    "latency_mean_us": float(arr_ms.mean() * 1000.0),
                    "latency_p50_us": float(np.percentile(arr_ms, 50) * 1000.0),
                    "latency_p95_us": float(np.percentile(arr_ms, 95) * 1000.0),
                    "query_count": nq,
                    "peak_rss_mb": peak_rss_mb(),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
