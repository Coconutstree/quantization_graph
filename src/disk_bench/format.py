"""Unified disk index format: ``index.pages`` + ``resident.bin`` + ``index.meta.json``."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .common import (
    FORMAT_MAGIC,
    FORMAT_VERSION,
    sha256_bytes,
    sha256_file,
    write_json,
)
from .diskio import NodeLayout, PageWriter, PageWriterAligned


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class IndexBuilder:
    """Builds ``index.pages``, ``resident.bin`` and ``index.meta.json``."""

    def __init__(
        self,
        out_dir: Path,
        layer: str,
        method: str,
        dataset: str,
        config_id: str,
        *,
        base_count: int,
        dimension: int,
        record_bytes: int,
        resident_bytes_per_node: int = 0,
        metric: str = "L2",
        k: int = 10,
        meta_extra: dict[str, Any] | None = None,
    ):
        self.out_dir = Path(out_dir)
        self.layer = layer
        self.method = method
        self.dataset = dataset
        self.config_id = config_id
        self.base_count = base_count
        self.dimension = dimension
        self.metric = metric
        self.k = k
        self.meta_extra = meta_extra or {}
        self.layout = NodeLayout(record_bytes=record_bytes, count=base_count)
        self.resident_bytes_per_node = resident_bytes_per_node
        self._resident_parts: list[bytes] = []
        if record_bytes >= 4096:
            self._writer = PageWriterAligned(self.pages_path, self.layout)
        else:
            self._writer = PageWriter(self.pages_path, self.layout)

    @property
    def pages_path(self) -> Path:
        return self.out_dir / "index.pages"

    @property
    def resident_path(self) -> Path:
        return self.out_dir / "resident.bin"

    @property
    def meta_path(self) -> Path:
        return self.out_dir / "index.meta.json"

    def append(self, record: bytes) -> int:
        return self._writer.append(record)

    def append_resident(self, record: bytes) -> None:
        expected = self.resident_bytes_per_node
        if expected and len(record) > expected:
            raise ValueError(f"resident record too large: {len(record)} > {expected}")
        self._resident_parts.append(record.ljust(expected, b"\x00") if expected else record)

    def finish(
        self,
        *,
        source_index_path: Path | None = None,
        source_index_sha256: str | None = None,
        entry_point: int = 0,
        quantizer: dict[str, Any] | None = None,
        nominal_bpd: float = 4.0,
        effective_bpd: float = 4.0,
    ) -> dict[str, Any]:
        page_bytes = self._writer.flush()
        resident_bytes = 0
        resident_sha = ""
        if self._resident_parts:
            resident_bytes = sum(len(p) for p in self._resident_parts)
            self.resident_path.parent.mkdir(parents=True, exist_ok=True)
            self.resident_path.write_bytes(b"".join(self._resident_parts))
            resident_sha = sha256_file(self.resident_path)
        meta: dict[str, Any] = {
            "format_magic": FORMAT_MAGIC.decode(),
            "format_version": FORMAT_VERSION,
            "layer": self.layer,
            "method": self.method,
            "dataset": self.dataset,
            "config_id": self.config_id,
            "N": self.base_count,
            "D": self.dimension,
            "R": None,
            "metric": self.metric,
            "k": self.k,
            "entry_point": entry_point,
            "node_record_bytes": self.layout.record_bytes,
            "nodes_per_page": self.layout.records_per_page,
            "pages_per_node": self.layout.pages_per_record,
            "page_count": self.layout.total_pages,
            "resident_record_bytes": self.resident_bytes_per_node,
            "resident_record_count": len(self._resident_parts),
            "source_index_path": str(source_index_path) if source_index_path else None,
            "source_index_sha256": source_index_sha256,
            "pages_file_sha256": sha256_file(self.pages_path),
            "resident_file_sha256": resident_sha or None,
            "resident_bytes_total": resident_bytes,
            "page_file_bytes": page_bytes,
            "quantizer": quantizer or {},
            "build_time": _now(),
            "nominal_bpd": nominal_bpd,
            "effective_bpd": effective_bpd,
        }
        meta.update(self.meta_extra)
        write_json(self.meta_path, meta)
        return meta


def load_index_meta(out_dir: Path) -> dict[str, Any]:
    return json.loads((Path(out_dir) / "index.meta.json").read_text())


def verify_index(out_dir: Path, expect_layer: str, expect_method: str) -> dict[str, Any]:
    """Format / checksum verification for one exported index (plan 6.1)."""
    out_dir = Path(out_dir)
    meta = load_index_meta(out_dir)
    if meta["format_magic"] != FORMAT_MAGIC.decode():
        raise ValueError("bad format magic")
    if meta["format_version"] != FORMAT_VERSION:
        raise ValueError("bad format version")
    if meta["layer"] != expect_layer:
        raise ValueError(f"layer mismatch: {meta['layer']} != {expect_layer}")
    if meta["method"] != expect_method:
        raise ValueError(f"method mismatch: {meta['method']} != {expect_method}")
    pages = out_dir / "index.pages"
    if not pages.exists():
        raise FileNotFoundError(pages)
    if pages.stat().st_size % 4096 != 0:
        raise ValueError(f"page file not 4 KiB aligned: {pages.stat().st_size}")
    if meta["pages_file_sha256"] != sha256_file(pages):
        raise ValueError("pages file checksum mismatch")
    resident = out_dir / "resident.bin"
    if resident.exists() and meta.get("resident_file_sha256") != sha256_file(resident):
        raise ValueError("resident file checksum mismatch")
    return meta


def header_bytes(
    *,
    n: int,
    d: int,
    record_bytes: int,
    resident_bytes: int,
    r: int = 0,
) -> bytes:
    """Small binary header for tests / debug (not part of the formal format)."""
    return np.asarray([n, d, record_bytes, resident_bytes, r], dtype=np.uint64).tobytes()


def dataset_manifest(
    dataset: str,
    data_root: Path,
    index_dirs: list[Path],
    *,
    layer: str,
) -> dict[str, Any]:
    """Record hashes of data, queries, GT and source indexes (plan 3.1)."""
    files = [
        data_root / dataset / f"{dataset}_base.fvecs",
        data_root / dataset / f"{dataset}_query.fvecs",
        data_root / dataset / f"{dataset}_groundtruth.ivecs",
    ]
    manifest: dict[str, Any] = {
        "layer": layer,
        "dataset": dataset,
        "data_files": [
            {"path": str(p), "size_bytes": p.stat().st_size, "sha256": sha256_file(p)}
            for p in files
        ],
        "indexes": [],
    }
    for d in index_dirs:
        manifest["indexes"].append(load_index_meta(d))
    return manifest


def checksum_bytes(data: bytes) -> str:
    return sha256_bytes(data)
