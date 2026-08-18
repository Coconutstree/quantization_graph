"""Worker script for the SymphonyQG adapter (official python binding)."""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, "/tmp/baseline_python")
import numpy as np  # noqa: E402
from symphonyqg.symphonyqg import Index  # noqa: E402


def read_fvecs(path: Path):
    with path.open("rb") as f:
        dim = int(np.fromfile(f, dtype=np.int32, count=1)[0])
        raw = np.fromfile(f, dtype=np.float32)
    n = (raw.size + 1) // (dim + 1)
    mask = np.ones(raw.size, dtype=bool)
    if n > 1:
        mask[np.arange(1, n) * (dim + 1) - 1] = False
    return raw[mask].reshape(n, dim)


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
    return np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.int64)


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["build", "search"], required=True)
    ap.add_argument("--base", type=Path)
    ap.add_argument("--query", type=Path)
    ap.add_argument("--gt", type=Path)
    ap.add_argument("--index", type=Path)
    ap.add_argument("--R", type=int, default=32)
    ap.add_argument("--EF", type=int, default=400)
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--ef-list", type=str, default="")
    ap.add_argument("--num-elements", type=int, default=0)
    args = ap.parse_args()

    if args.mode == "build":
        t0 = time.perf_counter()
        data = read_fvecs(args.base)
        n, d = data.shape
        idx = Index("QG", "L2", num_elements=n, dimension=d, degree_bound=args.R)
        idx.build_index(data, args.EF, num_iter=args.iters, num_thread=args.threads)
        args.index.parent.mkdir(parents=True, exist_ok=True)
        idx.save(str(args.index))
        build_ms = (time.perf_counter() - t0) * 1000.0
        print(
            "\n" +
            json.dumps(
                {
                    "status": "ok",
                    "build_time_ms": build_ms,
                    "index_size_mb": (
                        args.index.stat().st_size / (1024 * 1024)
                        if args.index.exists()
                        else 0.0
                    ),
                    "peak_rss_mb": peak_rss_mb(),
                    "base_count": n,
                    "dim": d,
                }
            ),
            flush=True,
        )
        return 0

    queries = read_fvecs(args.query)
    gt = read_ivecs(args.gt)
    n, d = queries.shape
    idx = Index(
        "QG",
        "L2",
        num_elements=args.num_elements,
        dimension=d,
        degree_bound=args.R,
    )
    idx.load(str(args.index))
    ef_list = [int(x) for x in args.ef_list.split(",") if x]
    for ef in ef_list:
        idx.set_ef(ef)
        times_us = []
        ids = []
        visited = []
        dist_calls = []
        for q in queries:
            t0 = time.perf_counter()
            r = idx.search(q, args.k)
            times_us.append((time.perf_counter() - t0) * 1e6)
            ids.append(np.asarray(r).reshape(-1))
            visited.append(idx.last_visited())
            dist_calls.append(idx.last_distance())
        rec = 0.0
        for truth, row in zip(gt, ids):
            s = set(int(x) for x in truth[: args.k])
            rec += sum(1 for x in row[: args.k] if int(x) in s)
        rec /= len(ids) * args.k
        arr = np.asarray(times_us)
        print(
            "\n" +
            json.dumps(
                {
                    "status": "ok",
                    "search_param": f"ef={ef}",
                    "ef": ef,
                    "recall": rec,
                    "qps": len(times_us) * 1e6 / arr.sum() if arr.sum() > 0 else 0.0,
                    "latency_mean_us": float(arr.mean()),
                    "latency_p50_us": float(np.percentile(arr, 50)),
                    "latency_p95_us": float(np.percentile(arr, 95)),
                    "visited_per_query": float(np.mean(visited)) if visited else 0.0,
                    "distance_calls_per_query": float(np.mean(dist_calls)) if dist_calls else 0.0,
                    "query_count": len(times_us),
                    "peak_rss_mb": peak_rss_mb(),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
