"""4 KiB page format and direct-I/O reader (plan section 4).

Layout
------
``index.pages`` is a flat, 4 KiB-sector file following DiskANN's disk layout.

* fixed-size node records < 4 KiB are packed as many per sector as fit; records
  never cross a sector and any tail bytes in the sector are left unused;
* fixed-size node records >= 4 KiB start at a 4 KiB boundary and occupy
  ``ceil(record_bytes / 4096)`` consecutive pages.

The reader always honours ``O_DIRECT`` (aligned offsets, lengths and buffers).
On filesystems without ``O_DIRECT`` support (tmpfs, some network mounts) a
``direct_ok=False`` fallback is used and recorded in the manifest; the formal
preflight rejects runs without direct I/O on the target NVMe.
"""

from __future__ import annotations

import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from .common import ALIGN, MAX_INFLIGHT, PAGE_SIZE


def align_up(value: int, alignment: int = ALIGN) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass
class NodeLayout:
    record_bytes: int
    count: int

    @property
    def records_per_page(self) -> int:
        return max(PAGE_SIZE // self.record_bytes, 1)

    @property
    def pages_per_record(self) -> int:
        return max((self.record_bytes + PAGE_SIZE - 1) // PAGE_SIZE, 1)

    def node_offset(self, node_id: int) -> int:
        if self.record_bytes < PAGE_SIZE:
            page = node_id // self.records_per_page
            slot = node_id % self.records_per_page
            return page * PAGE_SIZE + slot * self.record_bytes
        return node_id * self.pages_per_record * PAGE_SIZE

    def node_page(self, node_id: int) -> int:
        return self.node_offset(node_id) // PAGE_SIZE

    def node_span_pages(self, node_id: int) -> list[int]:
        """Pages physically touched by a record."""
        return self.node_pages(node_id)

    def node_pages(self, node_id: int) -> list[int]:
        npp = self.pages_per_record
        start = self.node_page(node_id)
        return list(range(start, start + npp))

    @property
    def total_pages(self) -> int:
        if self.record_bytes < PAGE_SIZE:
            return (self.count + self.records_per_page - 1) // self.records_per_page
        return self.count * self.pages_per_record

    def page_of_byte(self, offset: int) -> int:
        return offset // PAGE_SIZE

    def to_dict(self) -> dict:
        return {
            "record_bytes": self.record_bytes,
            "count": self.count,
            "records_per_page": self.records_per_page,
            "pages_per_record": self.pages_per_record,
            "total_pages": self.total_pages,
        }


class PageWriter:
    """Writes fixed-size records into 4 KiB sectors without record straddling."""

    def __init__(self, path: Path, layout: NodeLayout, aligned_padding: bool = True):
        self.path = path
        self.layout = layout
        self.aligned_padding = aligned_padding
        self._records: list[bytes] = []

    def append(self, record: bytes) -> int:
        rec = self.layout.record_bytes
        if len(record) > rec:
            raise ValueError(f"record too large: {len(record)} > {rec}")
        record = record.ljust(rec, b"\x00")
        self._records.append(record)
        return len(self._records) - 1

    def flush(self) -> int:
        pages = [bytearray(PAGE_SIZE) for _ in range(self.layout.total_pages)]
        for node_id, record in enumerate(self._records):
            offset = self.layout.node_offset(node_id)
            page = offset // PAGE_SIZE
            in_page = offset % PAGE_SIZE
            pages[page][in_page : in_page + self.layout.record_bytes] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"".join(bytes(page) for page in pages))
        return self.layout.total_pages * PAGE_SIZE


class PageWriterAligned:
    """Writer for records >= 4 KiB, one aligned record per page group."""

    def __init__(self, path: Path, layout: NodeLayout):
        self.path = path
        self.layout = layout
        self._pages: list[bytes] = []

    def append(self, record: bytes) -> int:
        rec = self.layout.record_bytes
        if len(record) > rec:
            raise ValueError(f"record too large: {len(record)} > {rec}")
        record = record.ljust(rec, b"\x00")
        npp = self.layout.pages_per_record
        group = record.ljust(npp * PAGE_SIZE, b"\x00")
        self._pages.append(group)
        return len(self._pages) - 1

    def flush(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"".join(self._pages))
        return self.layout.total_pages * PAGE_SIZE


@dataclass
class IOStats:
    """Per-query I/O accounting (plan section 10)."""

    requested_pages: int = 0
    duplicate_pages_removed: int = 0
    pre_coalesce_requests: int = 0
    coalesced_requests: int = 0
    io_requests: int = 0
    sectors_4k: int = 0
    bytes_read: int = 0
    io_wait_us: float = 0.0
    query_cache_hits: int = 0
    query_cache_misses: int = 0
    shared_cache_hits: int = 0
    shared_cache_misses: int = 0

    def merge(self, other: "IOStats") -> None:
        for name in (
            "requested_pages",
            "duplicate_pages_removed",
            "pre_coalesce_requests",
            "coalesced_requests",
            "io_requests",
            "sectors_4k",
            "bytes_read",
            "query_cache_hits",
            "query_cache_misses",
            "shared_cache_hits",
            "shared_cache_misses",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.io_wait_us += other.io_wait_us


class QueryPageCache:
    """Query-local page cache: pages stay alive for one query only."""

    __slots__ = ("_pages", "hits", "misses")

    def __init__(self) -> None:
        self._pages: dict[tuple[str, int], bytes] = {}
        self.hits = 0
        self.misses = 0

    def get(self, page: int, namespace: str = "default") -> bytes | None:
        data = self._pages.get((namespace, page))
        if data is None:
            self.misses += 1
            return None
        self.hits += 1
        return data

    def peek(self, page: int, namespace: str = "default") -> bytes | None:
        return self._pages.get((namespace, page))

    def put(self, page: int, data: bytes, namespace: str = "default") -> None:
        self._pages[(namespace, page)] = data

    def clear(self) -> None:
        self._pages.clear()


class SharedPageCache:
    """Bounded read-only cache shared across queries (BFS node cache, 05B/05C)."""

    def __init__(self, capacity_bytes: int):
        self.capacity_bytes = capacity_bytes
        self._pages: dict[tuple[str, int], bytes] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, page: int, namespace: str = "default") -> bytes | None:
        with self._lock:
            key = (namespace, page)
            data = self._pages.get(key)
            if data is None:
                self.misses += 1
                return None
            self.hits += 1
            # LRU touch: reinsert at the end
            self._pages.pop(key)
            self._pages[key] = data
            return data

    def peek(self, page: int, namespace: str = "default") -> bytes | None:
        with self._lock:
            return self._pages.get((namespace, page))

    def put(self, page: int, data: bytes, namespace: str = "default") -> None:
        with self._lock:
            key = (namespace, page)
            if len(self._pages) * PAGE_SIZE >= self.capacity_bytes and key not in self._pages:
                # evict oldest
                self._pages.pop(next(iter(self._pages)))
            self._pages[key] = data

    def clear(self) -> None:
        with self._lock:
            self._pages.clear()

    def size_bytes(self) -> int:
        with self._lock:
            return len(self._pages) * PAGE_SIZE


def plan_reads(
    pages: Iterable[int],
    query_cache: QueryPageCache | None,
    shared_cache: SharedPageCache | None,
    namespace: str = "default",
) -> tuple[list[int], list[int], IOStats]:
    """Deduplicate and coalesce page requests; returns (hit_pages, miss_pages, stats)."""
    stats = IOStats()
    unique = sorted(set(pages))
    stats.requested_pages = len(pages)
    stats.duplicate_pages_removed = len(pages) - len(unique)
    stats.pre_coalesce_requests = len(unique)

    hits: list[int] = []
    misses: list[int] = []
    for p in unique:
        if query_cache is not None and query_cache.get(p, namespace) is not None:
            hits.append(p)
            stats.query_cache_hits += 1
        elif shared_cache is not None and shared_cache.get(p, namespace) is not None:
            hits.append(p)
            stats.shared_cache_hits += 1
        else:
            misses.append(p)
            if query_cache is not None:
                stats.query_cache_misses += 1
            if shared_cache is not None:
                stats.shared_cache_misses += 1

    # Coalesce adjacent pages into runs.
    runs: list[tuple[int, int]] = []  # (start_page, run_len)
    for p in misses:
        if runs and p == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((p, 1))
    stats.coalesced_requests = len(misses) - len(runs)
    stats.io_requests = len(runs)
    stats.sectors_4k = sum(n for _, n in runs)
    stats.bytes_read = stats.sectors_4k * PAGE_SIZE
    return hits, misses, stats


class DirectPageReader:
    """Batched, 4 KiB-aligned page reader with optional O_DIRECT."""

    def __init__(
        self,
        path: Path,
        direct: bool = True,
        max_inflight: int = MAX_INFLIGHT,
        strict_direct: bool = False,
    ):
        self.path = Path(path)
        self.cache_namespace = str(self.path.resolve())
        self.max_inflight = max_inflight
        self.direct_ok = False
        self.fallback_reason: str | None = None
        flags = os.O_RDONLY
        if direct and hasattr(os, "O_DIRECT"):
            flags |= getattr(os, "O_DIRECT", 0)
        try:
            self._fd = os.open(str(self.path), flags)
            self.direct_ok = bool(direct and hasattr(os, "O_DIRECT"))
        except OSError as exc:
            if strict_direct:
                raise
            self.fallback_reason = str(exc)
            self._fd = os.open(str(self.path), os.O_RDONLY)
        if not direct:
            self.fallback_reason = "direct I/O explicitly disabled"
        self._pool = ThreadPoolExecutor(max_workers=min(16, self.max_inflight))

    def close(self) -> None:
        self._pool.shutdown(wait=True)
        os.close(self._fd)

    def __enter__(self) -> "DirectPageReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read_aligned(self, offset: int, size: int) -> bytes:
        """Read ``size`` bytes at an aligned offset into an aligned buffer."""
        size = align_up(size, ALIGN)
        # mmap buffers are page-aligned, satisfying O_DIRECT alignment.
        import mmap

        buf = mmap.mmap(-1, size)
        try:
            nread = os.preadv(self._fd, [buf], offset)
            if nread != size:
                got = os.fstat(self._fd).st_size - offset
                raise OSError(f"short read at offset {offset}: got {nread}, want {size}, file_left={got}")
            return bytes(buf[:size])
        finally:
            buf.close()

    def read_pages(
        self,
        pages: Iterable[int],
        query_cache: QueryPageCache | None = None,
        shared_cache: SharedPageCache | None = None,
        stats: IOStats | None = None,
    ) -> dict[int, bytes]:
        """Read requested pages; caches filled for both hit sources."""
        import time

        stats = stats or IOStats()
        page_list = list(pages)
        hits, misses, plan_stats = plan_reads(
            page_list, query_cache, shared_cache, self.cache_namespace
        )
        stats.merge(plan_stats)
        out: dict[int, bytes] = {}
        for p in hits:
            data = query_cache.peek(p, self.cache_namespace) if query_cache is not None else None
            if data is None and shared_cache is not None:
                data = shared_cache.peek(p, self.cache_namespace)
            out[p] = data

        if not misses:
            return out

        # Build runs of consecutive pages.
        runs: list[tuple[int, int]] = []
        for p in misses:
            if runs and p == runs[-1][0] + runs[-1][1]:
                runs[-1] = (runs[-1][0], runs[-1][1] + 1)
            else:
                runs.append((p, 1))

        start = time.perf_counter()
        for chunk_start in range(0, len(runs), self.max_inflight):
            chunk = runs[chunk_start : chunk_start + self.max_inflight]
            results = list(
                self._pool.map(
                    lambda run: (
                        run[0],
                        self._read_aligned(run[0] * PAGE_SIZE, run[1] * PAGE_SIZE),
                    ),
                    chunk,
                )
            )
            for page_no, data in results:
                # split run into individual pages for the caches
                n = len(data) // PAGE_SIZE
                for i in range(n):
                    piece = data[i * PAGE_SIZE : (i + 1) * PAGE_SIZE]
                    pno = page_no + i
                    out[pno] = piece
                    if query_cache is not None:
                        query_cache.put(pno, piece, self.cache_namespace)
                    if shared_cache is not None:
                        shared_cache.put(pno, piece, self.cache_namespace)
        stats.io_wait_us += (time.perf_counter() - start) * 1e6
        return out


class MemoryPageReader:
    """Serves pages from RAM (parity backend for memory-reader runs)."""

    direct_ok = False

    def __init__(self, path: Path):
        self.path = Path(path)
        self.cache_namespace = str(self.path.resolve())
        data = self.path.read_bytes()
        if len(data) % PAGE_SIZE:
            raise ValueError("page file not aligned")
        self._pages = {
            i: data[i * PAGE_SIZE : (i + 1) * PAGE_SIZE] for i in range(len(data) // PAGE_SIZE)
        }

    def read_pages(
        self,
        pages: Iterable[int],
        query_cache: QueryPageCache | None = None,
        shared_cache: SharedPageCache | None = None,
        stats: IOStats | None = None,
    ) -> dict[int, bytes]:
        stats = stats or IOStats()
        hits, misses, plan_stats = plan_reads(
            pages, query_cache, shared_cache, self.cache_namespace
        )
        stats.merge(plan_stats)
        out: dict[int, bytes] = {}
        for p in misses:
            out[p] = self._pages[p]
            if query_cache is not None:
                query_cache.put(p, out[p], self.cache_namespace)
            if shared_cache is not None:
                shared_cache.put(p, out[p], self.cache_namespace)
        for p in hits:
            data = query_cache.peek(p, self.cache_namespace) if query_cache is not None else None
            if data is None and shared_cache is not None:
                data = shared_cache.peek(p, self.cache_namespace)
            out[p] = data
        return out

    def close(self) -> None:
        pass

    def __enter__(self) -> "MemoryPageReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def extract_node(
    data: dict[int, bytes],
    layout: NodeLayout,
    node_id: int,
) -> bytes:
    """Reassemble a node record from its page(s)."""
    if layout.record_bytes < PAGE_SIZE:
        offset = layout.node_offset(node_id)
        page = layout.node_page(node_id)
        in_page = offset % PAGE_SIZE
        rec = data.get(page)
        if rec is None:
            raise KeyError(f"missing page {page} for node {node_id}")
        return rec[in_page : in_page + layout.record_bytes]
    pages = layout.node_pages(node_id)
    parts = []
    for p in pages:
        rec = data.get(p)
        if rec is None:
            raise KeyError(f"missing page {p} for node {node_id}")
        parts.append(rec)
    return b"".join(parts)[: layout.record_bytes]


def nodes_for_pages(layout: NodeLayout, pages: Iterable[int]) -> list[int]:
    """Map page numbers back to the node ids stored in them (packed layout)."""
    result: list[int] = []
    for p in pages:
        if layout.record_bytes < PAGE_SIZE:
            per = layout.records_per_page
            first = p * per
            result.extend(range(first, min(first + per, layout.count)))
        else:
            first = p // layout.pages_per_record
            result.append(first)
    return sorted(set(result))


def load_packed_codes(path: Path, record_bytes: int, count: int) -> np.ndarray:
    """Load fixed-size records from a DiskANN-style packed page file."""
    data = Path(path).read_bytes()
    layout = NodeLayout(record_bytes=record_bytes, count=count)
    codes = np.empty((count, record_bytes), dtype=np.uint8)
    for i in range(count):
        offset = layout.node_offset(i)
        codes[i] = np.frombuffer(data[offset : offset + record_bytes], dtype=np.uint8)
    return codes


def extract_record_from_pages(
    pages: dict[int, bytes],
    layout: NodeLayout,
    node_id: int,
) -> bytes:
    """Extract a packed or aligned record from a page dict."""
    return extract_node(pages, layout, node_id)


def record_stats_from_pages(
    pages_read: dict[int, bytes],
    requested_pages: int,
    layout: NodeLayout,
    stats: IOStats,
) -> IOStats:
    """Fill in derived counters after a batched read."""
    return stats


def page_bytes_for_records(record_bytes: int, count: int) -> int:
    """Total page-file bytes for a fixed-size record layout."""
    layout = NodeLayout(record_bytes=record_bytes, count=count)
    return layout.total_pages * PAGE_SIZE


def io_read_amplification(bytes_read: int, needed_bytes: int) -> float:
    return bytes_read / max(needed_bytes, 1)


def pack_record(fields: list[bytes]) -> bytes:
    return b"".join(fields)


def parse_record(record: bytes, sizes: list[int]) -> list[bytes]:
    out: list[bytes] = []
    off = 0
    for s in sizes:
        out.append(record[off : off + s])
        off += s
    return out


def encode_np(values: np.ndarray) -> bytes:
    return np.ascontiguousarray(values).tobytes()


def decode_np(buf: bytes, dtype: np.dtype, count: int) -> np.ndarray:
    return np.frombuffer(buf, dtype=dtype, count=count).copy()


def np_align_buffer(size: int) -> memoryview:
    """Return an aligned writable buffer for O_DIRECT staging (tests)."""
    import mmap

    m = mmap.mmap(-1, align_up(size, ALIGN))
    return memoryview(m)
