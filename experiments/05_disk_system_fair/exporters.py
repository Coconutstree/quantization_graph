"""NON-FORMAL prototype exporters retained for format unit tests only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .common import write_json
from .format import IndexBuilder
from .quantizers import Quantizer, pack_nibbles, pack_3bit, pack_db1


def export_05a_payload(
    out_dir: Path,
    *,
    dataset: str,
    method: str,
    config_id: str,
    quantizer: Quantizer,
    codes: np.ndarray,
    base_count: int,
    dimension: int,
    candidate_path: Path | None = None,
    candidate_sha256: str | None = None,
    extra: dict[str, Any] | None = None,
    ours_factors: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Export one 05A method's quantized payload into ``index.pages``."""
    rec_bytes = quantizer.payload_bytes_per_vector
    resident_per_node = (
        quantizer.resident_bytes_per_vector
        if quantizer.name == "Ours_RaBitQ_K1" and ours_factors is not None
        else 0
    )
    builder = IndexBuilder(
        out_dir,
        layer="05A",
        method=method,
        dataset=dataset,
        config_id=config_id,
        base_count=base_count,
        dimension=dimension,
        record_bytes=rec_bytes,
        resident_bytes_per_node=resident_per_node,
        meta_extra={
            "quantizer_name": quantizer.name,
            "candidate_path": str(candidate_path) if candidate_path else None,
            "candidate_sha256": candidate_sha256,
            "codebook": quantizer.to_dict(),
            **(extra or {}),
        },
    )
    for i in range(codes.shape[0]):
        builder.append(codes[i].tobytes())
        if resident_per_node:
            resid = (
                ours_factors["norm_sqr"][i : i + 1].astype("<f4").tobytes()
                + ours_factors["db1_scale"][i : i + 1].astype("<f4").tobytes()
                + ours_factors["res_scale"][i : i + 1].astype("<f4").tobytes()
            )
            builder.append_resident(resid)
    meta = builder.finish(
        nominal_bpd=quantizer.payload_bytes_per_vector * 8 / dimension,
        effective_bpd=quantizer.payload_bytes_per_vector * 8 / dimension,
    )
    # resident metadata (codebook / scale) is stored for reproducibility
    meta["quantizer_meta"] = quantizer.to_dict()
    write_json(out_dir / "index.meta.json", meta)
    return meta


def export_05a_resident(
    out_dir: Path,
    *,
    dataset: str,
    method: str,
    config_id: str,
    quantizer: Quantizer,
    codes: np.ndarray,
    base_count: int,
    dimension: int,
    extra: dict[str, Any] | None = None,
    ours_factors: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Write the resident copy of the same codes (for the resident mode)."""
    rec_bytes = quantizer.payload_bytes_per_vector
    resident_per_node = (
        quantizer.resident_bytes_per_vector
        if quantizer.name == "Ours_RaBitQ_K1" and ours_factors is not None
        else 0
    )
    builder = IndexBuilder(
        out_dir,
        layer="05A",
        method=method,
        dataset=dataset,
        config_id=config_id,
        base_count=base_count,
        dimension=dimension,
        record_bytes=rec_bytes,
        resident_bytes_per_node=resident_per_node,
        meta_extra={
            "storage_mode": "resident",
            "quantizer_meta": quantizer.to_dict(),
            **(extra or {}),
        },
    )
    for i in range(codes.shape[0]):
        builder.append(codes[i].tobytes())
        if resident_per_node:
            resid = (
                ours_factors["norm_sqr"][i : i + 1].astype("<f4").tobytes()
                + ours_factors["db1_scale"][i : i + 1].astype("<f4").tobytes()
                + ours_factors["res_scale"][i : i + 1].astype("<f4").tobytes()
            )
            builder.append_resident(resid)
    return builder.finish(
        nominal_bpd=quantizer.payload_bytes_per_vector * 8 / dimension,
        effective_bpd=quantizer.payload_bytes_per_vector * 8 / dimension,
    )


def export_ours_full4(
    out_dir: Path,
    *,
    dataset: str,
    method: str,
    config_id: str,
    quantizer,
    enc: dict[str, Any],
    dimension: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export Ours full4 payload pages + DB1/factor resident.bin (05B/05C)."""
    db1_bytes = (dimension + 7) // 8
    res_bytes = (dimension * 3 + 7) // 8
    rec_bytes = db1_bytes + res_bytes
    builder = IndexBuilder(
        out_dir,
        layer="05B",
        method=method,
        dataset=dataset,
        config_id=config_id,
        base_count=enc["codes"].shape[0],
        dimension=dimension,
        record_bytes=rec_bytes,
        resident_bytes_per_node=8,
        meta_extra={
            "quantizer_name": quantizer.name,
            "quantizer_meta": quantizer.to_dict(),
            "resident_fields": ["norm_sqr_f32", "db1_scale_f32"],
            **(extra or {}),
        },
    )
    for i in range(enc["codes"].shape[0]):
        builder.append(enc["codes"][i].tobytes())
        resid = np.concatenate(
            [
                enc["norm_sqr"][i : i + 1].astype("<f4").tobytes(),
                enc["db1_scale"][i : i + 1].astype("<f4").tobytes(),
            ]
        )
        builder.append_resident(resid)
    return builder.finish(
        nominal_bpd=4.0,
        effective_bpd=rec_bytes * 8 / dimension,
    )
