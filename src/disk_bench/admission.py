"""Evidence-based admission for 03/05 Ours and official DiskANN searches.

The existing native direct/memory comparison runs in a separate preparation
process. Its direct trace must exactly match the budgeted search trace. Reference
RSS and timing never enter performance summaries. This proves storage parity,
not PCA hard-prune safety or an independent audit of an algorithm implementation.
"""
from __future__ import annotations

import json
import math
import mmap
import os
import struct
from pathlib import Path

from .memory_runner import run_measured
from .protocol import sha256, validate_resource_evidence
from .storage_precondition import PROTOCOL as STORAGE_PROTOCOL, option, prepare_search, search_files

PROTOCOL = "separate_native_reference_exact_trace_v1"
from . import official_reference
METHODS = {"Ours-Disk", "DiskANN-PQ-Disk"} | set(official_reference.METHODS)
OUTPUT_FLAGS = {"--result-json", "--query-trace", "--parity-mode"}
INPUT_FLAGS = ("--warmup-query", "--query", "--groundtruth", "--query-order", "--input-manifest",
               "--tuning-lock", "--ours-graph", "--shared-graph",
               "--ours-hot-profile-manifest", "--ours-hot-ranks")


def write(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def supported(command):
    return option(command, "--layer") == "05c" and option(command, "--method") in METHODS


def measurement_environment(command, env=None):
    """Require a disk-only measurement; correctness belongs to a prior process."""
    result = dict(os.environ if env is None else env)
    if supported(command):
        if option(command, '--ours-search-path-dir') or option(command, '--adaptive-route-trace-dir'):
            raise ValueError('search path diagnostics cannot enter performance measurement')
        if option(command, '--parity-mode') != 'external':
            raise ValueError('budgeted search requires separate reference and --parity-mode external')
        result['QG05_FAST'] = '0'
        result['QG05_REFERENCE_MMAP'] = '0'
        for key in ('QG05_FAST_WIDTHS', 'QG05_FAST_WIDTH'):
            result.pop(key, None)
    return result


def replace(command, flag, value):
    if flag in command:
        command[command.index(flag) + 1] = str(value)
    else:
        command.extend([flag, str(value)])


def canonical(command):
    result, i = [], 0
    while i < len(command):
        if command[i] in OUTPUT_FLAGS:
            i += 2
        else:
            result.append(command[i]); i += 1
    return result


def stamp(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def input_snapshot(command, preparation):
    # Search files were already hashed with O_DIRECT. Hash resident assets and
    # query/config inputs separately, outside both search measurement processes.
    hashes = {f["path"]: f["sha256"] for f in preparation["files"]}
    paths = {p.resolve() for p in Path(option(command, "--disk-index-dir")).rglob("*") if p.is_file()}
    for flag in INPUT_FLAGS:
        if option(command, flag):
            paths.add(Path(option(command, flag)).resolve())
    if option(command, "--pca-route-dir"):
        paths.update(p.resolve() for p in Path(option(command, "--pca-route-dir")).rglob("*") if p.is_file())
    paths.add(Path(command[0]).resolve())
    result = {}
    for path in sorted(paths):
        before = stamp(path)
        digest = hashes.get(str(path)) or sha256(path)
        if stamp(path) != before:
            raise ValueError(f"input changed while hashing: {path}")
        result[str(path)] = {"sha256": digest, "stat": before}
    return result


def unchanged(snapshot):
    if not snapshot:
        raise ValueError("missing reference input snapshot")
    for path, record in snapshot.items():
        if stamp(path) != record["stat"]:
            raise ValueError(f"reference input changed: {path}; rerun with a fresh run-id")


def parity_document(result_path):
    artifact = json.loads(Path(result_path).read_text())
    path = Path(result_path).with_suffix(".parity.json")
    p = json.loads(path.read_text())
    if (artifact.get("status") != "done" or artifact.get("implementation_parity") != "passed"
            or artifact.get("parity", {}).get("reference_artifact_sha256") != sha256(path)):
        raise ValueError("native reference parity missing or hash mismatch")
    expected_count = sum(r["query_count"] for r in artifact["summary_rows"])
    if expected_count <= 0 or p.get("query_comparisons") != expected_count:
        raise ValueError("reference parity must cover every measured query/configuration")
    for key, limit, direction in (("max_recall_delta", .001, 1), ("mean_top10_overlap", .99, -1),
                                  ("mean_visited_count_relative_delta", .01, 1),
                                  ("mean_distance_count_relative_delta", .01, 1)):
        value = p.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or direction * (value - limit) > 0:
            raise ValueError(f"reference parity failed: {key}")
    return p


def prepare_reference(command, artifact_path, *, cpu_affinity=None, numa_node=None, env=None):
    if not supported(command) or os.environ.get("QG05_FAST") == "1":
        raise ValueError("separate formal reference requires supported native method and non-fast mode")
    folder = Path(artifact_path).with_suffix(".reference")
    if option(command, '--method') == 'SymphonyQG-DiskPort':
        from .symphony_provenance import verify_export, verify_source
        verify_source(command[0])
        verify_export(option(command, '--disk-index-dir'))
    folder.mkdir(parents=True, exist_ok=False)
    cmd = list(command)
    replace(cmd, "--result-json", folder / "result.json")
    replace(cmd, "--query-trace", folder / "queries.jsonl")
    replace(cmd, "--parity-mode", "internal")
    preparation = prepare_search(cmd, folder / "storage.json")
    inputs = input_snapshot(cmd, preparation)
    # An additional official reference is independent of process isolation.
    # Skipping a required official reference remains diagnostic-only.
    external = (option(cmd, "--method") in official_reference.METHODS
                and os.environ.get("QG05_SKIP_EXTERNAL_PARITY") != "1")
    ref_env = dict(os.environ if env is None else env, QG05_FAST="0",
                   QG05_REFERENCE_MMAP="1" if external else "0")
    evidence = run_measured(cmd, evidence_path=folder / "resources.json", log_path=folder / "terminal.log",
                            reference=True, cpu_affinity=cpu_affinity, numa_node=numa_node,
                            env=ref_env)
    if evidence["status"] != "completed":
        raise ValueError(f"reference process failed: {folder / 'resources.json'}")
    extra = []
    if external:
        binary = official_reference.prepare(cmd, folder, cpu_affinity=cpu_affinity, numa_node=numa_node, env=env)
        inputs[str(binary)] = {"sha256":sha256(binary),"stat":stamp(binary)}
        if option(cmd, '--method') == 'SymphonyQG-DiskPort':
            evidence = verify_source(cmd[0])
            inputs[str(evidence)] = {"sha256": sha256(evidence), "stat": stamp(evidence)}
        extra = ["official.jsonl", "official.resources.json", "reference.native.json"]
    unchanged(inputs)
    parity_document(folder / "result.json")
    files = {name: {"path": str((folder / name).resolve()), "sha256": sha256(folder / name)}
             for name in ["result.json", "result.parity.json", "queries.jsonl", "resources.json", "storage.json"] + extra}
    manifest = folder / "manifest.json"
    write(manifest, dict(protocol=PROTOCOL, preparation_only=True, performance_sample=False,
                         external_parity=("performed" if external else
                                          "skipped_user" if option(cmd, "--method") in official_reference.METHODS
                                          else "not_applicable"),
                         command=canonical(cmd), inputs=inputs, files=files))
    measured = list(command)
    # "external" means the prior process supplies correctness evidence, not
    # that correctness checks are optional. Never load a memory reference here.
    replace(measured, "--parity-mode", "external")
    return measured, manifest


def validate_storage(artifact, resource):
    path = Path(artifact.get("storage_precondition_path", ""))
    if (artifact.get("storage_precondition_protocol") != STORAGE_PROTOCOL
            or not path.is_file() or sha256(path) != artifact.get("storage_precondition_sha256")):
        raise ValueError("storage preparation evidence missing or hash mismatch")
    p = json.loads(path.read_text())
    for key, value in dict(protocol=STORAGE_PROTOCOL, status="completed", method=artifact["method"],
                           mode="direct", scope="all_bytes_of_disk_search_files_once_before_search_process",
                           included_in_query_timing=False, device_cache_controlled=False,
                           cold_storage_claim=False).items():
        if p.get(key) != value:
            raise ValueError(f"invalid storage preparation: {key}")
    paths = [str(p) for p in search_files(resource["command"])]
    if p.get("planned_files") != paths or [f["path"] for f in p["files"]] != paths:
        raise ValueError("storage preparation does not cover this search's files")
    if not p["started_unix"] <= p["finished_unix"] <= resource["started_unix"]:
        raise ValueError("storage preparation must finish before measured search")
    for f in p["files"]:
        s = Path(f["path"]).stat()
        if (f["io"] != "O_DIRECT" or f["offset"] != 0 or f["bytes"] != s.st_size or f["length"] != s.st_size
                or [f["device"], f["inode"], f["mtime_ns"]] != [s.st_dev, s.st_ino, s.st_mtime_ns]
                or len(f["sha256"]) != 64):
            raise ValueError("incomplete or changed storage input")
    if p.get("total_bytes") != sum(f["bytes"] for f in p["files"]):
        raise ValueError("storage preparation total bytes mismatch")
    return p


def exact_traces(reference, measured):
    def read(path):
        rows = {}
        with Path(path).open() as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = tuple(row[k] for k in ("config_id", "search_width", "beam_width", "query_id"))
                if key in rows:
                    raise ValueError("duplicate query/configuration in parity trace")
                rows[key] = tuple(row[k] for k in ("result_ids", "recall_at_10", "visited_nodes", "distance_evaluations"))
        return rows
    a, b = read(reference), read(measured)
    if not a or a != b:
        raise ValueError("measured search differs from separate reference trace")
    return len(a)


def audit_trace(artifact, command):
    """Recompute Recall@10 from IDs/GT and reconcile all per-config I/O means."""
    groups = {}
    order_data = Path(option(command, "--query-order")).read_bytes()
    order = set(struct.unpack(f"<{len(order_data)//4}I", order_data))
    if not order or len(order)*4 != len(order_data):
        raise ValueError("empty or duplicate query order")
    with Path(option(command, "--groundtruth")).open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as gt:
        k = struct.unpack_from("<I", gt)[0]
        stride = 4*(k+1)
        if k < 10 or len(gt) % stride:
            raise ValueError("invalid groundtruth ivecs for Recall@10")
        with Path(artifact["query_trace_path"]).open() as stream:
            for line in stream:
                if not line.strip():
                    continue
                r = json.loads(line)
                qid = r["query_id"]
                if qid not in order or (qid+1)*stride > len(gt) or struct.unpack_from("<I",gt,qid*stride)[0] != k:
                    raise ValueError("trace query ID/groundtruth mismatch")
                ids = r["result_ids"][:10]
                if len(ids) != len(set(ids)) or any(i < 0 or i >= artifact["base_count"] for i in ids):
                    raise ValueError("invalid result IDs")
                truth = set(struct.unpack_from("<10I",gt,qid*stride+4))
                recall = len(set(ids) & truth)/10
                if not math.isclose(recall,r["recall_at_10"],abs_tol=1e-8):
                    raise ValueError("Recall does not match result IDs and groundtruth")
                key = tuple(r[k] for k in ("config_id","search_width","beam_width"))
                group = groups.setdefault(key, {"ids":set(), "sums":[0.,0.,0.,0.]})
                if qid in group["ids"]:
                    raise ValueError("duplicate query in measured configuration")
                group["ids"].add(qid)
                for i,value in enumerate((recall,r["io_requests"],r["sectors_4k"],r["bytes_read"])):
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("invalid trace I/O counter")
                    group["sums"][i] += value
    for row in artifact["summary_rows"]:
        key = tuple(row[k] for k in ("config_id","search_width","beam_width"))
        group = groups.pop(key, None)
        if group is None or group["ids"] != order or row["query_count"] != len(order):
            raise ValueError("summary/configuration does not cover the query order exactly once")
        for total,key in zip(group["sums"],("recall","io_requests_per_query","sectors_4k_per_query","bytes_read_per_query")):
            if not math.isclose(total/len(order),row[key],abs_tol=1e-5,rel_tol=1e-8):
                raise ValueError(f"summary differs from query trace: {key}")
    if groups:
        raise ValueError("trace contains configurations missing from summaries")
    if artifact.get("implementation_fingerprint") == "05c-ours-cache-allocation-v6":
        if artifact.get("io_classification") != "graph_and_packed_records_by_request_stage_v1":
            raise ValueError("missing classified I/O evidence")
        totals = {}
        fields = [f"{stage}_{metric}" for stage in ("graph","full4_record","rerank_record")
                  for metric in ("io_requests","bytes_read","shared_cache_hits","query_cache_hits")]
        with Path(artifact["query_trace_path"]).open() as stream:
            for line in stream:
                r = json.loads(line)
                key = (r["search_width"], r["beam_width"])
                acc = totals.setdefault(key, [0.]*len(fields))
                for i,field in enumerate(fields):
                    value = r[field]
                    if type(value) is not int or value < 0: raise ValueError("invalid classified I/O counter")
                    acc[i] += value
                for metric in ("io_requests","bytes_read"):
                    if sum(r[f"{stage}_{metric}"] for stage in ("graph","full4_record","rerank_record")) != r[metric]:
                        raise ValueError("classified I/O does not reconcile to physical total")
        for row in artifact["summary_rows"]:
            acc = totals[(row["search_width"],row["beam_width"])]
            for value,field in zip(acc,fields):
                if not math.isclose(value/row["query_count"],row[field+"_per_query"],abs_tol=1e-5,rel_tol=1e-8):
                    raise ValueError("classified I/O summary differs from trace")



def verify_reference(artifact, resource, manifest_path, manifest_sha):
    path = Path(manifest_path)
    if sha256(path) != manifest_sha:
        raise ValueError("separate reference manifest hash mismatch")
    m = json.loads(path.read_text())
    command = resource["command"]
    if artifact.get("throughput_comparable") is False or artifact.get("diagnostic_neighbor_trace"):
        raise ValueError("diagnostic search paths cannot be performance results")
    if artifact.get("method") == "Ours-Disk":
        allocation = option(command, "--ours-cache-allocation") or "graph_first"
        if artifact.get("ours_cache_allocation", "graph_first") != allocation:
            raise ValueError("artifact cache allocation differs from measured command")
    if artifact.get("timing_semantics") != "search_wall_excludes_recall_evaluation":
        raise ValueError("formal QPS must exclude post-search Recall evaluation")
    native_path = Path(artifact.get("native_search_result_path", ""))
    if not native_path.is_file() or sha256(native_path) != artifact.get("native_search_result_sha256"):
        raise ValueError("preserved native search result missing or hash mismatch")
    native = json.loads(native_path.read_text())
    if len(native["summary_rows"]) != len(artifact["summary_rows"]):
        raise ValueError("native/measured summary length mismatch")
    for original, row in zip(native["summary_rows"], artifact["summary_rows"]):
        if any(row.get(key) != value for key, value in original.items() if key != "peak_rss_bytes"):
            raise ValueError("summary differs from preserved native result")
    if m.get("reference_kind") in ("ours_cache_pair_exact_paths_v1", "ours_cache_pair_results_only_v1"):
        from .paired_reference import verify
        if m.get("protocol") != PROTOCOL: raise ValueError("cache-pair protocol mismatch")
        return verify(artifact, resource, path, m)
    if (m.get("protocol") != PROTOCOL or m.get("preparation_only") is not True
            or m.get("performance_sample") is not False or not supported(command)
            or m["command"] != canonical(command) or option(command, "--parity-mode") != "external"):
        raise ValueError("reference command/protocol differs from measured search")
    unchanged(m["inputs"])
    for item in m["files"].values():
        if sha256(item["path"]) != item["sha256"]:
            raise ValueError("reference evidence file hash mismatch")
    file = lambda name: Path(m["files"][name]["path"])
    e = json.loads(file("resources.json").read_text())
    if (e.get("status") != "completed" or e.get("exit_code") != 0 or e.get("reference_only") is not True
            or e.get("binary_sha256") != resource["binary_sha256"]
            or e.get("launch_policy") != resource.get("launch_policy")
            or canonical(e["command"]) != canonical(command) or option(e["command"], "--parity-mode") != "internal"
            or Path(option(e["command"], "--result-json")).resolve() != file("result.json").resolve()):
        raise ValueError("invalid separate reference resource evidence")
    prep = validate_storage(artifact, resource)
    finished = e["finished_unix"]
    if option(command,"--method") in official_reference.METHODS:
        finished = max(finished, official_reference.verify(m,command))
    if finished > prep["started_unix"]:
        raise ValueError("search storage must be prepared after the reference process finishes")
    for f in prep["files"]:
        if m["inputs"][f["path"]]["sha256"] != f["sha256"]:
            raise ValueError("search index bytes differ from reference")
    r = json.loads(file("result.json").read_text())
    for key in ("method", "layer", "dataset", "phase", "run_id", "repeat_id", "workers", "cache_mode",
                "search_dram_budget_gib", "implementation_fingerprint", "source_kernel", "source_index_manifest_sha256",
                "native_binary_sha256", "query_order_sha256", "query_split_sha256", "warmup_queries"):
        if r.get(key) != artifact.get(key):
            raise ValueError(f"reference artifact mismatch: {key}")
    if sha256(file("queries.jsonl")) != r["query_trace_sha256"]:
        raise ValueError("native reference trace hash mismatch")
    if sha256(artifact["query_trace_path"]) != artifact["query_trace_sha256"]:
        raise ValueError("measured trace hash mismatch")
    comparisons = exact_traces(file("queries.jsonl"), artifact["query_trace_path"])
    audit_trace(artifact, command)
    p = parity_document(file("result.json"))
    if comparisons != p["query_comparisons"] or comparisons != sum(r["query_count"] for r in artifact["summary_rows"]):
        raise ValueError("separate reference does not cover all measured queries")
    return dict(p, reference_artifact_sha256=sha256(file("result.parity.json")))


def finalize(path, manifest, *, spec, expected, disk_root):
    from .native_contract import validate_artifact
    path, manifest = Path(path), Path(manifest)
    artifact = json.loads(path.read_text())
    resource = validate_resource_evidence(artifact, dict(expected, artifact_path=str(path.resolve())))
    native = path.with_suffix(".native.json")
    artifact.update(native_search_result_path=str(native.resolve()), native_search_result_sha256=sha256(native))
    manifest_data = json.loads(Path(manifest).read_text())
    artifact["parity"] = verify_reference(artifact, resource, manifest, sha256(manifest))
    artifact.update(implementation_parity="passed", separate_reference_protocol=PROTOCOL,
                    separate_reference_path=str(manifest.resolve()), separate_reference_sha256=sha256(manifest),
                    formal_ready=False, memory_acceptance_basis="whole_process_peak_rss_and_zero_sampled_swap")
    # Preserve native false flags and category attribution in *.native.json.
    # A failed check leaves the candidate explicitly non-formal on disk.
    write(path, artifact)
    validate_artifact(path, spec=spec, expected=expected, disk_root=disk_root, require_formal_ready=False)
    artifact["external_parity_status"] = manifest_data.get("external_parity", "missing")
    artifact["formal_ready"] = (artifact["method"] not in official_reference.METHODS or
                                manifest_data.get("external_parity") == "performed")
    write(path, artifact)
    return artifact
