"""Worker script for the Glass-NSG adapter (official pyglass)."""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import glass
import numpy as np


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
    ap.add_argument("--graph", type=Path)
    ap.add_argument("--R", type=int, default=32)
    ap.add_argument("--L", type=int, default=50)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--ef-list", type=str, default="")
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--optimize", action="store_true",
                    help="call Searcher.optimize() so get_last_search_dist_cmps is populated")
    ap.add_argument("--batch", action="store_true",
                    help="use batch_search so get_last_search_dist_cmps reflects this search")
    args = ap.parse_args()

    if args.mode == "build":
        glass.set_num_threads(args.threads)
        t0 = time.perf_counter()
        data = read_fvecs(args.base)
        idx = glass.Index(index_type="NSG", metric="L2", R=args.R, L=args.L)
        graph = idx.build(data)
        args.graph.parent.mkdir(parents=True, exist_ok=True)
        graph.save(str(args.graph))
        build_ms = (time.perf_counter() - t0) * 1000.0
        print(
            "\n" +
            json.dumps(
                {
                    "status": "ok",
                    "build_time_ms": build_ms,
                    "index_size_mb": (
                        args.graph.stat().st_size / (1024 * 1024)
                        if args.graph.exists()
                        else 0.0
                    ),
                    "peak_rss_mb": peak_rss_mb(),
                    "base_count": data.shape[0],
                    "dim": data.shape[1],
                }
            ),
            flush=True,
        )
        return 0

    base = read_fvecs(args.base)
    queries = read_fvecs(args.query)
    gt = read_ivecs(args.gt)
    glass.set_num_threads(args.threads)
    graph = glass.Graph()
    graph.load(str(args.graph))
    searcher = glass.Searcher(graph=graph, data=base, metric="L2", quantizer="SQ4U")
    if args.optimize:
        searcher.optimize()
    total_cmps = 0
    ef_list = [int(x) for x in args.ef_list.split(",") if x]
    for ef in ef_list:
        searcher.set_ef(ef)
        if args.batch:
            t0 = time.perf_counter()
            ids_arr, _ = searcher.batch_search(queries, args.k, args.threads)
            elapsed = time.perf_counter() - t0
            ids = [np.asarray(row).reshape(-1) for row in ids_arr]
            dist_cmps = searcher.get_last_search_dist_cmps()
            visited = searcher.get_last_search_visited()
            times_us = [elapsed / len(queries) * 1e6] * len(queries)
        else:
            times_us = []
            ids = []
            dist_cmps = 0.0
            visited = 0.0
            for q in queries:
                t0 = time.perf_counter()
                r = searcher.search(query=q, k=args.k)
                times_us.append((time.perf_counter() - t0) * 1e6)
                ids.append(np.asarray(r[0]).reshape(-1))
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
                    "distance_cmps_per_query": float(dist_cmps),
                    "visited_per_query": float(visited),
                    "query_count": len(times_us),
                    "peak_rss_mb": peak_rss_mb(),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
