"""Serialized, isolated AGNews remeasurement; never relabel historical results."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from measure_05_process import compare_traces, digest, measure

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/single_test_w32_20260911"
CPP = ROOT / "build/formal_single_run_gcc11/05_disk_system_fair"
BINARIES = {
    "Ours-Disk": ROOT / "src/graph_core/target/release/qgraph05_shared_graph_port",
    "SymphonyQG-DiskPort": CPP / "qgraph05_symphonyqg_disk_port",
    "OG-LVQ-DiskPort": CPP / "qgraph05_og_lvq_disk_port",
    "Glass-NSG-DiskPort": CPP / "qgraph05_glass_disk_port",
    "DiskANN-PQ-Disk": ROOT
    / "experiments/03_disk_system/native_diskann/target/release/qgraph05_diskann_port",
}


def identity(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def set_arg(command, key, value):
    if key in command:
        command[command.index(key) + 1] = str(value)
    else:
        command.extend([key, str(value)])


def input_snapshot(command):
    keys = ["--query", "--groundtruth", "--query-order", "--input-manifest"]
    files = [Path(command[command.index(k) + 1]) for k in keys]
    index = Path(command[command.index("--disk-index-dir") + 1])
    files.extend(p for p in index.rglob("*") if p.is_file())
    if "--locality-layout-dir" in command:
        files.extend(Path(command[command.index("--locality-layout-dir") + 1]).glob("*"))
    # Full hashes would themselves warm large index files immediately before timing.
    return {str(p): {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns,
                     "inode": p.stat().st_ino} for p in files if p.is_file()}


def active_searches():
    found = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            if args and Path(os.fsdecode(args[0])).name.startswith("qgraph05_"):
                found.append(int(proc.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue-parent", type=int)
    parser.add_argument("--wait-child", type=int)
    args = parser.parse_args()
    lock = (RUN / "acceptance_serial.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    state = {"status": "preparing", "formal_ready": False, "methods": {}}
    def save():
        state["updated"] = datetime.datetime.now().isoformat()
        temp = output / "state.tmp"
        temp.write_text(json.dumps(state, indent=2) + "\n")
        temp.replace(output / "state.json")
    save()
    run = json.loads((RUN / "run.json").read_text())
    run.update(datasets=["agnews"], methods=list(BINARIES), acceptance="pending",
               memory_policy="whole_user_process_RLIMIT_AS_2GiB", formal_ready=False)
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    binaries = output / "binaries"
    binaries.mkdir()
    for method, source in BINARIES.items():
        shutil.copy2(source, binaries / source.name)
        state["methods"][method] = {"binary_sha256": digest(binaries / source.name), "status": "pending"}
    parent_identity = identity(args.queue_parent) if args.queue_parent else None
    child_identity = identity(args.wait_child) if args.wait_child else None
    paused = False
    try:
        if args.queue_parent:
            cmd = Path(f"/proc/{args.queue_parent}/cmdline").read_bytes()
            if b"resume_05c_single_test.py" not in cmd or not parent_identity:
                raise ValueError("refusing to pause an unrelated process")
            if not args.wait_child or not child_identity:
                raise ValueError("a live queue child is required")
            child_cmd = Path(f"/proc/{args.wait_child}/cmdline").read_bytes()
            child_stat = Path(f"/proc/{args.wait_child}/stat").read_text().rsplit(")", 1)[1].split()
            if b"check_03c_query_cache.py" not in child_cmd or int(child_stat[1]) != args.queue_parent:
                raise ValueError("unexpected queue child")
            os.kill(args.queue_parent, signal.SIGSTOP)
            paused = True
            state.update(status="waiting_for_current_dataset", queue_parent=args.queue_parent,
                         wait_child=args.wait_child)
            save()
            while identity(args.wait_child) == child_identity:
                # An exited child remains a zombie while its parent is paused.
                try:
                    stat = Path(f"/proc/{args.wait_child}/stat").read_text().rsplit(")", 1)[1].split()
                except FileNotFoundError:
                    break
                if stat[0] == "Z":
                    break
                time.sleep(5)
        if active_searches():
            raise RuntimeError(f"other native searches still active: {active_searches()}")
        state["status"] = "remeasuring_agnews"
        save()
        original_widths = json.loads((RUN / "run.json").read_text())["widths"]
        for method, source in BINARIES.items():
            row = state["methods"][method]
            command = json.loads((RUN / "agnews" / method / "command.json").read_text())
            command[0] = str(binaries / source.name)
            widths = [w for w in original_widths if w >= 10 or method not in {"Ours-Disk", "DiskANN-PQ-Disk"}]
            folder = output / "agnews" / method
            for key, value in {"--native-binary-sha256": row["binary_sha256"],
                "--integration-widths": ",".join(map(str, widths)), "--workers": 32,
                "--warmup-queries": 100, "--cache-mode": "c0", "--repeat-id": 0,
                "--result-json": folder / "result.json", "--query-trace": folder / "queries.jsonl"}.items():
                set_arg(command, key, value)
            rust = method in {"Ours-Disk", "DiskANN-PQ-Disk"}
            if rust:
                set_arg(command, "--parity-mode", "external")
            row["status"] = "running_disk"
            save()
            try:
                before = input_snapshot(command)
                memory = measure(command, folder, budget=2 << 30)
                if before != input_snapshot(command):
                    raise ValueError("index/input metadata changed during measurement")
                artifact = json.loads((folder / "result.json").read_text())
                if not artifact.get("direct_io") or artifact.get("cache_mode") != "c0":
                    raise ValueError("not a C0 direct-I/O result")
                actual = [r["search_width"] for r in artifact["summary_rows"]]
                if sorted(actual) != sorted(widths):
                    raise ValueError("incomplete width sweep")
                protocol = {"protocol": "fixed_query_warmup_v1", "warmup_queries": 100,
                            "workers": 32, "cache_mode": "c0", "query_order_sha256":
                            digest(command[command.index("--query-order") + 1]),
                            "cross_query_application_cache": False, "device_cache_controlled": False,
                            "cold_storage_claim": False, "index_snapshot": before,
                            "index_unchanged": True, "other_native_searches_at_end": active_searches()}
                (folder / "storage_protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
                row.update(status="running_reference", memory_budget_passed=memory["user_address_space_budget_passed"])
                save()
                reference_dir = output / "references" / method
                ref = command.copy()
                set_arg(ref, "--result-json", reference_dir / "result.json")
                set_arg(ref, "--query-trace", reference_dir / "queries.jsonl")
                if rust:
                    set_arg(ref, "--parity-mode", "inline")
                measure(ref, reference_dir, reference=not rust)
                comparison = compare_traces(folder / "queries.jsonl", reference_dir / "queries.jsonl")
                if rust:
                    native = json.loads((reference_dir / "result.parity.json").read_text())
                    comparison["native_reference_evidence"] = native
                    comparison["passed"] &= native.get("query_comparisons", 0) > 0
                    comparison["passed"] &= native.get("max_recall_delta", 1) <= 0.001
                    comparison["passed"] &= native.get("mean_top10_overlap", 0) >= 0.99
                    comparison["passed"] &= native.get("mean_distance_count_relative_delta", 1) <= 0.01
                    comparison["scope"] = "disk_run_reproducibility_plus_native_memory_parity"
                (folder / "measured_parity.json").write_text(json.dumps(comparison, indent=2) + "\n")
                row.update(status="evidence_complete" if comparison["passed"] else "parity_failed",
                           parity_passed=comparison["passed"], query_comparisons=comparison["query_comparisons"])
            except Exception as exc:
                row.update(status="failed", error=str(exc))
            save()
        state["status"] = "evidence_finished_requires_acceptance"
        save()
        for script in ("export_05c_pending_figures.py", "plot_05c_recall_qps.py"):
            subprocess.run([sys.executable, str(ROOT / "scripts/local_runs" / script),
                            str(output)], check=True)
    except BaseException as exc:
        state.update(status="failed", error=str(exc))
        save()
        raise
    finally:
        if paused and identity(args.queue_parent) == parent_identity:
            os.kill(args.queue_parent, signal.SIGCONT)
            state["queue_resumed"] = True
            save()


if __name__ == "__main__":
    main()
