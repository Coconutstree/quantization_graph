"""Repair interrupted timings, then serialize optimized dataset measurements."""
import datetime
import argparse
import fcntl
import importlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from export_ours_locality_layout import export as export_layout
from measure_05_process import compare_traces, digest, measure
from run_agnews_acceptance import ROOT, BINARIES, active_searches, input_snapshot, set_arg
from sampled_05_parity import run_preflight
from resume_05_widths import resume_widths

OUT = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/test_L_400_w_32"
ORIGINAL = OUT / "_runtime/agnews_acceptance_20260912"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
STATE = OUT / "optimized_queue_state.json"
METHODS = list(BINARIES)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".new")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def configured(command, folder, widths, reference=False):
    result = command.copy()
    for key, value in {"--result-json": folder / "result.json", "--query-trace": folder / "queries.jsonl",
                       "--integration-widths": ",".join(map(str, widths)), "--workers": 32,
                       "--warmup-queries": 100, "--cache-mode": "c0", "--repeat-id": 0,
                       "--run-id": "native_integration_optimized_queue",
                       "--native-binary-sha256": digest(command[0])}.items():
        set_arg(result, key, value)
    method = result[result.index("--method") + 1]
    if method in {"Ours-Disk", "DiskANN-PQ-Disk"}:
        set_arg(result, "--parity-mode", "inline" if reference else "external")
    return result


def disk_measure(command, folder):
    if active_searches():
        raise RuntimeError(f"refusing overlapping search processes: {active_searches()}")
    before = input_snapshot(command)
    memory = measure(command, folder, budget=2 << 30)
    if not memory["user_address_space_budget_passed"] or before != input_snapshot(command):
        raise RuntimeError("memory budget or input immutability check failed")
    artifact = json.loads((folder / "result.json").read_text())
    if not artifact.get("direct_io") or artifact.get("cache_mode") != "c0":
        raise RuntimeError("not a direct-I/O C0 measurement")
    write(folder / "storage_protocol.json", {"protocol": "fixed_query_warmup_v1",
          "warmup_queries": 100, "workers": 32, "cache_mode": "c0",
          "input_snapshot": before, "no_other_native_search_at_start": True,
          "device_cache_controlled": False, "cold_storage_claim": False})
    return artifact


def acceptance(dataset):
    # These outstanding requirements cannot be manufactured by changing a flag.
    write(OUT / dataset / "formal_acceptance.json", {
        "formal_ready": False, "status": "requires_acceptance",
        "remaining": ["complete workspace category accounting or reviewed conservative-bound policy",
                      "cross-binary timing-scope acceptance",
                      "acceptance of documented warm-storage protocol; controller cache is uncontrolled"],
        "note": "Measured parity and a 2GiB process address-space bound are evidence, not blanket formal approval."})


def draw(root):
    for script in ("export_05c_pending_figures.py", "plot_05c_recall_qps.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts/local_runs" / script), str(root)], check=True)


def repair_agnews():
    method = "DiskANN-PQ-Disk"
    dest = OUT / "agnews" / method
    folder = OUT / "agnews/_repairs" / STAMP
    original = json.loads((dest / "result.json").read_text())
    command = configured(json.loads((dest / "command.json").read_text()), folder, [540, 580])
    replacement = disk_measure(command, folder)
    reference = folder / "reference_subset.jsonl"
    with reference.open("w") as sink, (OUT / "agnews/_references" / method / "queries.jsonl").open() as source:
        for line in source:
            if json.loads(line)["search_width"] in {540, 580}:
                sink.write(line)
    parity = compare_traces(folder / "queries.jsonl", reference)
    write(folder / "measured_parity.json", parity)
    if not parity["passed"] or parity["query_comparisons"] != 1600:
        raise RuntimeError("repair parity failed")
    if {r["search_width"] for r in replacement["summary_rows"]} != {540, 580}:
        raise RuntimeError("repair width mismatch")
    archive = OUT / "agnews/_history" / ("before_timing_repair_" + STAMP)
    archive.mkdir(parents=True)
    for name in ("result.json", "queries.jsonl", "command.json"):
        shutil.copy2(dest / name, archive / name)
    replacement_rows = {r["search_width"]: r for r in replacement["summary_rows"]}
    original["summary_rows"] = [replacement_rows.get(r["search_width"], r) for r in original["summary_rows"]]
    temporary = dest / "queries.repaired.jsonl"
    with temporary.open("w") as sink:
        with (archive / "queries.jsonl").open() as source:
            for line in source:
                if json.loads(line)["search_width"] not in replacement_rows:
                    sink.write(line)
        with (folder / "queries.jsonl").open() as source:
            shutil.copyfileobj(source, sink)
    temporary.replace(dest / "queries.jsonl")
    original.update(query_trace_path=str(dest / "queries.jsonl"),
                    query_trace_sha256=digest(dest / "queries.jsonl"), formal_ready=False,
                    timing_repair_manifest=str(folder / "promotion.json"))
    write(folder / "promotion.json", {"replaced_widths": [540, 580], "reason": "migration interference",
          "previous_result_sha256": digest(archive / "result.json"),
          "replacement_result_sha256": digest(folder / "result.json"),
          "replacement_command_sha256": digest(folder / "command.json"),
          "parity_passed": True, "formal_ready": False})
    write(dest / "result.json", original)
    acceptance("agnews")
    draw(ORIGINAL)


def dataset_command(dataset, method, binary, order, query, gt, split_hash):
    path = ROOT / "artifacts/graphs/reader_audit" / dataset / method / "command.json"
    command = json.loads(path.read_text())
    command[0] = str(binary)
    for key, value in {"--phase": "test", "--query": query, "--groundtruth": gt,
                       "--query-order": order, "--query-order-sha256": digest(order),
                       "--query-split-sha256": split_hash}.items():
        set_arg(command, key, value)
    return command


def run_dataset(dataset, state, run):
    import hashlib
    sys.path.insert(0, str(ROOT))
    common = importlib.import_module("src.disk_bench.common")
    splits = common.prepare_query_splits(dataset, ROOT / "data", ROOT / "results/archive/legacy_layout_20260918/disk_environment")
    query, gt = splits["test_query"], splits["test_gt"]
    count = common.vecs_count(query)
    if count <= 0 or common.vecs_count(gt) != count:
        raise RuntimeError("empty or inconsistent query split")
    dest = OUT / dataset
    dest.mkdir(exist_ok=True)
    order = dest / "optimized_order.u32"
    order.write_bytes(struct.pack(f"<{count}I", *map(int, common.random_query_order(count, 20260813))))
    pair = hashlib.sha256()
    for path in (query, gt):
        pair.update(path.name.encode())
        pair.update(bytes.fromhex(digest(path)))
    layout = ROOT / "work/05_disk_system_fair/experimental_layouts" / (dataset + "_bfs_split_" + STAMP)
    ours = dataset_command(dataset, "Ours-Disk", ORIGINAL / "binaries" / BINARIES["Ours-Disk"].name,
                           order, query, gt, pair.hexdigest())
    source = Path(ours[ours.index("--disk-index-dir") + 1])
    state.update(dataset=dataset, phase="exporting_verified_locality")
    write(STATE, state)
    saved_command = dest / "Ours-Disk/command.json"
    if saved_command.exists():
        previous = json.loads(saved_command.read_text())
        if "--locality-layout-dir" in previous:
            layout = Path(previous[previous.index("--locality-layout-dir") + 1])
    if not layout.exists():
        export_layout(source, layout)
    manifest = json.loads((layout / "manifest.json").read_text())
    for path, expected_hash in manifest["source_hashes"].items():
        if digest(path) != expected_hash:
            raise RuntimeError("layout source changed since full byte verification")
    for name, info in manifest["files"].items():
        if digest(layout / name) != info["sha256"]:
            raise RuntimeError("verified layout file changed")
    if not manifest["all_records_verified"] or manifest["graph_bytes"] + manifest["compact_bytes"] > 4096 or manifest["residual_bytes"] > 4096:
        raise RuntimeError("unsupported or unverified locality geometry")
    for method in METHODS:
        state.update(method=method, phase="disk_measurement")
        write(STATE, state)
        binary = ORIGINAL / "binaries" / BINARIES[method].name
        expected = json.loads((ORIGINAL / "state.json").read_text())["methods"][method]["binary_sha256"]
        if digest(binary) != expected:
            raise RuntimeError("pinned binary changed")
        command = dataset_command(dataset, method, binary, order, query, gt, pair.hexdigest())
        if method == "Ours-Disk":
            for flag, value in {"--locality-layout-dir": layout,
                "--locality-combined-sha256": manifest["files"]["graph_compact.pages"]["sha256"],
                "--locality-residual-sha256": manifest["files"]["residual.pages"]["sha256"],
                "--locality-mapping-sha256": manifest["files"]["id_to_slot.u32"]["sha256"]}.items():
                set_arg(command, flag, value)
        widths = [w for w in run["widths"] if w >= 10 or method not in {"Ours-Disk", "DiskANN-PQ-Disk"}]
        folder = dest / method
        direct = configured(command, folder, widths)
        state["phase"] = "sampled_preflight"
        write(STATE, state)
        parity = run_preflight(command, dest / "_preflight" / STAMP / method,
                               count, configured, set_arg)
        if (folder / "result.json").exists():
            # Keep complete disk measurements only when provenance and geometry match.
            previous = json.loads((folder / "result.json").read_text())
            old_command = json.loads((folder / "command.json").read_text())
            ignored = {"--result-json", "--query-trace", "--run-id"}
            old_flags = dict(zip(old_command[1::2], old_command[2::2]))
            new_flags = dict(zip(direct[1::2], direct[2::2]))
            if {k:v for k,v in old_flags.items() if k not in ignored} != {k:v for k,v in new_flags.items() if k not in ignored}:
                raise RuntimeError("completed result config differs; refusing silent reuse or overwrite")
            memory = json.loads((folder / "memory_measurement.json").read_text())
            if ((not memory.get("user_address_space_budget_passed") and not (folder / "width_resume.json").exists()) or previous.get("status") != "done"
                    or previous.get("native_binary_sha256") != expected
                    or previous.get("query_trace_sha256") != digest(folder / "queries.jsonl")
                    or sorted(r["search_width"] for r in previous["summary_rows"]) != sorted(widths)
                    or any(r["query_count"] != count for r in previous["summary_rows"])):
                raise RuntimeError("completed disk measurement failed reuse checks")
            state["phase"] = "reused_completed_disk_measurement"
        else:
            def progress(width, phase):
                state.update(phase=phase, current_width=width)
                write(STATE, state)
            resume_widths(direct, folder, widths, count, configured, disk_measure, write, progress)
        write(STATE, state)
        write(folder / "measured_parity.json", parity)
        if not parity["passed"]:
            raise RuntimeError("actual query parity failed")
        if method == "Ours-Disk":
            write(folder / "layout_revision.json", {"layout": str(layout), "all_records_verified": True,
                                                      "query_parity_passed": True, "formal_ready": False})
    acceptance(dataset)
    draw(OUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    options = parser.parse_args()
    if (OUT / "ALGORITHM_AUDIT_BLOCKED.json").exists():
        raise RuntimeError("algorithm audit unresolved; refusing to resume pinned legacy binaries")
    lock = (OUT / "optimized_queue.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if active_searches():
        raise RuntimeError("stop legacy queries before starting optimized queue")
    run = json.loads((ORIGINAL / "run.json").read_text())
    run["datasets"] = ["agnews", "gist", "dbpedia", "sift10m"]
    write(OUT / "run.json", run)
    previous = json.loads(STATE.read_text()) if options.resume and STATE.exists() else {}
    if STATE.exists() and not options.resume:
        raise RuntimeError("existing queue state; use --resume to preserve completed work")
    state = {"pid": __import__("os").getpid(), "status": "running", "phase": "repair_agnews_540_580",
             "formal_ready": False, "completed": previous.get("completed", []),
             "parity_policy": "32_queries_low_mid_high_before_single_disk_sweep"}
    write(STATE, state)
    try:
        if "agnews_timing_repair" not in state["completed"]:
            repair_agnews()
            state["completed"].append("agnews_timing_repair")
        for dataset in ("gist", "dbpedia", "sift10m"):
            if dataset in state["completed"]:
                continue
            run_dataset(dataset, state, run)
            state["completed"].append(dataset)
            write(STATE, state)
        state.update(status="queries_complete_acceptance_pending", phase="done")
        write(STATE, state)
    except BaseException as exc:
        state.update(status="failed", error=str(exc))
        write(STATE, state)
        raise


if __name__ == "__main__":
    main()
