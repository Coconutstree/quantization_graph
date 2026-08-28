#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path


REPO = Path("/home/msy2025/quantization_graph")
RUN_ROOT = (
    REPO
    / "results/disk_environment/.formal_runs/runs/formal_diskenv_20260826_114755_bc_agnews"
    / "05C_disk_system_fair/agnews/artifacts/test"
)
BASE = "OG-LVQ-DiskPort__hybrid_disk__B2__standard__w32__r0"
WIDTHS = list(range(10, 31)) + list(range(40, 101, 10)) + list(range(140, 581, 40))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    json_paths = [RUN_ROOT / f"{BASE}.json.w{w}.tmp" for w in WIDTHS]
    trace_paths = [RUN_ROOT / f"{BASE}.queries.jsonl.w{w}.tmp" for w in WIDTHS]
    missing = [p for p in json_paths + trace_paths if not p.exists()]
    if missing:
        for path in missing:
            print(f"missing {path}")
        return 2

    rows = []
    final_doc = None
    for path in json_paths:
        doc = json.loads(path.read_text())
        if final_doc is None:
            final_doc = doc
        got = doc.get("summary_rows", [])
        if len(got) != 1:
            raise SystemExit(f"bad summary_rows in {path}: {len(got)}")
        rows.extend(got)

    final_trace = RUN_ROOT / f"{BASE}.queries.jsonl"
    with final_trace.open("wb") as out:
        for path in trace_paths:
            data = path.read_bytes()
            out.write(data)
            if data and not data.endswith(b"\n"):
                out.write(b"\n")

    native = REPO / "build/formal_local/05_disk_system_fair/qgraph05_og_lvq_disk_port"
    final_doc["summary_rows"] = rows
    final_doc["query_trace_path"] = str(final_trace.resolve())
    final_doc["query_trace_sha256"] = sha256(final_trace)
    final_doc["native_binary_sha256"] = sha256(native)
    (RUN_ROOT / f"{BASE}.json").write_text(json.dumps(final_doc, indent=2) + "\n")

    recalls = [float(row["recall"]) for row in rows]
    qps = [float(row["qps"]) for row in rows]
    print(
        f"merged OG-LVQ rows={len(rows)} "
        f"recall={min(recalls):.6f}..{max(recalls):.6f} "
        f"qps={min(qps):.6f}..{max(qps):.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
