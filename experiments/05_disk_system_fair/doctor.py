"""Read-only diagnostics for the formal 05 disk suite."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from diskfair.common import (
    DEFAULT_DISK_ROOT,
    dataset_paths,
    lsblk_rotational,
    query_split_candidates,
    resolve_executable,
    resolve_repo_path,
    run_preflight,
    vecs_count,
    vecs_shape,
)
from diskfair.dataset_policy import (
    metric_compatibility_error,
    policy_for,
    resident_budget_errors,
    validation_query_count,
)
from diskfair.fio_preflight import resolve_fio
from diskfair.native_contract import (
    METHOD_SPECS,
    ContractError,
    load_port_registry,
    sha256_file,
    validate_registry,
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def _source_roots(repo_root: Path) -> dict[str, Path]:
    return {
        "Faiss": repo_root / "baselines" / "faiss" / "upstream",
        "RaBitQ": repo_root / "Ours" / "core" / "hnswlib" / "space_rabitq.h",
        "SAQ": repo_root / "baselines" / "saq",
        "DiskANN": repo_root / "baselines" / "diskann",
        "SymphonyQG": repo_root / "baselines" / "symphonyqg",
        "SVS": repo_root / "baselines" / "svs",
        "Glass": repo_root / "baselines" / "pyglass",
        "glog": repo_root / "baselines" / "deps" / "glog",
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _external_symlinks(root: Path, repo_root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_symlink() and not _inside(path, repo_root)
    ]


def _elf_runpaths(path: Path) -> tuple[str, ...]:
    if not path.is_file() or shutil.which("readelf") is None:
        return ()
    try:
        output = subprocess.check_output(
            ["readelf", "-d", str(path)], stderr=subprocess.DEVNULL, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return ()
    paths: list[str] = []
    for line in output.splitlines():
        if "(RPATH)" not in line and "(RUNPATH)" not in line:
            continue
        if "[" in line and "]" in line:
            paths.extend(line.rsplit("[", 1)[1].split("]", 1)[0].split(":"))
    return tuple(paths)


def _ours_graph_candidates(repo_root: Path, dataset: str) -> tuple[Path, ...]:
    filename = f"{dataset}_Ours_R64_Lbuild400.graph.bin"
    return (
        repo_root / "results" / "graph" / dataset / "Ours" / filename,
        repo_root / "results" / dataset / "indexes" / "02_diskann_fair" / "Ours" / filename,
        repo_root / "results" / "02_diskann_fair" / dataset / "indexes" / "Ours" / filename,
    )


def run_doctor(
    *,
    repo_root: Path,
    layers: Iterable[str],
    datasets: Iterable[str],
    data_root: Path,
    ports_path: Path,
    disk_root: Path | None,
    disk_profile: str = "nvme",
    search_dram_budget_gib: float = 2.0,
    val_queries: int | None = None,
) -> int:
    layers = tuple(layers)
    datasets = tuple(datasets)
    ports_path = resolve_repo_path(ports_path, repo_root)
    data_root = resolve_repo_path(data_root, repo_root)
    if disk_root is None:
        disk_root = DEFAULT_DISK_ROOT
    else:
        disk_root = resolve_repo_path(disk_root, repo_root)
    if disk_profile == "auto":
        disk_root.mkdir(parents=True, exist_ok=True)
        disk_profile = "hdd_raid" if lsblk_rotational(disk_root) is True else "nvme"
    checks: list[Check] = []

    def add(ok: bool, name: str, good: str, bad: str) -> None:
        checks.append(Check("PASS" if ok else "FAIL", name, good if ok else bad))

    for executable in ("cmake", "ninja", "c++"):
        resolved = shutil.which(executable)
        add(
            resolved is not None,
            f"tool:{executable}",
            resolved or "",
            f"{executable} is not on PATH",
        )
    fio, _fio_env = resolve_fio()
    add(
        fio is not None,
        "tool:fio",
        str(fio) if fio else "",
        "fio is required; run scripts/setup_fio_local.sh",
    )
    local_libaio = repo_root / "baselines" / "deps" / "local" / "usr" / "include" / "libaio.h"
    add(
        Path("/usr/include/libaio.h").exists() or local_libaio.exists(),
        "dependency:libaio",
        "/usr/include/libaio.h or baselines/deps/local/usr/include/libaio.h",
        "libaio development header is missing",
    )

    for name, path in _source_roots(repo_root).items():
        add(path.exists(), f"source:{name}", str(path), f"missing {path}")
    lock_path = repo_root / "baselines" / "DEPENDENCY_LOCK.json"
    try:
        lock = json.loads(lock_path.read_text())
        locked_paths = {
            repo_root / str(item["path"])
            for item in lock.get("dependencies", {}).values()
        }
        runtime_paths = {
            repo_root / str(item["path"])
            for item in lock.get("repository_local_runtime_assets", {}).values()
        }
        lock_ok = (
            set(lock.get("dependencies", {}))
            == {
                "faiss",
                "saq",
                "svs",
                "pyglass",
                "symphonyqg",
                "glog",
                "diskann",
            }
            and set(lock.get("repository_local_runtime_assets", {}))
            == {
                "saq_preprocessed_data",
                "svs_python_binding",
                "symphonyqg_python_binding",
                "pyglass_python_binding",
                "cpp_dependency_prefix",
                "fio_local",
            }
            and all(path.exists() for path in locked_paths | runtime_paths)
            and all(_inside(path, repo_root) for path in locked_paths | runtime_paths)
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        lock_ok = False
    add(
        lock_ok,
        "dependency-lock",
        str(lock_path),
        f"missing/invalid repository-local dependency lock {lock_path}",
    )
    external_links = _external_symlinks(repo_root / "baselines", repo_root)
    add(
        not external_links,
        "repository-local-symlinks",
        "all baseline symlinks resolve inside quantization_graph",
        "external symlinks: " + ", ".join(map(str, external_links[:10])),
    )
    saq_binary = repo_root / "baselines" / "saq" / "bin" / "test_fixed_candidates"
    saq_runpaths = _elf_runpaths(saq_binary)
    bad_runpaths = [
        value
        for value in saq_runpaths
        if value.startswith("/") and not _inside(Path(value), repo_root)
    ]
    add(
        saq_binary.is_file() and not bad_runpaths,
        "repository-local-saq-runtime",
        f"{saq_binary}; RUNPATH={':'.join(saq_runpaths) or '<system>'}",
        f"missing SAQ binary or external RUNPATH={':'.join(bad_runpaths)}",
    )

    for dataset in datasets:
        paths = dataset_paths(dataset, data_root)
        for kind, path in paths.items():
            add(
                path.is_file() and path.stat().st_size > 0,
                f"data:{dataset}:{kind}",
                f"{path} ({path.stat().st_size} bytes)" if path.exists() else str(path),
                f"missing or empty {path}",
            )
        try:
            policy = policy_for(dataset)
            metric_error = metric_compatibility_error(dataset)
        except ValueError as exc:
            add(False, f"metric:{dataset}", "", str(exc))
        else:
            add(
                metric_error is None,
                f"metric:{dataset}",
                f"source={policy.source_metric}, runner={policy.runner_metric}, "
                f"unit_normalized={policy.vectors_unit_normalized}",
                metric_error or "",
            )
        if paths["base"].is_file() and paths["query"].is_file():
            try:
                base_count, dimension = vecs_shape(paths["base"])
                total_queries = vecs_count(paths["query"])
                validation_count = validation_query_count(dataset, total_queries, val_queries)
            except ValueError as exc:
                add(False, f"split-policy:{dataset}", "", str(exc))
            else:
                budget_errors = resident_budget_errors(
                    dataset,
                    base_count,
                    dimension,
                    layers,
                    search_dram_budget_gib,
                )
                for error in budget_errors:
                    add(False, f"dram-budget:{dataset}", "", error)
                if not budget_errors:
                    add(
                        True,
                        f"dram-budget:{dataset}",
                        f"resident-code lower bounds fit B={search_dram_budget_gib:g} GiB",
                        "",
                    )
                required = (
                    "validation_query.fvecs",
                    "validation_gt.ivecs",
                    "test_query.fvecs",
                    "test_gt.ivecs",
                )
                candidates = query_split_candidates(dataset)
                split = next(
                    (candidate for candidate in candidates if all((candidate / name).is_file() for name in required)),
                    None,
                )
                partial = [
                    candidate
                    for candidate in candidates
                    if any((candidate / name).exists() for name in required)
                    and not all((candidate / name).is_file() for name in required)
                ]
                if partial:
                    add(False, f"split:{dataset}", "", f"incomplete query split: {partial[0]}")
                elif split is None:
                    add(
                        True,
                        f"split:{dataset}",
                        f"will create validation={validation_count}, "
                        f"test={total_queries - validation_count} in immutable run output",
                        "",
                    )
                else:
                    actual_validation = vecs_count(split / "validation_query.fvecs")
                    actual_test = vecs_count(split / "test_query.fvecs")
                    split_ok = (
                        actual_validation == validation_count
                        and actual_validation + actual_test == total_queries
                    )
                    add(
                        split_ok,
                        f"split:{dataset}",
                        f"{split}: validation={actual_validation}, test={actual_test}",
                        f"stale split {split}: validation={actual_validation}, test={actual_test}; "
                        f"expected validation={validation_count}, total={total_queries}",
                    )
        if "05b" in layers:
            shared_filename = "diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
            shared_candidates = (
                repo_root / "results" / "graph" / dataset / "shared_graph" / shared_filename,
                repo_root
                / "results"
                / dataset
                / "indexes"
                / "02_diskann_fair"
                / "shared_graph"
                / shared_filename,
            )
            graph = next((path for path in shared_candidates if path.exists()), shared_candidates[0])
            add(
                graph.is_file() and graph.stat().st_size > 0,
                f"shared-graph:{dataset}",
                str(graph),
                f"missing formal R64 shared graph {graph}",
            )
            ours_candidates = _ours_graph_candidates(repo_root, dataset)
            ours_graph = next((path for path in ours_candidates if path.exists()), ours_candidates[0])
            add(
                ours_graph.is_file() and ours_graph.stat().st_size > 0,
                f"ours-native-graph:{dataset}",
                str(ours_graph),
                "missing formal M64 ExRaBitQ-symmetric Ours graph; checked "
                + ", ".join(str(path) for path in ours_candidates),
            )

    try:
        ports = load_port_registry(ports_path)
        validate_registry(ports, layers)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        checks.append(Check("FAIL", "native-ports", str(exc)))
    else:
        bad_binaries: list[str] = []
        for spec in METHOD_SPECS:
            if spec.layer not in layers:
                continue
            port = ports[spec.key]
            command = port["command"]
            try:
                resolved = Path(resolve_executable(command[0], repo_root))
            except (FileNotFoundError, PermissionError) as exc:
                bad_binaries.append(f"{spec.key}: {exc}")
                continue
            if sha256_file(resolved) != port["binary_sha256"]:
                bad_binaries.append(f"{spec.key}: binary SHA-256 differs from registry")
        if bad_binaries:
            checks.append(Check("FAIL", "native-binaries", "; ".join(bad_binaries)))
        else:
            checks.append(Check("PASS", "native-ports", "all required ports are hash-pinned"))

    try:
        report = run_preflight(disk_root.resolve())
    except OSError as exc:
        checks.append(Check("FAIL", "formal-disk", str(exc)))
    else:
        rotational_ok = (
            report.get("rotational") is False
            if disk_profile == "nvme"
            else report.get("rotational") is True
        )
        ready = bool(
            report.get("odirect") is True
            and rotational_ok
            and report.get("sufficient_space_200gib") is True
        )
        detail = (
            f"profile={disk_profile}, root={disk_root.resolve()}, "
            f"rotational={report.get('rotational')}, "
            f"odirect={report.get('odirect')}, free_gib={report.get('free_gib', 0):.1f}"
        )
        checks.append(Check("PASS" if ready else "FAIL", "formal-disk", detail))

    print("05 disk suite doctor")
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.detail}")
    failures = sum(check.status == "FAIL" for check in checks)
    print(f"summary: {len(checks) - failures} passed, {failures} failed")
    return 0 if failures == 0 else 2


def as_json(checks: Iterable[Check]) -> str:
    """Kept small and dependency-free for callers that need structured checks."""
    return json.dumps([asdict(item) for item in checks], indent=2)
