"""Shared constants, paths, hashing, query splits and statistics helpers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import shutil
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .dataset_policy import validation_query_count

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
RESULTS_ROOT = REPO_ROOT / "results"
WORK_ROOT = REPO_ROOT / "work" / "05_disk_system_fair"
DEFAULT_DISK_ROOT = WORK_ROOT / "disk_root"

SEED = 20260813
PAGE_SIZE = 4096
ALIGN = 4096
MAX_INFLIGHT = 128
K = 10
FORMAL_METHODS_05A = ("PQ_4bit", "SQ_4bit", "SAQ_B4", "Ours_RaBitQ_K1")
FORMAL_METHODS_05B = (
    "PQ-DiskANN-Disk",
    "SQ-DiskANN-Disk",
    "SAQ-DiskANN-Disk",
    "Ours-Disk",
)
FORMAL_SYSTEMS_05C = (
    "Ours-Disk",
    "SymphonyQG-DiskPort",
    "OG-LVQ-DiskPort",
    "Glass-NSG-DiskPort",
    "DiskANN-PQ-Disk",
)
FORMAT_MAGIC = b"QGRAPH05"
FORMAT_VERSION = 1

# Unified per-query statistic fields (plan section 10).
QUERY_STAT_FIELDS = [
    "layer",
    "storage_mode",
    "dataset",
    "method",
    "config_id",
    "repeat_id",
    "query_id",
    "search_width",
    "beam_width",
    "workers",
    "search_dram_budget_gib",
    "cache_nodes",
    "resident_bytes",
    "cache_bytes",
    "peak_rss_bytes",
    "recall_at_10",
    "fixed_candidate_recall_at_10",
    "mean_relative_error",
    "p95_relative_error",
    "pairwise_flip_rate",
    "latency_us",
    "query_prep_us",
    "queue_compute_us",
    "io_wait_us",
    "distance_compute_us",
    "rerank_us",
    "visited_nodes",
    "distance_evaluations",
    "io_requests",
    "sectors_4k",
    "bytes_read",
    "average_read_bytes",
    "coalesced_requests",
    "duplicate_pages_removed",
    "shared_cache_hits",
    "shared_cache_misses",
    "query_cache_hits",
    "query_cache_misses",
    # Ours-specific counters
    "db1_checks",
    "db1_survivors",
    "full4_candidates",
    "full4_page_reads",
    "rerank_candidates",
    "rerank_page_reads",
]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_repo_path(path: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve CLI paths from cwd first, then from the repository root.

    The local launch scripts pass repository-relative paths.  This keeps those
    paths stable even when the entry point is invoked from the parent workspace.
    """
    path = path.expanduser()
    if path.is_absolute():
        return path.resolve()
    if path.exists():
        return path.resolve()
    return (repo_root / path).resolve()


def resolve_executable(argv0: str, repo_root: Path = REPO_ROOT) -> str:
    path = Path(argv0).expanduser()
    if path.is_absolute():
        resolved = path
    elif os.path.sep in argv0:
        cwd_candidate = path.resolve()
        repo_candidate = (repo_root / path).resolve()
        resolved = cwd_candidate if cwd_candidate.exists() else repo_candidate
    else:
        found = shutil.which(argv0)
        resolved = Path(found).resolve() if found else path
    if not resolved.exists():
        raise FileNotFoundError(f"executable not found: {argv0}")
    if not os.access(resolved, os.X_OK):
        raise PermissionError(f"not executable: {resolved}")
    return str(resolved)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def fvecs(path: Path, mmap_mode: str | None = None) -> np.ndarray:
    """Read an fvecs file; with mmap_mode='r' avoids loading into RAM."""
    with path.open("rb") as f:
        dim = int(np.fromfile(f, dtype=np.int32, count=1)[0])
    n = path.stat().st_size // (4 * (dim + 1))
    if mmap_mode:
        raw = np.memmap(path, dtype=np.int32, mode=mmap_mode)
    else:
        raw = np.fromfile(path, dtype=np.int32)
    raw = raw.reshape(n, dim + 1)
    return raw[:, 1:].copy().view(np.float32).reshape(n, dim)


def ivecs(path: Path) -> np.ndarray:
    with path.open("rb") as f:
        dim = int(np.fromfile(f, dtype=np.int32, count=1)[0])
    n = path.stat().st_size // (4 * (dim + 1))
    raw = np.fromfile(path, dtype=np.int32).reshape(n, dim + 1)
    return raw[:, 1:].copy()


def write_fvecs(path: Path, arr: np.ndarray) -> None:
    ensure_dir(path.parent)
    arr = np.ascontiguousarray(arr, dtype=np.float32)
    n, d = arr.shape
    with path.open("wb") as f:
        for i in range(n):
            f.write(struct.pack("<i", d))
            f.write(arr[i].tobytes())


def write_ivecs(path: Path, arr: np.ndarray) -> None:
    ensure_dir(path.parent)
    arr = np.ascontiguousarray(arr, dtype=np.int32)
    n, k = arr.shape
    with path.open("wb") as f:
        for i in range(n):
            f.write(struct.pack("<i", k))
            f.write(arr[i].tobytes())


def query_split_candidates(dataset: str) -> tuple[Path, ...]:
    return (REPO_ROOT / "artifacts" / "query_splits" / dataset / "shared",)



def vecs_shape(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        raw = stream.read(4)
    if len(raw) != 4:
        raise ValueError(f"empty vecs file: {path}")
    dimension = struct.unpack("<i", raw)[0]
    row_bytes = 4 * (dimension + 1)
    size = path.stat().st_size
    if dimension <= 0 or size % row_bytes:
        raise ValueError(f"malformed vecs file: {path}")
    return size // row_bytes, dimension


def vecs_count(path: Path) -> int:
    return vecs_shape(path)[0]


def prepare_query_splits(
    dataset: str,
    data_root: Path,
    out_root: Path,
    val_queries: int | None = None,
) -> dict[str, Path]:
    """Reuse the 03 system-fair query splits when present, else create them.

    The 03 suite stores splits at
    ``artifacts/query_splits/<dataset>/shared``
    (validation = first ``val_queries``, test = the rest). The 05 suite reuses
    exactly those files so all experiments share the same query partition.
    """
    query_path = data_root / dataset / f"{dataset}_query.fvecs"
    total_queries = vecs_count(query_path)
    val_queries = validation_query_count(dataset, total_queries, val_queries)
    required_split_files = (
        "validation_query.fvecs",
        "validation_gt.ivecs",
        "test_query.fvecs",
        "test_gt.ivecs",
    )
    for legacy in query_split_candidates(dataset):
        if not all((legacy / name).exists() for name in required_split_files):
            continue
        validation_count = vecs_count(legacy / "validation_query.fvecs")
        test_count = vecs_count(legacy / "test_query.fvecs")
        if validation_count != val_queries or validation_count + test_count != total_queries:
            raise ValueError(
                f"stale query split {legacy}: validation={validation_count}, "
                f"test={test_count}, expected validation={val_queries}, total={total_queries}"
            )
        return {
            "validation_query": legacy / "validation_query.fvecs",
            "validation_gt": legacy / "validation_gt.ivecs",
            "test_query": legacy / "test_query.fvecs",
            "test_gt": legacy / "test_gt.ivecs",
        }
    split_dir = out_root / dataset
    ensure_dir(split_dir)
    test_gt = split_dir / "test_gt.ivecs"
    if test_gt.exists():
        validation_count = vecs_count(split_dir / "validation_query.fvecs")
        test_count = vecs_count(split_dir / "test_query.fvecs")
        if validation_count != val_queries or validation_count + test_count != total_queries:
            raise ValueError(
                f"stale query split {split_dir}: validation={validation_count}, "
                f"test={test_count}, expected validation={val_queries}, total={total_queries}"
            )
        return {
            "validation_query": split_dir / "validation_query.fvecs",
            "validation_gt": split_dir / "validation_gt.ivecs",
            "test_query": split_dir / "test_query.fvecs",
            "test_gt": test_gt,
        }
    q = fvecs(query_path)
    gt = ivecs(data_root / dataset / f"{dataset}_groundtruth.ivecs")
    val_q, test_q = q[:val_queries], q[val_queries:]
    val_gt, test_gt_arr = gt[:val_queries], gt[val_queries:]
    write_fvecs(split_dir / "validation_query.fvecs", val_q)
    write_ivecs(split_dir / "validation_gt.ivecs", val_gt)
    write_fvecs(split_dir / "test_query.fvecs", test_q)
    write_ivecs(split_dir / "test_gt.ivecs", test_gt_arr)
    return {
        "validation_query": split_dir / "validation_query.fvecs",
        "validation_gt": split_dir / "validation_gt.ivecs",
        "test_query": split_dir / "test_query.fvecs",
        "test_gt": test_gt,
    }


def dataset_paths(dataset: str, data_root: Path = DATA_ROOT) -> dict[str, Path]:
    return {
        "base": data_root / dataset / f"{dataset}_base.fvecs",
        "query": data_root / dataset / f"{dataset}_query.fvecs",
        "gt": data_root / dataset / f"{dataset}_groundtruth.ivecs",
    }


def random_query_order(n: int, seed: int = SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    order = np.arange(n)
    rng.shuffle(order)
    return order


def hardware_info() -> dict[str, Any]:
    info = {
        "platform": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": platform.node(),
    }
    try:
        import psutil

        vm = psutil.virtual_memory()
        info["ram_total_bytes"] = vm.total
    except Exception:
        info["ram_total_bytes"] = None
    try:
        cpu_count = os.cpu_count()
    except Exception:
        cpu_count = None
    info["cpu_count"] = cpu_count
    return info


def fs_info(path: Path) -> dict[str, Any]:
    st = os.statvfs(path)
    return {
        "path": str(path),
        "total_bytes": st.f_blocks * st.f_frsize,
        "free_bytes": st.f_bavail * st.f_frsize,
        "block_size": st.f_frsize,
    }


def lsblk_rotational(path: Path) -> bool | None:
    """Return True when the backing device is rotational (HDD), False for NVMe.

    Resolves the device behind *path* via /proc/self/mountinfo and reads
    /sys/block/<dev>/queue/rotational. Returns None when it cannot be resolved.
    """
    try:
        st = os.stat(path)
        sys_dev = Path(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}")
        if not sys_dev.exists():
            return None
        device = sys_dev.resolve()
        candidates = [device, *device.parents]
        for candidate in candidates:
            rotational = candidate / "queue" / "rotational"
            if rotational.exists():
                return rotational.read_text().strip() == "1"
        # Device-mapper/RAID targets expose rotational state on their slaves.
        slaves = device / "slaves"
        if slaves.exists():
            values = []
            for slave in slaves.iterdir():
                rotational = slave.resolve() / "queue" / "rotational"
                if rotational.exists():
                    values.append(rotational.read_text().strip() == "1")
            if values:
                return any(values)
        return None
    except Exception:
        return None


def run_preflight(disk_root: Path) -> dict[str, Any]:
    """NVMe / O_DIRECT preflight (plan section 9)."""
    ensure_dir(disk_root)
    probe = disk_root / ".odirect_probe.bin"
    report: dict[str, Any] = {"disk_root": str(disk_root)}
    report["fs"] = fs_info(disk_root)
    report["rotational"] = lsblk_rotational(disk_root)
    report["free_gib"] = report["fs"]["free_bytes"] / (1 << 30)
    report["sufficient_space_200gib"] = report["free_gib"] >= 200
    # O_DIRECT probe: 4 KiB aligned buffer, aligned offset.
    fd = None
    try:
        import mmap

        if not hasattr(os, "O_DIRECT"):
            raise OSError("this platform does not expose O_DIRECT")
        seed_fd = os.open(str(probe), os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o644)
        try:
            os.ftruncate(seed_fd, PAGE_SIZE)
        finally:
            os.close(seed_fd)
        fd = os.open(str(probe), os.O_RDWR | os.O_DIRECT)
        write_buf = mmap.mmap(-1, PAGE_SIZE)
        read_buf = mmap.mmap(-1, PAGE_SIZE)
        try:
            write_buf[:] = b"Q" * PAGE_SIZE
            written = os.pwritev(fd, [write_buf], 0)
            read = os.preadv(fd, [read_buf], 0)
            report["odirect"] = bool(
                written == PAGE_SIZE
                and read == PAGE_SIZE
                and read_buf[:16] == b"Q" * 16
            )
        finally:
            write_buf.close()
            read_buf.close()
    except OSError as exc:
        report["odirect"] = False
        report["odirect_error"] = str(exc)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            probe.unlink()
        except OSError:
            pass
    return report


@dataclass
class DRAMBudget:
    budget_gib: float
    resident_bytes: int = 0
    codebook_bytes: int = 0
    worker_scratch_bytes: int = 0
    cache_bytes: int = 0
    cache_nodes: int = 0
    node_cached_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return (
            self.resident_bytes
            + self.codebook_bytes
            + self.worker_scratch_bytes
            + self.cache_bytes
        )

    @property
    def budget_bytes(self) -> int:
        return int(self.budget_gib * (1 << 30))

    @property
    def ok(self) -> bool:
        return self.total_bytes <= self.budget_bytes

    def compute_cache(
        self,
        base_count: int,
        cached_node_bytes: int,
        max_workers: int,
    ) -> None:
        """Auto-size BFS node cache: C <= 10% N, total <= B (plan 3.4)."""
        self.node_cached_bytes = cached_node_bytes
        fixed = self.resident_bytes + self.codebook_bytes + self.worker_scratch_bytes
        headroom = self.budget_bytes - fixed
        if headroom <= 0:
            self.cache_nodes = 0
            self.cache_bytes = 0
            return
        max_by_budget = headroom // max(cached_node_bytes, 1)
        max_by_pct = int(base_count * 0.10)
        self.cache_nodes = min(max_by_budget, max_by_pct)
        self.cache_bytes = self.cache_nodes * cached_node_bytes

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QueryRecord:
    """One row of the unified per-query raw output."""

    layer: str = ""
    storage_mode: str = ""
    dataset: str = ""
    method: str = ""
    config_id: str = ""
    repeat_id: int = 0
    query_id: int = 0
    search_width: int = 0
    beam_width: int = 0
    workers: int = 1
    search_dram_budget_gib: float = 0.0
    cache_nodes: int = 0
    resident_bytes: int = 0
    cache_bytes: int = 0
    peak_rss_bytes: int = 0
    recall_at_10: float = 0.0
    fixed_candidate_recall_at_10: float = 0.0
    mean_relative_error: float = 0.0
    p95_relative_error: float = 0.0
    pairwise_flip_rate: float = 0.0
    latency_us: float = 0.0
    query_prep_us: float = 0.0
    queue_compute_us: float = 0.0
    io_wait_us: float = 0.0
    distance_compute_us: float = 0.0
    rerank_us: float = 0.0
    visited_nodes: int = 0
    distance_evaluations: int = 0
    io_requests: int = 0
    sectors_4k: int = 0
    bytes_read: int = 0
    average_read_bytes: float = 0.0
    coalesced_requests: int = 0
    duplicate_pages_removed: int = 0
    shared_cache_hits: int = 0
    shared_cache_misses: int = 0
    query_cache_hits: int = 0
    query_cache_misses: int = 0
    db1_checks: int = 0
    db1_survivors: int = 0
    full4_candidates: int = 0
    full4_page_reads: int = 0
    rerank_candidates: int = 0
    rerank_page_reads: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = {}
        for f in QUERY_STAT_FIELDS:
            row[f] = getattr(self, f)
        row.update(self.extra)
        return row


def percentile(values: Iterable[float], p: float) -> float:
    arr = np.asarray(list(values), dtype=np.float64)
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, p))


def recall_at_k(pred: np.ndarray, truth: np.ndarray, k: int = K) -> float:
    if pred.size == 0:
        return 0.0
    return float(np.isin(pred[:k], truth[:k]).sum()) / k


def mean_p95_relative_error(dist_approx: np.ndarray, dist_exact: np.ndarray) -> tuple[float, float]:
    """Match the 01 suite: skip distances at or below the 1e-6 floor."""
    dist_exact = np.asarray(dist_exact, dtype=np.float64)
    dist_approx = np.asarray(dist_approx, dtype=np.float64)
    mask = dist_exact > 1e-6
    if not mask.any():
        return 0.0, 0.0
    rel = np.abs(dist_approx[mask] - dist_exact[mask]) / dist_exact[mask]
    return float(rel.mean()), float(np.percentile(rel, 95))


def pairwise_flip_rate(top_exact: np.ndarray, top_approx: np.ndarray, k: int = 10) -> float:
    """Fraction of pairwise ordering differences inside the top-k ID set."""
    if k < 2:
        return 0.0
    a = list(top_exact[:k])
    b = list(top_approx[:k])
    common = set(a) & set(b)
    if len(common) < 2:
        return 0.0
    pos_a = {v: i for i, v in enumerate(a) if v in common}
    pos_b = {v: i for i, v in enumerate(b) if v in common}
    pairs = 0
    flips = 0
    for x in common:
        for y in common:
            if x < y:
                pairs += 1
                if (pos_a[x] < pos_a[y]) != (pos_b[x] < pos_b[y]):
                    flips += 1
    return flips / pairs if pairs else 0.0


def peak_rss_bytes() -> int:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def write_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    ensure_dir(path.parent)
    if not rows:
        path.write_text("")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                fields.append(k)
    append = path.exists() and path.stat().st_size > 0
    with path.open("a" if append else "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not append:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


def write_rows_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def median_of_repeats(rows: list[dict[str, Any]], by: tuple[str, ...]) -> list[dict[str, Any]]:
    """Median across repeat_id per (by...) group; keeps latency metrics median-wise."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = tuple(r[k] for k in by)
        groups.setdefault(key, []).append(r)
    out = []
    numeric = [
        "latency_us",
        "query_prep_us",
        "io_wait_us",
        "distance_compute_us",
        "rerank_us",
        "recall_at_10",
        "fixed_candidate_recall_at_10",
        "mean_relative_error",
        "p95_relative_error",
        "pairwise_flip_rate",
        "io_requests",
        "sectors_4k",
        "bytes_read",
    ]
    for key, group in groups.items():
        base = dict(group[0])
        for r in group[1:]:
            base.update({k: v for k, v in r.items() if k not in by})
        for name in numeric:
            vals = [float(r.get(name, float("nan"))) for r in group]
            vals = [v for v in vals if v == v]
            if vals:
                base[name] = float(np.median(vals))
        out.append(base)
    return out


def write_method_logs(
    layer: str,
    dataset: str,
    out_root: Path,
    rows: list[dict[str, Any]],
    *,
    ef_field: str = "search_width",
    split_fields: tuple[str, ...] = (),
    metric_fields: tuple[str, ...] = (
        "recall_at_10",
        "latency_us",
        "io_requests",
        "sectors_4k",
        "bytes_read",
        "db1_checks",
        "db1_survivors",
        "full4_page_reads",
        "rerank_page_reads",
    ),
    header_extra: dict[str, Any] | None = None,
    style: str = "01",
) -> list[Path]:
    """Write per-method, per-ef human-readable logs like the 01 suite.

    Logs land under ``results/05_disk_system_fair/<layer>/<dataset>/logs/<method>/``
    with one line per ef (search width) so the plotting scripts can use ef as
    the x-axis:

        <method> ef=40 recall=0.8 qps=123.4 latency_p95_us=... io_requests=... ...
    """
    layer_map = {
        "05A": "05A_disk_quantizer_io",
        "05B": "05B_diskann_shared_graph",
        "05C": "05C_disk_system_fair",
    }
    base = out_root / layer_map.get(layer, layer) / dataset / "logs"
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        method_key = r.get("method", "?")
        key = (method_key,) + (
            tuple(r.get(f) for f in split_fields) if split_fields else ()
        )
        groups.setdefault(key, []).append(r)
    # de-duplicate repeated runs: keep the newest row per (ef, query_id)
    for key in groups:
        latest: dict[tuple, dict] = {}
        for r in groups[key]:
            dedup_key = (r.get(ef_field), r.get("query_id"))
            latest[dedup_key] = r
        groups[key] = list(latest.values())

    written: list[Path] = []
    for key, group in groups.items():
        method = str(key[0])
        suffix = "_".join(str(v) for v in key[1:] if v)
        suffix = suffix.replace("/", "-")
        log_dir = ensure_dir(base / method)
        log_name = f"{method}{'_' + suffix if suffix else ''}.log"
        log_path = log_dir / log_name
        # aggregate per ef: mean recall, p95 latency (matching the 01 log style)
        med: list[dict[str, Any]] = []
        by_ef: dict[Any, list[dict[str, Any]]] = {}
        for r in group:
            by_ef.setdefault(r.get(ef_field), []).append(r)
        for ef, rows in by_ef.items():
            base_row = dict(rows[0])
            base_row[ef_field] = ef
            qps_vals = [
                float(x.get("aggregate_qps", 0.0) or 0.0)
                for x in rows
                if float(x.get("aggregate_qps", 0.0) or 0.0) > 0
            ]
            base_row["qps"] = float(np.mean(qps_vals)) if qps_vals else 0.0
            for name in ("recall_at_10",):
                vals = [float(r.get(name, float("nan"))) for r in rows]
                vals = [v for v in vals if v == v]
                base_row[name] = float(np.mean(vals)) if vals else float("nan")
            for name in ("latency_us",):
                vals = [float(r.get(name, float("nan"))) for r in rows]
                vals = [v for v in vals if v == v]
                base_row[name] = (
                    float(np.percentile(vals, 95)) if vals else float("nan")
                )
            for name in metric_fields:
                vals = [float(r.get(name, float("nan"))) for r in rows]
                vals = [v for v in vals if v == v]
                if vals:
                    base_row[name] = float(np.mean(vals))
            for name in (
                "query_prep_us",
                "io_wait_us",
                "distance_compute_us",
                "queue_compute_us",
                "rerank_us",
                "visited_nodes",
                "distance_evaluations",
            ):
                vals = [float(r.get(name, float("nan"))) for r in rows]
                vals = [v for v in vals if v == v]
                if vals:
                    base_row[name] = float(np.mean(vals))
            lat_all = [
                float(r.get("latency_us", float("nan"))) for r in rows
            ]
            lat_all = [v for v in lat_all if v == v]
            base_row["latency_mean_us"] = (
                float(np.mean(lat_all)) if lat_all else float("nan")
            )
            med.append(base_row)
        med.sort(key=lambda x: int(x.get(ef_field, 0) or 0))
        with log_path.open("w") as f:
            if style == "02":
                f.write(f"suite=05_{layer}\n")
                f.write(f"dataset={dataset}\n")
                f.write(f"method={method}\n")
                for k, v in (header_extra or {}).items():
                    f.write(f"{k}={v}\n")
                for r in med:
                    ef = r.get(ef_field, "?")
                    recall = float(r.get("recall_at_10", float("nan")))
                    lat_mean = float(r.get("latency_mean_us", float("nan")))
                    lat_p95 = float(r.get("latency_us", float("nan")))
                    qps = float(r.get("qps", 0.0) or 0.0)
                    if qps <= 0.0 and lat_mean > 0.0:
                        qps = 1e6 / lat_mean
                    beam = int(r.get("beam_width", 1) or 1)
                    traverse = (
                        float(r.get("io_wait_us", 0.0) or 0.0)
                        + float(r.get("distance_compute_us", 0.0) or 0.0)
                        + float(r.get("queue_compute_us", 0.0) or 0.0)
                    )
                    parts = [
                        f"{method}",
                        f"{ef}",
                        f"{recall:.6f}",
                        f"{lat_mean:.6f}",
                        "us",
                        f"search_list_size={ef}",
                        f"efSearch={ef}",
                        f"search_beam_width={beam}",
                        f"total_us_per_query={lat_mean:.6f}",
                        f"qps={qps:.6f}",
                        f"p95_us={lat_p95:.6f}",
                        f"prepare_us={float(r.get('query_prep_us', 0.0) or 0.0):.6f}",
                        f"traverse_us={traverse:.6f}",
                        f"rerank_us={float(r.get('rerank_us', 0.0) or 0.0):.6f}",
                        f"visited_nodes={float(r.get('visited_nodes', 0.0) or 0.0):.4f}",
                        f"distance_computations={float(r.get('distance_evaluations', 0.0) or 0.0):.4f}",
                    ]
                    for mf in (
                        "db1_checks",
                        "db1_survivors",
                        "full4_page_reads",
                        "io_requests",
                        "sectors_4k",
                        "bytes_read",
                    ):
                        v = r.get(mf)
                        if v is None or v == "":
                            continue
                        parts.append(f"{mf}={float(v):.4g}")
                    f.write(" ".join(parts) + "\n")
            else:
                f.write(f"# {layer} {dataset} method={method}\n")
                for k, v in (header_extra or {}).items():
                    f.write(f"# {k}={v}\n")
                fields = [
                    "recall",
                    "qps",
                    "latency_p95_us",
                    "latency_mean_us",
                ] + [m for m in metric_fields if m not in ("recall_at_10", "latency_us")]
                for r in med:
                    ef = r.get(ef_field, "?")
                    recall = float(r.get("recall_at_10", float("nan")))
                    lat_us = float(r.get("latency_us", float("nan")))
                    qps = float(r.get("qps", 0.0) or 0.0)
                    if qps <= 0.0 and lat_us > 0.0:
                        qps = 1e6 / lat_us
                    vals = [f"{method}", f"ef={ef}", f"recall={recall:.6f}", f"qps={qps:.1f}"]
                    vals.append(f"latency_p95_us={lat_us:.1f}")
                    vals.append(f"latency_mean_us={lat_us:.1f}")
                    for mf in fields:
                        if mf in ("recall", "qps", "latency_p95_us", "latency_mean_us"):
                            continue
                        v = r.get(mf)
                        if v is None or v == "":
                            continue
                        try:
                            vals.append(f"{mf}={float(v):.4g}")
                        except (TypeError, ValueError):
                            vals.append(f"{mf}={v}")
                    f.write(" ".join(vals) + "\n")
        written.append(log_path)
    return written


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
