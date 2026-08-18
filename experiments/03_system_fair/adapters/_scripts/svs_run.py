"""Worker script for the OG-LVQ adapter (official Intel SVS python bindings)."""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import svs


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def read_fvecs(path):
    import numpy as np

    with path.open("rb") as f:
        dim = int(np.fromfile(f, dtype=np.int32, count=1)[0])
        raw = np.fromfile(f, dtype=np.float32)
    n = (raw.size + 1) // (dim + 1)
    mask = np.ones(raw.size, dtype=bool)
    if n > 1:
        mask[np.arange(1, n) * (dim + 1) - 1] = False
    return raw[mask].reshape(n, dim)


def read_ivecs(path: Path):
    import numpy as np

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["build", "search"], required=True)
    ap.add_argument("--base", type=Path)
    ap.add_argument("--query", type=Path)
    ap.add_argument("--gt", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--R", type=int, default=32)
    ap.add_argument("--W", type=int, default=400)
    ap.add_argument("--primary", type=int, default=4)
    ap.add_argument("--residual", type=int, default=8)
    ap.add_argument("--alpha", type=float, default=1.2)
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--window-list", type=str, default="")
    args = ap.parse_args()

    if args.mode == "build":
        t0 = time.perf_counter()
        loader = svs.VectorDataLoader(str(args.base), svs.DataType.float32)
        lvq = svs.LVQLoader(loader, primary=args.primary, residual=args.residual)
        params = svs.VamanaBuildParameters(
            graph_max_degree=args.R, window_size=args.W, alpha=args.alpha
        )
        index = svs.Vamana.build(params, lvq, svs.DistanceType.L2, num_threads=args.threads)
        args.out.mkdir(parents=True, exist_ok=True)
        index.save(
            str(args.out / "config"),
            str(args.out / "graph"),
            str(args.out / "data"),
        )
        build_ms = (time.perf_counter() - t0) * 1000.0
        size_mb = sum(
            p.stat().st_size for p in args.out.rglob("*") if p.is_file()
        ) / (1024 * 1024)
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

    import numpy as np

    queries = svs.read_vecs(str(args.query))
    gt = read_ivecs(args.gt)
    index = svs.Vamana(
        str(args.out / "config"),
        str(args.out / "graph"),
        str(args.out / "data"),
        svs.DistanceType.L2,
        num_threads=args.threads,
    )
    index.num_threads = args.threads
    window_list = [int(x) for x in args.window_list.split(",") if x]
    for window in window_list:
        index.search_window_size = window
        times_us = []
        ids = []
        for q in queries:
            t0 = time.perf_counter()
            I, _ = index.search(q, args.k)
            times_us.append((time.perf_counter() - t0) * 1e6)
            ids.append(np.asarray(I).reshape(-1))
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
                    "search_param": f"W={window}",
                    "window": window,
                    "recall": rec,
                    "qps": len(times_us) * 1e6 / arr.sum() if arr.sum() > 0 else 0.0,
                    "latency_mean_us": float(arr.mean()),
                    "latency_p50_us": float(np.percentile(arr, 50)),
                    "latency_p95_us": float(np.percentile(arr, 95)),
                    "query_count": len(times_us),
                    "peak_rss_mb": peak_rss_mb(),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
