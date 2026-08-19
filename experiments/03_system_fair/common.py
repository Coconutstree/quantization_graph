"""Shared helpers for the 03 system-fair experiment framework.

This module is the Python equivalent of the C++ ``experiments/common``
infrastructure listed in BASELINE_EXPERIMENT_PLAN_MS_V2.md (dataset loader,
groundtruth/recall, timer, RSS / index-size, hardware info, CSV writer,
repeat runner and thread pinning).  All measurements are wall-clock based and
reported in the units required by the plan (query latency in microseconds,
build time in milliseconds).
"""

from __future__ import annotations

import csv
import json
import os
import platform
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
MINICONDA_PYTHON = Path(os.environ.get("MINICONDA_PYTHON", "python3"))
NLTK_FIELDS = ["build_time_ms", "index_size_mb", "peak_rss_mb"]


# --------------------------------------------------------------------------
# dataset I/O
# --------------------------------------------------------------------------


def read_fvecs(path: str | Path, count: int | None = None) -> np.ndarray:
    """Read a .fvecs file into a float32 (n, d) array."""
    path = Path(path)
    with path.open("rb") as f:
        buf = np.fromfile(f, dtype=np.int32, count=1)
        if buf.size != 1:
            raise ValueError(f"empty fvecs: {path}")
        dim = int(buf[0])
        raw = np.fromfile(f, dtype=np.float32)
    # raw floats include one int32 dim-header per row (first header already
    # consumed); headers sit at raw indices k*(dim+1)-1 for k=1..n-1
    n = (raw.size + 1) // (dim + 1)
    mask = np.ones(raw.size, dtype=bool)
    if n > 1:
        header_idx = np.arange(1, n) * (dim + 1) - 1
        mask[header_idx] = False
    if mask.sum() != n * dim:
        raise ValueError(f"malformed fvecs: {path}")
    arr = raw[mask].reshape(n, dim)
    if count is not None:
        arr = arr[:count]
    return arr


def fvec_count(path: str | Path) -> int:
    path = Path(path)
    size = path.stat().st_size
    dim = fvec_dim(path)
    return size // (4 * (dim + 1))


def fvec_dim(path: str | Path) -> int:
    with Path(path).open("rb") as f:
        head = f.read(4)
        if len(head) < 4:
            raise ValueError(f"cannot read dim from {path}")
        return int(np.frombuffer(head, dtype=np.int32)[0])


def read_ivecs(path: str | Path, count: int | None = None) -> np.ndarray:
    """Read a .ivecs file into an int64 (n, width) array."""
    path = Path(path)
    rows: list[np.ndarray] = []
    with path.open("rb") as f:
        while True:
            head = f.read(4)
            if not head:
                break
            width = int(np.frombuffer(head, dtype=np.int32)[0])
            vals = np.frombuffer(f.read(width * 4), dtype=np.int32)
            if vals.size != width:
                raise ValueError(f"malformed ivecs: {path}")
            rows.append(vals.astype(np.int64))
            if count is not None and len(rows) >= count:
                break
    if not rows:
        return np.zeros((0, 0), dtype=np.int64)
    return np.vstack(rows)


def write_fvecs(path: str | Path, arr: np.ndarray) -> None:
    arr = np.ascontiguousarray(arr, dtype=np.float32)
    n, d = arr.shape
    with Path(path).open("wb") as f:
        for i in range(n):
            f.write(np.int32(d).tobytes())
            f.write(arr[i].tobytes())


def write_ivecs(path: str | Path, arr: np.ndarray) -> None:
    arr = np.ascontiguousarray(arr, dtype=np.int32)
    with Path(path).open("wb") as f:
        for row in arr:
            f.write(np.int32(row.size).tobytes())
            f.write(row.tobytes())


def write_tsv(path: str | Path, arr: np.ndarray) -> None:
    """Write a float32 (n, d) array as space-delimited NGT text input."""
    arr = np.ascontiguousarray(arr, dtype=np.float32)
    with Path(path).open("w") as f:
        for row in arr:
            f.write(" ".join(f"{v:.6g}" for v in row))
            f.write("\n")


def recall_at(gt: np.ndarray, pred: Iterable[Iterable[int]], k: int) -> float:
    """Recall@k averaged over queries. gt: (nq, width) int64, pred per-query ids."""
    hits = 0.0
    total = 0
    for i, (truth, row) in enumerate(zip(gt, pred)):
        truth_set = set(int(x) for x in truth[:k])
        hits += sum(1 for x in list(row)[:k] if int(x) in truth_set)
        total += k
    return hits / total if total else 0.0


def latency_stats(times_us: list[float]) -> dict[str, float]:
    if not times_us:
        return {"latency_mean_us": 0.0, "latency_p50_us": 0.0, "latency_p95_us": 0.0}
    arr = np.asarray(times_us, dtype=np.float64)
    return {
        "latency_mean_us": float(arr.mean()),
        "latency_p50_us": float(np.percentile(arr, 50)),
        "latency_p95_us": float(np.percentile(arr, 95)),
    }


def qps_from_times(times_us: list[float]) -> float:
    total = float(np.sum(times_us))
    return len(times_us) * 1e6 / total if total > 0 else 0.0


# --------------------------------------------------------------------------
# timer / process stats
# --------------------------------------------------------------------------


class Timer:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    def reset(self) -> None:
        self._start = time.perf_counter()

    @property
    def seconds(self) -> float:
        return time.perf_counter() - self._start

    @property
    def ms(self) -> float:
        return self.seconds * 1000.0


def peak_rss_mb() -> float:
    """Peak RSS of this process (and waited children) in MB."""
    self_max = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child_max = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return max(self_max, child_max) / 1024.0  # ru_maxrss is KiB on Linux


def dir_size_mb(path: str | Path) -> float:
    path = Path(path)
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total / (1024 * 1024)


def hardware_info() -> dict[str, Any]:
    cpu_flags: list[str] = []
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("flags"):
                cpu_flags = line.split(":", 1)[1].split()
                break
    simd = "avx512" if any(f.startswith("avx512") for f in cpu_flags) else (
        "avx2" if "avx2" in cpu_flags else "unknown"
    )
    with open("/proc/meminfo") as f:
        mem_kb = 0
        for line in f:
            if line.startswith("MemTotal"):
                mem_kb = int(line.split()[1])
                break
    return {
        "host": platform.node(),
        "cpu_model": platform.processor(),
        "cpu_cores": os.cpu_count() or 0,
        "simd": simd,
        "mem_total_gb": round(mem_kb / (1024 * 1024), 1),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def run_cmd(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [str(c) for c in cmd],
        env=full_env,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )


# --------------------------------------------------------------------------
# output bookkeeping
# --------------------------------------------------------------------------


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: str | Path, obj: Any) -> None:
    ensure_dir(Path(path).parent)
    with Path(path).open("w") as f:
        json.dump(obj, f, indent=2, default=str)


def read_json(path: str | Path) -> Any:
    with Path(path).open() as f:
        return json.load(f)


def last_json_line(text: str) -> str:
    """Return the last JSON object embedded in captured stdout.

    C/C++ progress bars use ``\\r`` without newlines, so the JSON printed by
    the worker may be glued to the tail of a progress line; scan the whole
    text for the last ``{...}`` block instead of requiring a line start.
    """
    import re

    for match in reversed(list(re.finditer(r"\{.*\}", text, re.DOTALL))):
        candidate = match.group(0)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON line found in output")


def append_csv_row(path: str | Path, row: dict[str, Any], columns: list[str]) -> None:
    ensure_dir(Path(path).parent)
    new_file = not Path(path).exists()
    with Path(path).open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


@dataclass
class RunContext:
    """Everything a system adapter needs to locate data and write outputs."""

    dataset: str
    data_root: Path = field(default_factory=lambda: ROOT / "data")
    out_root: Path = field(default_factory=lambda: ROOT / "results")
    suite: str = "03_system_fair"
    k: int = 10
    metric: str = "L2"
    threads: int = 64
    seed: int = 20260813
    val_queries: int = 1000
    repeats: int = 5
    git_commit: str = "not-a-git-repo"
    phase: str = "test"  # "test" | "val"
    full_test_queries: bool = False  # align with 02: final sweep uses ALL queries

    @property
    def base_path(self) -> Path:
        return self.data_root / self.dataset / f"{self.dataset}_base.fvecs"

    @property
    def query_path(self) -> Path:
        return self.data_root / self.dataset / f"{self.dataset}_query.fvecs"

    @property
    def gt_path(self) -> Path:
        return self.data_root / self.dataset / f"{self.dataset}_groundtruth.ivecs"

    @property
    def out_dir(self) -> Path:
        return self.out_root / self.suite / self.dataset

    @property
    def raw_dir(self) -> Path:
        return self.out_dir / "logs"

    @property
    def csv_dir(self) -> Path:
        return self.out_dir / "csv"

    @property
    def index_root(self) -> Path:
        return self.out_dir / "indexes"

    @property
    def figures_dir(self) -> Path:
        return self.out_dir / "figures"

    @property
    def audit_dir(self) -> Path:
        return self.out_dir / "audit"

    @property
    def manifest_dir(self) -> Path:
        return self.out_root / self.suite / self.dataset / "manifests"

    @property
    def shared_split_dir(self) -> Path:
        return self.csv_dir / "_query_splits"

    @property
    def val_query_path(self) -> Path:
        return self.shared_split_dir / "validation_query.fvecs"

    @property
    def val_gt_path(self) -> Path:
        return self.shared_split_dir / "validation_gt.ivecs"

    @property
    def test_query_path(self) -> Path:
        if self.phase == "val":
            return self.val_query_path
        if self.full_test_queries:
            return self.query_path
        return self.shared_split_dir / "test_query.fvecs"

    @property
    def test_gt_path(self) -> Path:
        if self.phase == "val":
            return self.val_gt_path
        if self.full_test_queries:
            return self.gt_path
        return self.shared_split_dir / "test_gt.ivecs"

    def prepare_query_splits(self) -> None:
        """Split queries once per dataset: first N for validation, rest for test."""
        ensure_dir(self.shared_split_dir)
        if self.test_gt_path.exists():
            return
        queries = read_fvecs(self.query_path)
        gt = read_ivecs(self.gt_path)
        n_val = min(self.val_queries, len(queries))
        if n_val >= len(queries):
            print(
                f"[warn] val_queries={self.val_queries} >= query_count={len(queries)}; "
                "test set is empty"
            )
        write_fvecs(self.val_query_path, queries[:n_val])
        write_ivecs(self.val_gt_path, gt[:n_val])
        write_fvecs(self.test_query_path, queries[n_val:])
        write_ivecs(self.test_gt_path, gt[n_val:])

    def system_dir(self, method: str) -> Path:
        return self.out_dir / method

    def raw_system_dir(self, method: str) -> Path:
        return self.raw_dir / method

    def index_system_dir(self, method: str) -> Path:
        return self.index_root / method

    def tuning_csv(self, method: str) -> Path:
        return self.csv_dir / "tuning" / f"{method}_validation_trials.csv"

    def selected_config_json(self, method: str) -> Path:
        return self.csv_dir / "tuning" / f"{method}_selected_config.json"

    def raw_log(
        self, method: str, config_id: str, search_param: str, repeat_id: int
    ) -> Path:
        safe_param = str(search_param).replace("/", "_").replace(":", "-")
        return (
            self.raw_system_dir(method)
            / f"{method}_{config_id}_{safe_param}_repeat{repeat_id}.log"
        )

    def method_log(self, method: str) -> Path:
        """Single unified log per method (all configs, search params, repeats)."""
        return self.raw_system_dir(method) / f"{method}.log"

    def build_record_path(self, method: str, config_id: str) -> Path:
        return (
            self.index_system_dir(method)
            / f"{method}_{config_id}_build.json"
        )


def base_row(ctx: RunContext, method: str) -> dict[str, Any]:
    hw = hardware_info()
    return {
        "suite": ctx.suite,
        "dataset": ctx.dataset,
        "method": method,
        "k": ctx.k,
        "metric": ctx.metric,
        "threads": ctx.threads,
        "query_count": 0,
        "git_commit": ctx.git_commit,
        "simd": hw["simd"],
        "cpu_model": hw["cpu_model"],
    }
