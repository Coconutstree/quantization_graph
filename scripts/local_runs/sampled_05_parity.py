"""Small deterministic correctness preflight, separate from performance data."""
import json
from pathlib import Path
import struct

from measure_05_process import compare_traces, digest, measure


def extract_records(source, target, ids):
    with Path(source).open("rb") as stream:
        header = stream.read(4)
        if len(header) != 4:
            raise ValueError("missing vecs header")
        dimension = struct.unpack("<I", header)[0]
        size = 4 * (dimension + 1)
        if not dimension or Path(source).stat().st_size % size:
            raise ValueError("invalid vecs geometry")
        with target.open("xb") as output:
            for query_id in ids:
                stream.seek(query_id * size)
                record = stream.read(size)
                if len(record) != size or record[:4] != header:
                    raise ValueError("invalid selected vecs record")
                output.write(record)


def sample_ids(count, limit=32):
    if count <= 0:
        raise ValueError("empty query set")
    n = min(count, limit)
    return [i * count // n for i in range(n)]


def run_preflight(command, folder, count, configured, set_arg):
    folder.mkdir(parents=True, exist_ok=False)
    ids = sample_ids(count)
    query = folder / "sample_query.fvecs"
    gt = folder / "sample_gt.ivecs"
    extract_records(command[command.index("--query") + 1], query, ids)
    extract_records(command[command.index("--groundtruth") + 1], gt, ids)
    order = folder / "order.u32"
    order.write_bytes(struct.pack(f"<{len(ids)}I", *range(len(ids))))
    import hashlib
    pair = hashlib.sha256()
    for path in (query, gt):
        pair.update(path.name.encode())
        pair.update(bytes.fromhex(digest(path)))
    method = command[command.index("--method") + 1]
    rust = method in {"Ours-Disk", "DiskANN-PQ-Disk"}
    widths = [10, 100, 580] if rust else [1, 100, 580]
    base = command.copy()
    for key, value in {"--query": query, "--groundtruth": gt,
                       "--query-order": order, "--query-order-sha256": digest(order),
                       "--query-split-sha256": pair.hexdigest()}.items():
        set_arg(base, key, value)
    sample = configured(base, folder / "disk", widths, reference=rust)
    set_arg(sample, "--warmup-queries", 0)
    # Rust's inline path compares disk and memory only for these 96 queries.
    measure(sample, folder / "disk", budget=None if rust else 2 << 30)
    if rust:
        native_path = folder / "disk/result.parity.json"
        parity = json.loads(native_path.read_text())
        passed = (parity.get("query_comparisons") == len(ids) * len(widths)
                  and parity.get("max_recall_delta", 1) <= .001
                  and parity.get("mean_top10_overlap", 0) >= .99
                  and parity.get("mean_visited_count_relative_delta", 1) <= .01
                  and parity.get("mean_distance_count_relative_delta", 1) <= .01)
        parity.update(passed=passed, reference_artifact_sha256=digest(native_path))
    else:
        reference = configured(base, folder / "memory", widths, reference=True)
        set_arg(reference, "--warmup-queries", 0)
        measure(reference, folder / "memory", reference=True)
        parity = compare_traces(folder / "disk/queries.jsonl", folder / "memory/queries.jsonl")
        parity["passed"] &= parity["query_comparisons"] == len(ids) * len(widths)
    parity.update(scope="sampled_preflight_not_full_query_parity", source_query_ids=ids,
                  sample_queries=len(ids), sample_widths=widths,
                  native_binary_sha256=digest(command[0]), formal_ready=False)
    (folder / "parity.json").write_text(json.dumps(parity, indent=2) + "\n")
    if not parity["passed"]:
        raise RuntimeError(f"sampled parity failed: {folder}")
    return parity
