"""Diagnostic replay of existing indexes; never exports a missing index."""
import argparse
import hashlib
import json
import subprocess
import sys
import struct
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METHODS = ["Ours-Disk", "SymphonyQG-DiskPort", "OG-LVQ-DiskPort",
           "Glass-NSG-DiskPort", "DiskANN-PQ-Disk"]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="agnews")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--widths", default="64,256")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--validation", action="store_true")
    parser.add_argument("--split", choices=("validation", "test"))
    parser.add_argument("--binary-root", type=Path)
    parser.add_argument("--cpp-binary-root", type=Path)
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--warmup-queries", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=args.resume)
    query_flags = {}
    split = args.split or ("validation" if args.validation else None)
    if split:
        sys.path.insert(0, str(ROOT))
        common = importlib.import_module("src.disk_bench.common")
        prepare_query_splits = common.prepare_query_splits
        vecs_count = common.vecs_count
        random_query_order = common.random_query_order
        splits = prepare_query_splits(args.dataset, ROOT / "data", ROOT / "results/archive/legacy_layout_20260918/disk_environment")
        query = splits[f"{split}_query"]
        gt = splits[f"{split}_gt"]
        count = vecs_count(query)
        if count <= 0 or vecs_count(gt) != count:
            raise RuntimeError("invalid query/groundtruth counts")
        order = out / "order.u32"
        order.write_bytes(struct.pack(f"<{count}I", *map(int, random_query_order(count, 20260813))))
        pair = hashlib.sha256()
        for path in (query, gt):
            pair.update(path.name.encode())
            pair.update(bytes.fromhex(digest(path)))
        query_flags = {"--query": str(query), "--groundtruth": str(gt),
                       "--query-order": str(order), "--query-order-sha256": digest(order),
                       "--query-split-sha256": pair.hexdigest()}
    (out / "diagnostic.json").write_text(json.dumps({
        "formal": False, "dataset": args.dataset, "workers": args.workers,
        "git_commit": git_commit,
        "split": split or "four_query_smoke", "warmup_queries": args.warmup_queries,
        "widths": args.widths, "query_flags": query_flags,
        "reason": "query workspace memory accounting is incomplete",
    }, indent=2) + "\n")
    report = json.loads((out / "report.json").read_text()) if args.resume and (out / "report.json").exists() else []
    selected_methods = args.methods.split(",")
    if any(method not in METHODS for method in selected_methods):
        raise ValueError("unknown method")
    for method in selected_methods:
        if any(r["method"] == method and r.get("index_unchanged") for r in report):
            artifact = json.loads((out / method / "result.json").read_text())
            assert artifact["status"] == "done" and artifact["workers"] == args.workers
            assert artifact["phase"] == split and artifact["warmup_queries"] == args.warmup_queries
            print(f"Resume: keeping completed {method}", flush=True)
            continue
        source = ROOT / "artifacts/graphs/reader_audit" / args.dataset / method
        command = json.loads((source / "command.json").read_text())
        if args.binary_root:
            command[0] = str(args.binary_root.resolve() / Path(command[0]).name)
        if args.cpp_binary_root and method not in ("Ours-Disk", "DiskANN-PQ-Disk"):
            command[0] = str(args.cpp_binary_root.resolve() / Path(command[0]).name)
        flags = dict(zip(command[1::2], command[2::2]))
        index = Path(flags["--disk-index-dir"])
        if not index.is_dir():
            raise RuntimeError(f"missing index: {index}")
        meta_path = index / ("index.meta.json" if method == "DiskANN-PQ-Disk" else "index.meta")
        if not meta_path.is_file():
            raise RuntimeError(f"missing completion metadata: {meta_path}")
        if method in ("Glass-NSG-DiskPort", "SymphonyQG-DiskPort"):
            meta = dict(line.split("=", 1) for line in meta_path.read_text().splitlines() if "=" in line)
            for field in ("implementation_fingerprint", "input_manifest_sha256"):
                if meta[field] != flags["--" + field.replace("_", "-")]:
                    raise RuntimeError(f"{method}: reuse guard mismatch")
        before = snapshot(index)
        dest = out / method
        if args.resume and dest.exists():
            import time
            dest.rename(out / (method + f".failed_before_resume_{time.time_ns()}"))
        dest.mkdir()
        requested = [int(w) for w in args.widths.split(",")]
        supported = [w for w in requested if method not in ("Ours-Disk", "DiskANN-PQ-Disk") or w >= 10]
        (dest / "width_support.json").write_text(json.dumps({
            "requested": requested, "supported": supported,
            "unsupported": [w for w in requested if w not in supported],
            "reason": "Ours/DiskANN require L >= top-k (10); widths are not clamped" if method in ("Ours-Disk", "DiskANN-PQ-Disk") else "All requested widths supported",
        }, indent=2) + "\n")
        if not supported:
            raise ValueError("No supported search widths")
        flags.update({
            "--result-json": str(dest / "result.json"),
            "--query-trace": str(dest / "queries.jsonl"),
            "--cache-mode": "c0", "--workers": str(args.workers),
            "--warmup-queries": str(args.warmup_queries),
            "--integration-widths": ",".join(map(str, supported)),
            "--run-id": "native_integration_query_cache",
            "--native-binary-sha256": digest(Path(command[0])),
            "--git-commit": git_commit,
        })
        flags.update(query_flags)
        if split:
            flags["--phase"] = split
        if method in ("Ours-Disk", "DiskANN-PQ-Disk"):
            flags["--integration-beams"] = "1"
        command = [command[0]] + [value for pair in flags.items() for value in pair]
        (dest / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (dest / "terminal.log").open("w") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
        if snapshot(index) != before:
            raise RuntimeError(f"index changed during diagnostic: {index}")
        if result.returncode:
            raise RuntimeError(f"{method} exited {result.returncode}; see {dest / 'terminal.log'}")
        artifact = json.loads((dest / "result.json").read_text())
        traces = [json.loads(line) for line in (dest / "queries.jsonl").read_text().splitlines()]
        assert traces, method
        for row in traces:
            assert 0 < row["query_cache_allocated_bytes"] <= 4 * 1024 * 1024, row
            assert row["query_cache_hits"] >= 0 and row["query_cache_misses"] > 0, row
            assert row["bytes_read"] > 0 and row["io_requests"] > 0, row
        item = {"method": method, "diagnostic_only": True, "index_unchanged": True,
                "query_rows": len(traces), "summary_rows": artifact["summary_rows"]}
        report.append(item)
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"method": method, "query_rows": len(traces), "status": "diagnostic_pass"}), flush=True)


if __name__ == "__main__":
    main()
