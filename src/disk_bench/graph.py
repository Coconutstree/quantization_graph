"""Shared Vamana graph: DiskANN3 canonical loader + 05B page exporter."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np

from .common import sha256_file, write_json
from .diskio import NodeLayout
from .format import IndexBuilder


def load_diskann_graph(path: Path) -> dict[str, Any]:
    """Load a DiskANN3 canonical graph (24-byte header + per-node lists)."""
    data = path.read_bytes()
    if len(data) < 24:
        raise ValueError("graph file too small")
    file_size = struct.unpack_from("<Q", data, 0)[0]
    max_degree = struct.unpack_from("<I", data, 8)[0]
    start_point = struct.unpack_from("<I", data, 12)[0]
    num_start_points = struct.unpack_from("<Q", data, 16)[0]
    pos = 24
    degrees: list[int] = []
    neighbors: list[np.ndarray] = []
    while pos < len(data):
        (count,) = struct.unpack_from("<I", data, pos)
        pos += 4
        arr = np.frombuffer(data, dtype="<u4", count=count, offset=pos)
        neighbors.append(arr.astype(np.int64))
        pos += 4 * count
        degrees.append(count)
    if file_size != len(data):
        file_size = len(data)
    return {
        "path": str(path),
        "file_size": file_size,
        "max_degree": int(max_degree),
        "start_point": int(start_point),
        "num_start_points": int(num_start_points),
        "count": len(neighbors),
        "degrees": np.asarray(degrees, dtype=np.int32),
        "neighbors": neighbors,
        "sha256": sha256_file(path),
    }


def adjacency_bytes(neighbors: list[np.ndarray], max_degree: int) -> tuple[int, np.ndarray]:
    """Return (record_bytes, packed u32 adjacency) for a fixed-degree layout.

    Each node record stores ``max_degree`` u32 ids, padding with 0 on missing
    neighbors; the true degree is recorded in the first u32.
    """
    n = len(neighbors)
    rec = np.full((n, max_degree + 1), 0, dtype="<u4")
    for i, nb in enumerate(neighbors):
        k = min(len(nb), max_degree)
        rec[i, 0] = k
        rec[i, 1 : 1 + k] = nb[:k]
    record_bytes = 4 * (max_degree + 1)
    return record_bytes, rec


def export_shared_graph(
    graph: dict[str, Any],
    out_dir: Path,
    *,
    dataset: str,
    method: str,
    config_id: str,
    layer: str = "05B",
    max_degree: int | None = None,
    nominal_bpd: float = 0.0,
    note: str = "",
) -> dict[str, Any]:
    """Export a loaded graph into ``index.pages`` (adjacency) + meta."""
    max_degree = max_degree or int(graph["max_degree"])
    record_bytes, rec = adjacency_bytes(graph["neighbors"], max_degree)
    builder = IndexBuilder(
        out_dir,
        layer=layer,
        method=method,
        dataset=dataset,
        config_id=config_id,
        base_count=graph["count"],
        dimension=0,
        record_bytes=record_bytes,
        metric="L2",
        meta_extra={
            "graph_type": "shared_vamana_diskann3",
            "graph_source_path": graph["path"],
            "graph_source_sha256": graph["sha256"],
            "data_node_count": graph["count"] - graph["num_start_points"],
            "max_degree": max_degree,
            "L_build": 400,
            "alpha": 1.2,
            "graph_build_seed": 20260813,
            "note": note,
        },
    )
    for i in range(graph["count"]):
        builder.append(rec[i].tobytes())
    meta = builder.finish(
        entry_point=graph["start_point"],
        nominal_bpd=nominal_bpd,
        effective_bpd=0.0,
    )
    meta["adjacency_record_bytes"] = record_bytes
    meta["adjacency_sha256"] = meta["pages_file_sha256"]
    write_json(out_dir / "index.meta.json", meta)
    return meta


def graph_checksum(graph: dict[str, Any]) -> str:
    return graph["sha256"]


def read_adjacency_from_pages(
    pages: dict[int, bytes],
    layout: NodeLayout,
    node_id: int,
    max_degree: int,
) -> np.ndarray:
    from .diskio import extract_node

    rec = extract_node(pages, layout, node_id)
    arr = np.frombuffer(rec, dtype="<u4")
    k = int(arr[0])
    return arr[1 : 1 + k]


def write_graph_meta(path: Path, meta: dict[str, Any]) -> None:
    write_json(path, meta)
