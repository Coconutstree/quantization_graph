"""Read-only diagnostics for the formal 05 disk suite."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from diskfair.common import dataset_paths, run_preflight
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


def run_doctor(
    *,
    repo_root: Path,
    layers: Iterable[str],
    datasets: Iterable[str],
    data_root: Path,
    ports_path: Path,
    disk_root: Path | None,
    disk_profile: str = "nvme",
) -> int:
    layers = tuple(layers)
    datasets = tuple(datasets)
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
        for kind, path in dataset_paths(dataset, data_root).items():
            add(
                path.is_file() and path.stat().st_size > 0,
                f"data:{dataset}:{kind}",
                f"{path} ({path.stat().st_size} bytes)" if path.exists() else str(path),
                f"missing or empty {path}",
            )
        split = repo_root / "results" / "03_system_fair" / dataset / "csv" / "_query_splits"
        for filename in (
            "validation_query.fvecs",
            "validation_gt.ivecs",
            "test_query.fvecs",
            "test_gt.ivecs",
        ):
            path = split / filename
            add(
                path.is_file() and path.stat().st_size > 0,
                f"split:{dataset}:{filename}",
                str(path),
                f"missing 03 query split {path}",
            )
        if "05b" in layers:
            graph = (
                repo_root
                / "results"
                / dataset
                / "indexes"
                / "02_diskann_fair"
                / "shared_graph"
                / "diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
            )
            add(
                graph.is_file() and graph.stat().st_size > 0,
                f"shared-graph:{dataset}",
                str(graph),
                f"missing formal R64 shared graph {graph}",
            )
            ours_graph = (
                repo_root
                / "results"
                / "02_diskann_fair"
                / dataset
                / "indexes"
                / "Ours"
                / f"{dataset}_Ours_R64_Lbuild400.graph.bin"
            )
            add(
                ours_graph.is_file() and ours_graph.stat().st_size > 0,
                f"ours-native-graph:{dataset}",
                str(ours_graph),
                f"missing formal M64 ExRaBitQ-symmetric Ours graph {ours_graph}",
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
            executable = Path(command[0]) if os.path.sep in command[0] else None
            resolved = executable if executable and executable.is_absolute() else None
            if resolved is None:
                found = shutil.which(command[0])
                resolved = Path(found) if found else None
            if resolved is None or not resolved.is_file() or not os.access(resolved, os.X_OK):
                bad_binaries.append(f"{spec.key}: executable not found")
                continue
            if sha256_file(resolved) != port["binary_sha256"]:
                bad_binaries.append(f"{spec.key}: binary SHA-256 differs from registry")
        if bad_binaries:
            checks.append(Check("FAIL", "native-binaries", "; ".join(bad_binaries)))
        else:
            checks.append(Check("PASS", "native-ports", "all required ports are hash-pinned"))

    if disk_root is None:
        checks.append(
            Check("FAIL", "formal-disk", "--disk-root was not supplied; formal phases require it")
        )
    else:
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
