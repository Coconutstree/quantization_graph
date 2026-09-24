"""Freeze validation-only hot records before timing the formal Ours cache policy."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .memory_runner import run_measured
from .ours_pca import sha, dump
from .storage_precondition import prepare_search

POLICY = "validation_hot_then_dynamic_fifo16_v1"


def value(command, flag):
    return command[command.index(flag) + 1]


def replace(command, flag, new):
    command[command.index(flag) + 1] = str(new)


def profile_args(manifest: Path, expected: dict, expected_sha: str | None = None) -> list[str]:
    if expected_sha is not None and sha(manifest) != expected_sha:
        raise ValueError("hot-record profile differs from frozen validation manifest")
    data = json.loads(manifest.read_text())
    for key, wanted in expected.items():
        if data.get(key) != wanted:
            raise ValueError(f"hot-record profile mismatch: {key}")
    if data.get("source_phase") != "validation" or data.get("policy") != POLICY:
        raise ValueError("hot records must be learned from validation only")
    ranks = manifest.parent / "ranks.u32"
    if sha(ranks) != data["ranks_sha256"]:
        raise ValueError("hot-record ranks hash mismatch")
    return ["--ours-record-cache-policy", "hot_dynamic",
            "--ours-hot-profile-manifest", str(manifest.resolve()),
            "--ours-hot-profile-sha256", sha(manifest),
            "--ours-hot-ranks", str(ranks.resolve()),
            "--ours-hot-ranks-sha256", data["ranks_sha256"]]


def prepare_record_args(command: list[str], artifact: Path, *, phase: str,
                        tuning_lock: Path | None, cpu_affinity=None, numa_node=None, shared_profile: Path | None = None) -> list[str]:
    if value(command, "--cache-mode") == "c0":
        return ["--ours-record-cache-policy", "off"]
    route = json.loads(artifact.with_suffix(".route_plan.json").read_text())
    expected = dict(policy=POLICY, source_phase="validation", route_plan=route,
                    native_binary_sha256=value(command, "--native-binary-sha256"),
                    source_graph_sha256=value(command, "--ours-graph-sha256"),
                    index_dir=str(Path(value(command, "--disk-index-dir")).resolve()),
                    pca_assets_sha256=(value(command, "--pca-assets-sha256")
                                      if "--pca-assets-sha256" in command else ""))
    if "--search-widths" in command:
        expected["profile_search_widths"] = [int(w) for w in value(command,"--search-widths").split(',')]
    if phase == "test":
        if tuning_lock is None:
            raise ValueError("test hot records require a frozen validation tuning lock")
        selected = json.loads(tuning_lock.read_text())["selected"]["Ours-Disk::hybrid_disk"]
        allocation = value(command, "--ours-cache-allocation") if "--ours-cache-allocation" in command else "graph_first"
        if selected.get("ours_cache_allocation", "graph_first") != allocation:
            raise ValueError("cache allocation differs from frozen validation lock")
        if selected.get("ours_record_cache_policy") != POLICY:
            raise ValueError("validation did not select the current hot+dynamic cache policy")
        return profile_args(Path(selected["ours_hot_profile_manifest"]), expected,
                            selected["ours_hot_profile_sha256"])
    if phase not in ("validation", "validate"):
        raise ValueError("hot records can only be prepared using validation")
    expected.update(query_sha256=sha(Path(value(command, "--query"))),
                    query_split_sha256=value(command, "--query-split-sha256"),
                    query_order_sha256=value(command, "--query-order-sha256"))
    if shared_profile is not None:
        return profile_args(shared_profile, expected)
    folder = artifact.with_suffix(".hot_profile")
    manifest = folder / "manifest.json"
    if manifest.exists():
        return profile_args(manifest, expected)
    folder.mkdir(parents=True, exist_ok=False)
    ranks = folder / "ranks.u32"
    profile_command = list(command)
    replace(profile_command, "--phase", "validation")
    replace(profile_command, "--result-json", folder / "profile.json")
    replace(profile_command, "--query-trace", folder / "profile.queries.jsonl")
    profile_command += ["--ours-record-cache-policy", "profile",
                        "--ours-record-profile-output", str(ranks)]
    preparation = folder / "storage_precondition.json"
    prepare_search(profile_command, preparation)
    # Offline preparation is recorded separately, not charged as search QPS or
    # admitted as a 2 GiB search point. Measured search receives no profile arrays.
    evidence_path = folder / "resources.json"
    evidence = run_measured(profile_command, evidence_path=evidence_path,
                           log_path=folder / "terminal.log", reference=True,
                           cpu_affinity=cpu_affinity, numa_node=numa_node,
                           env=dict(os.environ, OMP_NUM_THREADS=value(command, "--workers"),
                                    MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1"))
    if evidence["status"] != "completed":
        raise ValueError(f"validation hot-record profile failed: {evidence_path}")
    result = json.loads((folder / "profile.json").read_text())
    if result.get("throughput_comparable") is not False or result.get("ours_record_cache_policy") != "profile":
        raise ValueError("expected an untimed validation profile artifact")
    dump(manifest, dict(**expected, ranks_sha256=sha(ranks),
         resource_measurement_sha256=sha(evidence_path),
         storage_precondition_sha256=sha(preparation),
         profile_result_sha256=sha(folder / "profile.json"),
         configs=[dict(width=r["search_width"], beam=r["beam_width"]) for r in result["summary_rows"]],
         profile_cache_allocation=(value(command, "--ours-cache-allocation") if "--ours-cache-allocation" in command else "graph_first"),
         scoring="normalized record requests per validation configuration; descending score then ID",
         preparation_only=True))
    return profile_args(manifest, expected)
