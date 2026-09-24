"""Resume width checkpoints without claiming missing process-level evidence."""
import json
import shutil
from pathlib import Path

from measure_05_process import digest


def flags(command):
    ignored = {"--result-json", "--query-trace", "--run-id", "--integration-widths"}
    return {k: v for k, v in zip(command[1::2], command[2::2]) if k not in ignored}


def checkpoint(path, trace, command, width, count):
    data = json.loads(path.read_text())
    f = flags(command)
    for field, flag in {"native_binary_sha256": "--native-binary-sha256",
                        "query_split_sha256": "--query-split-sha256",
                        "query_order_sha256": "--query-order-sha256",
                        "dataset": "--dataset", "method": "--method"}.items():
        if data.get(field) != f[flag]:
            raise ValueError(f"checkpoint provenance mismatch: {path}: {field}")
    rows = data.get("summary_rows", [])
    if (data.get("status") != "done" or not data.get("direct_io")
            or data.get("cache_mode") != "c0" or data.get("workers") != 32
            or len(rows) != 1 or rows[0]["search_width"] != width
            or rows[0]["query_count"] != count or digest(trace) != data["query_trace_sha256"]):
        raise ValueError(f"invalid checkpoint: {path}")
    ids = set()
    with trace.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["search_width"] != width or row["query_id"] in ids or len(row["result_ids"]) != 10:
                raise ValueError(f"invalid trace: {trace}")
            ids.add(row["query_id"])
    if ids != set(range(count)):
        raise ValueError(f"incomplete trace: {trace}")
    return data


def resume_widths(command, folder, widths, count, configured, disk_measure, write, progress):
    folder = Path(folder)
    original = folder / "command.json"
    if original.exists():
        previous = json.loads(original.read_text())
        if previous[0] != command[0] or flags(previous) != flags(command):
            raise ValueError("resume command differs; refusing to mix measurements")
    else:
        write(original, command)
    parts = []
    for width in widths:
        part = folder / "_widths" / str(width)
        result, trace = part / "result.json", part / "queries.jsonl"
        legacy = False
        if not result.exists():
            old_result = folder / f"result.json.w{width}.tmp"
            old_trace = folder / f"queries.jsonl.w{width}.tmp"
            if old_result.exists() and old_trace.exists():
                result, trace, legacy = old_result, old_trace, True
            else:
                progress(width, "disk_measurement")
                disk_measure(configured(command, part, [width]), part)
        data = checkpoint(result, trace, command, width, count)
        memory_path = part / "memory_measurement.json"
        memory = json.loads(memory_path.read_text()) if not legacy and memory_path.exists() else {}
        parts.append((data, trace, {"width": width, "result": str(result),
                     "result_sha256": digest(result), "trace_sha256": digest(trace),
                     "memory_evidence": str(memory_path) if memory else None,
                     "memory_passed": bool(memory.get("user_address_space_budget_passed")),
                     "legacy_checkpoint": legacy}))
        write(folder / "width_resume.json", {"completed_widths": [p[2]["width"] for p in parts],
                                              "parts": [p[2] for p in parts], "formal_ready": False})
    merged_trace = folder / "queries.jsonl"
    temporary = folder / "queries.jsonl.new"
    with temporary.open("wb") as out:
        for _, trace, _ in parts:
            with trace.open("rb") as stream:
                shutil.copyfileobj(stream, out)
    temporary.replace(merged_trace)
    merged = dict(parts[0][0])
    merged.update(summary_rows=[p[0]["summary_rows"][0] for p in parts],
                  query_trace_path=str(merged_trace), query_trace_sha256=digest(merged_trace),
                  peak_rss_bytes=max(p[0].get("peak_rss_bytes", 0) for p in parts), formal_ready=False)
    write(folder / "memory_measurement.json", {
        "user_address_space_budget_passed": all(p[2]["memory_passed"] for p in parts),
        "scope": "per_width_processes_with_legacy_evidence_gaps",
        "missing_evidence_widths": [p[2]["width"] for p in parts if not p[2]["memory_passed"]],
        "formal_ready": False})
    write(folder / "result.json", merged)
