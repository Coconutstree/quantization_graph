"""Conservative whole-user-process budget evidence, without allocator estimates."""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def measure(command, folder, *, budget=None, reference=False, timeout=86400):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("QG05_") or key in {"LD_PRELOAD", "LD_AUDIT"}:
            del env[key]
    env.update(MALLOC_ARENA_MAX="2", OMP_DYNAMIC="FALSE", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", QG05_MEASURE_WHOLE_PROCESS="1")
    if reference:
        env["QG05_REFERENCE_MMAP"] = "1"

    def limits():
        if budget is not None:
            resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    (folder / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    evidence = {"command_sha256": digest(folder / "command.json"),
                "binary_sha256": digest(command[0]), "rlimit_as_bytes": budget,
                "scope": "whole_user_process_including_inputs_libraries_stacks_allocator",
                "excludes": ["kernel_memory", "device_controller_cache"],
                "category_attribution_complete": False,
                "environment": {k: env[k] for k in ["MALLOC_ARENA_MAX", "OMP_DYNAMIC",
                    "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "QG05_MEASURE_WHOLE_PROCESS"]},
                "reference_only": reference, "started_unix": time.time()}
    peaks = {k: 0 for k in ("VmPeak", "VmHWM", "VmSwap")}
    with (folder / "terminal.log").open("w") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   preexec_fn=limits)
        evidence["pid"] = process.pid
        verified_limit = False
        try:
            while True:
                try:
                    for line in Path(f"/proc/{process.pid}/status").read_text().splitlines():
                        key, _, value = line.partition(":")
                        if key in peaks:
                            peaks[key] = max(peaks[key], int(value.split()[0]) * 1024)
                    if budget is not None:
                        verified_limit |= resource.prlimit(process.pid, resource.RLIMIT_AS) == (budget, budget)
                except (FileNotFoundError, ProcessLookupError):
                    pass
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    break
                if time.time() - evidence["started_unix"] > timeout:
                    process.kill()
                    _, status, usage = os.wait4(process.pid, 0)
                    process.returncode = os.waitstatus_to_exitcode(status)
                    evidence["timed_out"] = True
                    break
                time.sleep(0.05)
        except BaseException:
            if process.returncode is None:
                process.kill()
                _, status, _ = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            raise
    evidence.update(exit_code=process.returncode, finished_unix=time.time(),
                    kernel_wait4_peak_rss_bytes=int(usage.ru_maxrss) * 1024,
                    sampled_high_water_bytes=peaks, hard_limit_verified=verified_limit,
                    user_address_space_budget_passed=(budget is not None and verified_limit
                        and process.returncode == 0 and peaks["VmPeak"] <= budget
                        and int(usage.ru_maxrss) * 1024 <= budget))
    (folder / "memory_measurement.json").write_text(json.dumps(evidence, indent=2) + "\n")
    if process.returncode:
        raise RuntimeError(f"measurement failed ({process.returncode}): {folder / 'terminal.log'}")
    return evidence


def compare_traces(actual, reference):
    def read(path):
        rows = {}
        with Path(path).open() as stream:
            for line in stream:
                row = json.loads(line)
                key = (row["search_width"], row["query_id"])
                if key in rows:
                    raise ValueError(f"duplicate trace key {key}")
                rows[key] = row
        if not rows:
            raise ValueError("empty parity evidence")
        return rows
    a, b = read(actual), read(reference)
    if a.keys() != b.keys():
        raise ValueError("parity trace key sets differ")
    fields = ["result_ids", "recall_at_10", "visited_nodes", "distance_evaluations"]
    mismatches = []
    for key, row in a.items():
        for field in fields:
            if field not in row or field not in b[key] or row[field] != b[key][field]:
                mismatches.append({"key": key, "field": field})
        if len(row.get("result_ids", [])) != 10:
            mismatches.append({"key": key, "field": "top10_length"})
    return {"passed": not mismatches, "query_comparisons": len(a),
            "reference_artifact_sha256": digest(reference),
            "disk_artifact_sha256": digest(actual), "compared_fields": fields,
            "mismatch_count": len(mismatches), "first_mismatches": mismatches[:20],
            "scope": "same_search_kernel_mmap_vs_direct_io_not_independent_algorithm"}
