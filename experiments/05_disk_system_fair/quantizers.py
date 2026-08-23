"""NON-FORMAL NumPy codec prototypes for format/unit tests only.

These are numpy reference implementations of the four 01-suite methods with the
same nominal size (4 bit/dim, 480 bytes/vector on GIST D=960):

* ``PQQuantizer4``   : 1-dim subquantizers, 16 centroids (4-bit PQ).
* ``SQQuantizer4``   : per-dim uniform 16-level scalar quantizer.
* ``SAQQuantizer4``  : spherical block quantizer (2-dim blocks: 4-bit
  direction + 4-bit magnitude, 8 bits per block).
* ``OursRaBitQ``     : RaBitQ-style DB1 (1 bit) + 3-bit residual with INT8
  query; DB1/factor resident, full payload on SSD for the gate ablations.

The prototype parity tests require *resident* and *payload_on_ssd* to decode
the same bytes. They are not the 01/02 production kernels and must never
generate paper data.
Formal entry points use ``native_contract.py`` and do not import this module.
"""

from __future__ import annotations

REFERENCE_ONLY = True

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

SQRT_2_PI = math.sqrt(2.0 / math.pi)


def pack_nibbles(codes: np.ndarray) -> np.ndarray:
    """Pack uint8 codes in 0..15, two per byte (low nibble first)."""
    codes = np.asarray(codes, dtype=np.uint8)
    if codes.shape[-1] % 2:
        codes = np.pad(codes, ((0, 0), (0, 1)), mode="constant") if codes.ndim == 2 else np.pad(
            codes, (0, 1)
        )
    flat = codes.reshape(-1, 2)
    return (flat[:, 0] | (flat[:, 1] << 4)).reshape(codes.shape[:-1] + (-1,))


def unpack_nibbles(packed: np.ndarray) -> np.ndarray:
    flat = np.asarray(packed, dtype=np.uint8).reshape(-1)
    out = np.empty(flat.size * 2, dtype=np.uint8)
    out[0::2] = flat & 0x0F
    out[1::2] = (flat >> 4) & 0x0F
    total = packed.shape[-1] * 2
    rows = int(np.prod(packed.shape[:-1])) if packed.ndim > 1 else 1
    return out[: rows * total].reshape(packed.shape[:-1] + (total,))


def pack_3bit(codes: np.ndarray) -> np.ndarray:
    """Pack uint8 codes in 0..7, 8 dims per 3 bytes (LSB-first within byte)."""
    codes = np.asarray(codes, dtype=np.uint8)
    n = codes.shape[-1]
    pad = (-n) % 8
    if pad:
        codes = np.pad(codes, ((0, 0), (0, pad)), mode="constant") if codes.ndim == 2 else np.pad(
            codes, (0, pad)
        )
    c = codes.reshape(codes.shape[:-1] + (-1, 8))
    b0 = c[..., 0] | (c[..., 1] << 3) | (c[..., 2] << 6)
    b1 = (c[..., 2] >> 2) | (c[..., 3] << 1) | (c[..., 4] << 4) | (c[..., 5] << 7)
    b2 = (c[..., 5] >> 1) | (c[..., 6] << 2) | (c[..., 7] << 5)
    return np.stack([b0, b1, b2], axis=-1).reshape(codes.shape[:-1] + (-1,))


def unpack_3bit(packed: np.ndarray) -> np.ndarray:
    flat = np.asarray(packed, dtype=np.uint8).reshape(-1, 3)
    n8 = flat.shape[0] * 8
    out = np.empty(n8, dtype=np.uint8)
    b0, b1, b2 = flat[:, 0], flat[:, 1], flat[:, 2]
    out[0::8] = b0 & 0x07
    out[1::8] = (b0 >> 3) & 0x07
    out[2::8] = ((b0 >> 6) | (b1 << 2)) & 0x07
    out[3::8] = (b1 >> 1) & 0x07
    out[4::8] = (b1 >> 4) & 0x07
    out[5::8] = ((b1 >> 7) | (b2 << 1)) & 0x07
    out[6::8] = (b2 >> 2) & 0x07
    out[7::8] = (b2 >> 5) & 0x07
    total = packed.shape[-1] * 8 // 3
    rows = int(np.prod(packed.shape[:-1])) if packed.ndim > 1 else 1
    return out[: rows * total].reshape(packed.shape[:-1] + (total,))


def pack_db1(bits: np.ndarray) -> np.ndarray:
    """Pack 1-bit signs (1 = positive), bit i of byte (i>>3), LSB-first."""
    bits = np.asarray(bits > 0, dtype=np.uint8)
    n = bits.shape[-1]
    pad = (-n) % 8
    if pad:
        bits = np.pad(bits, ((0, 0), (0, pad)), mode="constant") if bits.ndim == 2 else np.pad(
            bits, (0, pad)
        )
    b = bits.reshape(bits.shape[:-1] + (-1, 8))
    byte = np.zeros(b.shape[:-1] + (1,), dtype=np.uint8)
    for i in range(8):
        byte = byte | (b[..., i : i + 1] << i)
    return byte.reshape(bits.shape[:-1] + (-1,))


def unpack_db1(packed: np.ndarray) -> np.ndarray:
    flat = np.asarray(packed, dtype=np.uint8).reshape(-1)
    n = flat.size * 8
    out = np.empty(n, dtype=np.uint8)
    for i in range(8):
        out[i::8] = (flat >> i) & 1
    total = packed.shape[-1] * 8
    rows = int(np.prod(packed.shape[:-1])) if packed.ndim > 1 else 1
    return out[: rows * total].reshape(packed.shape[:-1] + (total,))


def packed_size(dim: int, bits_per_dim: int) -> int:
    return (dim * bits_per_dim + 7) // 8


def _kmeans_1d(values: np.ndarray, k: int, seed: int, iters: int = 10) -> np.ndarray:
    """Fast deterministic 1-D k-means via sorted assignment (O(n log k) per iter)."""
    v = np.sort(values)
    n = v.size
    if n == 0:
        return np.zeros(k, dtype=np.float64)
    if n <= k:
        return v[:k].astype(np.float64)
    q = np.linspace(0, 1, k + 2)[1:-1]
    centers = np.interp(q, np.linspace(0, 1, n), v).astype(np.float64)
    for _ in range(iters):
        midpoints = (centers[:-1] + centers[1:]) / 2.0
        assign = np.searchsorted(midpoints, v, side="right")
        new_centers = np.empty(k, dtype=np.float64)
        for c in range(k):
            sel = v[assign == c]
            new_centers[c] = sel.mean() if sel.size else centers[c]
        if np.allclose(new_centers, centers):
            centers = new_centers
            break
        centers = new_centers
    return centers


class Quantizer:
    name = ""
    payload_bytes_per_vector = 0
    resident_bytes_per_vector = 0

    def train(self, x: np.ndarray, seed: int = 20260813) -> "Quantizer":
        raise NotImplementedError

    def encode(self, x: np.ndarray, chunk: int = 8192) -> np.ndarray:
        raise NotImplementedError

    def prepare_query(self, q: np.ndarray) -> dict[str, Any]:
        raise NotImplementedError

    def distances_from_codes(
        self,
        qctx: dict[str, Any],
        codes: np.ndarray,
        ids: np.ndarray | None = None,
    ) -> np.ndarray:
        raise NotImplementedError

    def resident_meta_bytes(self) -> int:
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload_bytes_per_vector": self.payload_bytes_per_vector,
            "resident_bytes_per_vector": self.resident_bytes_per_vector,
            "resident_meta_bytes": self.resident_meta_bytes(),
            "nominal_bpd": self.payload_bytes_per_vector * 8 / self.dim,
        }


class PQQuantizer4(Quantizer):
    name = "PQ_4bit"

    def __init__(self, dim: int, seed: int = 20260813):
        self.dim = dim
        self.seed = seed
        self.centroids: np.ndarray | None = None  # (D, 16)
        self.payload_bytes_per_vector = packed_size(dim, 4)
        self.resident_bytes_per_vector = 0

    def train(self, x: np.ndarray, seed: int | None = None) -> "PQQuantizer4":
        seed = seed if seed is not None else self.seed
        if x.shape[1] != self.dim:
            raise ValueError("train dim mismatch")
        sample = x
        if x.shape[0] > 100_000:
            rng = np.random.default_rng(seed)
            sample = x[rng.choice(x.shape[0], 100_000, replace=False)]
        centroids = np.empty((self.dim, 16), dtype=np.float32)
        for d in range(self.dim):
            centroids[d] = _kmeans_1d(sample[:, d], 16, seed + d, iters=12)
        self.centroids = centroids
        return self

    def encode(self, x: np.ndarray, chunk: int = 8192) -> np.ndarray:
        codes = np.empty((x.shape[0], self.dim), dtype=np.uint8)
        for start in range(0, x.shape[0], chunk):
            xc = x[start : start + chunk, :, None]
            dist = (xc - self.centroids[None, :, :]) ** 2
            codes[start : start + chunk] = np.argmin(dist, axis=2)
        return pack_nibbles(codes)

    def prepare_query(self, q: np.ndarray) -> dict[str, Any]:
        lut = (q[:, None] - self.centroids) ** 2  # (D, 16)
        return {"lut": lut, "q_norm_sqr": float((q**2).sum())}

    def distances_from_codes(
        self,
        qctx: dict[str, Any],
        codes: np.ndarray,
        ids: np.ndarray | None = None,
    ) -> np.ndarray:
        codes = unpack_nibbles(codes)
        lut = qctx["lut"]  # (D, 16)
        return lut[np.arange(self.dim), codes].sum(axis=-1)

    def resident_meta_bytes(self) -> int:
        return int(self.centroids.nbytes) if self.centroids is not None else 0

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["codebook"] = {"centroids": self.centroids.shape if self.centroids is not None else None}
        return d


class SQQuantizer4(Quantizer):
    name = "SQ_4bit"

    def __init__(self, dim: int, seed: int = 20260813):
        self.dim = dim
        self.seed = seed
        self.lo: np.ndarray | None = None
        self.hi: np.ndarray | None = None
        self.payload_bytes_per_vector = packed_size(dim, 4)
        self.resident_bytes_per_vector = 0

    def train(self, x: np.ndarray, seed: int | None = None) -> "SQQuantizer4":
        seed = seed if seed is not None else self.seed
        sample = x
        if x.shape[0] > 100_000:
            rng = np.random.default_rng(seed)
            sample = x[rng.choice(x.shape[0], 100_000, replace=False)]
        lo = np.percentile(sample, 1.0, axis=0).astype(np.float32)
        hi = np.percentile(sample, 99.0, axis=0).astype(np.float32)
        hi = np.maximum(hi, lo + 1e-6)
        self.lo, self.hi = lo, hi
        return self

    def encode(self, x: np.ndarray, chunk: int = 8192) -> np.ndarray:
        codes = np.clip(
            np.round((x - self.lo) * 15.0 / (self.hi - self.lo)), 0, 15
        ).astype(np.uint8)
        return pack_nibbles(codes)

    def _decode_levels(self) -> np.ndarray:
        return self.lo[:, None] + np.arange(16, dtype=np.float32)[None, :] * (
            self.hi - self.lo
        )[:, None] / 15.0

    def prepare_query(self, q: np.ndarray) -> dict[str, Any]:
        levels = self._decode_levels()  # (D, 16)
        lut = (q[:, None] - levels) ** 2
        return {"lut": lut, "q_norm_sqr": float((q**2).sum())}

    def distances_from_codes(
        self,
        qctx: dict[str, Any],
        codes: np.ndarray,
        ids: np.ndarray | None = None,
    ) -> np.ndarray:
        codes = unpack_nibbles(codes)
        return qctx["lut"][np.arange(self.dim), codes].sum(axis=-1)

    def resident_meta_bytes(self) -> int:
        return int(self.lo.nbytes + self.hi.nbytes) if self.lo is not None else 0


class SAQQuantizer4(Quantizer):
    """Spherical reference: 2-dim blocks, 4-bit angle + 4-bit magnitude."""

    name = "SAQ_B4"

    def __init__(self, dim: int, seed: int = 20260813):
        if dim % 2:
            raise ValueError("SAQ reference requires even dim")
        self.dim = dim
        self.seed = seed
        self.nblocks = dim // 2
        self.mag_scale: np.ndarray | None = None  # (nblocks,)
        self.payload_bytes_per_vector = self.nblocks  # 1 byte per 2-dim block
        self.resident_bytes_per_vector = 0

    def train(self, x: np.ndarray, seed: int | None = None) -> "SAQQuantizer4":
        seed = seed if seed is not None else self.seed
        sample = x
        if x.shape[0] > 100_000:
            rng = np.random.default_rng(seed)
            sample = x[rng.choice(x.shape[0], 100_000, replace=False)]
        blocks = sample.reshape(sample.shape[0], self.nblocks, 2)
        norms = np.linalg.norm(blocks, axis=2)  # (n, nblocks)
        # per-block magnitude scale: 95th percentile of block norms
        self.mag_scale = np.percentile(norms, 95.0, axis=0).astype(np.float32)
        self.mag_scale = np.maximum(self.mag_scale, 1e-6)
        return self

    @staticmethod
    def _dir_vectors() -> np.ndarray:
        angles = np.arange(16, dtype=np.float32) * (2 * np.pi / 16.0)
        return np.stack([np.cos(angles), np.sin(angles)], axis=1)  # (16, 2)

    def encode(self, x: np.ndarray, chunk: int = 8192) -> np.ndarray:
        n = x.shape[0]
        codes = np.empty((n, self.nblocks), dtype=np.uint8)
        for start in range(0, n, chunk):
            blocks = x[start : start + chunk].reshape(-1, self.nblocks, 2)
            norms = np.linalg.norm(blocks, axis=2)
            safe = np.where(norms > 1e-12, norms, 1.0)
            ux = blocks[..., 0] / safe
            uy = blocks[..., 1] / safe
            ang = np.arctan2(uy, ux) % (2 * np.pi)
            angle_code = np.round(ang / (2 * np.pi / 16.0)).astype(np.uint8) % 16
            mag = np.clip(
                np.round(norms / self.mag_scale[None, :] * 15.0), 0, 15
            ).astype(np.uint8)
            codes[start : start + chunk] = (angle_code | (mag << 4)).astype(np.uint8)
        return codes

    def prepare_query(self, q: np.ndarray) -> dict[str, Any]:
        blocks = q.reshape(self.nblocks, 2)
        dirs = self._dir_vectors()  # (16, 2)
        dot = blocks @ dirs.T  # (nblocks, 16)
        q_block_norm_sqr = (blocks**2).sum(axis=1)  # (nblocks,)
        return {
            "dot": dot,
            "q_block_norm_sqr": q_block_norm_sqr,
            "q_norm_sqr": float((q**2).sum()),
        }

    def distances_from_codes(
        self,
        qctx: dict[str, Any],
        codes: np.ndarray,
        ids: np.ndarray | None = None,
    ) -> np.ndarray:
        codes = np.asarray(codes, dtype=np.uint8)
        angle_code = (codes & 0x0F).astype(np.int64)
        mag_code = (codes >> 4).astype(np.float32)
        norms = (mag_code + 0.5) / 16.0 * self.mag_scale[None, :]
        dot = qctx["dot"][np.arange(self.nblocks), angle_code]
        return qctx["q_norm_sqr"] + (norms**2).sum(axis=-1) - 2.0 * (norms * dot).sum(axis=-1)

    def resident_meta_bytes(self) -> int:
        return int(self.mag_scale.nbytes) if self.mag_scale is not None else 0


class OursRaBitQ(Quantizer):
    """RaBitQ-style codec: DB1 (1 bit) + 3-bit residual, INT8 query."""

    name = "Ours_RaBitQ_K1"

    def __init__(self, dim: int, seed: int = 20260813, residual_block: int = 16):
        if dim % residual_block:
            raise ValueError("dim must be divisible by residual_block")
        self.dim = dim
        self.seed = seed
        self.residual_block = residual_block
        self.n_blocks = dim // residual_block
        self.P: np.ndarray | None = None  # random orthogonal (dim, dim)
        self.res_scale: np.ndarray | None = None  # per-dim residual scale
        self.db1_bits: np.ndarray | None = None  # (N, dim) packed? kept raw
        self.res_codes: np.ndarray | None = None
        self.norms_sqr: np.ndarray | None = None
        self.db1_scales: np.ndarray | None = None
        self.payload_bytes_per_vector = packed_size(dim, 1) + packed_size(dim, 3)
        self.resident_bytes_per_vector = 4 + 4 + 4 * self.n_blocks  # db1_scale + norm_sqr + block scales

    def _rotation(self) -> np.ndarray:
        rng = np.random.default_rng(self.seed)
        a = rng.standard_normal((self.dim, self.dim))
        q, r = np.linalg.qr(a)
        # fix sign so diagonal of R is positive
        d = np.sign(np.diag(r))
        d[d == 0] = 1.0
        return (q * d[None, :]).astype(np.float32)

    def train(self, x: np.ndarray, seed: int | None = None) -> "OursRaBitQ":
        seed = seed if seed is not None else self.seed
        self.seed = seed
        self.P = self._rotation()
        sample = x
        if x.shape[0] > 100_000:
            rng = np.random.default_rng(seed)
            sample = x[rng.choice(x.shape[0], 100_000, replace=False)]
        y = sample @ self.P.T
        sign = np.where(y > 0, 1.0, -1.0)
        x_hat = sign @ self.P * (np.linalg.norm(sample, axis=1, keepdims=True) / math.sqrt(self.dim) * SQRT_2_PI)
        res = sample - x_hat
        self.res_scale = np.percentile(np.abs(res), 99.0, axis=0).astype(np.float32) / 3.5
        self.res_scale = np.maximum(self.res_scale, 1e-6)
        return self

    def encode(self, x: np.ndarray, chunk: int = 8192) -> np.ndarray:
        n = x.shape[0]
        db1 = np.empty((n, self.dim), dtype=np.uint8)
        res3 = np.empty((n, self.dim), dtype=np.uint8)
        for start in range(0, n, chunk):
            xc = x[start : start + chunk]
            y = xc @ self.P.T
            db1[start : start + chunk] = y > 0
            sign = np.where(y > 0, 1.0, -1.0)
            norm = np.linalg.norm(xc, axis=1, keepdims=True)
            x_hat = sign @ self.P * (norm / math.sqrt(self.dim) * SQRT_2_PI)
            res = xc - x_hat
            res3[start : start + chunk] = np.clip(
                np.round(res / self.res_scale[None, :] + 3.5), 0, 7
            ).astype(np.uint8)
        return np.concatenate([pack_db1(db1), pack_3bit(res3)], axis=1)

    def encode_with_factors(self, x: np.ndarray, chunk: int = 8192) -> dict[str, Any]:
        n = x.shape[0]
        codes = np.empty((n, self.payload_bytes_per_vector), dtype=np.uint8)
        norms_sqr = np.empty(n, dtype=np.float32)
        db1_scales = np.empty(n, dtype=np.float32)
        res_scales = np.empty((n, self.n_blocks), dtype=np.float32)
        for start in range(0, n, chunk):
            xc = x[start : start + chunk]
            y = xc @ self.P.T
            sign = np.where(y > 0, 1.0, -1.0)
            # MSE-optimal DB1 scale: s_v = mean(|Px|) minimises ||x - x_hat1||
            db1_scale_v = np.abs(y).mean(axis=1)
            x_hat = sign @ self.P * db1_scale_v[:, None]
            res = xc - x_hat
            db1 = (y > 0).astype(np.uint8)
            res_b = res.reshape(res.shape[0], self.n_blocks, self.residual_block)
            res_scale_b = np.max(np.abs(res_b), axis=2) / 3.5
            res_scale_b = np.maximum(res_scale_b, 1e-6).astype(np.float32)
            res3 = np.clip(
                np.round(res_b / res_scale_b[:, :, None] + 3.5), 0, 7
            ).astype(np.uint8)
            res3 = res3.reshape(res.shape)
            codes[start : start + chunk] = np.concatenate(
                [pack_db1(db1), pack_3bit(res3)], axis=1
            )
            norms_sqr[start : start + chunk] = (np.linalg.norm(xc, axis=1) ** 2)
            db1_scales[start : start + chunk] = db1_scale_v
            res_scales[start : start + chunk] = res_scale_b
        return {
            "codes": codes,
            "norm_sqr": norms_sqr,
            "db1_scale": db1_scales,
            "res_scale": res_scales,
        }

    def prepare_query(self, q: np.ndarray) -> dict[str, Any]:
        """INT8 query + rotation + residual LUT."""
        scale = np.abs(q).max() / 127.0 if np.abs(q).max() > 0 else 1.0
        q_int8 = np.clip(np.round(q / scale), -127, 127).astype(np.int8)
        pq = (q_int8.astype(np.float32) @ self.P.T) * scale
        res_lut = (
            q.astype(np.float32)[:, None]
            * (np.arange(8, dtype=np.float32)[None, :] - 3.5)
        )  # (D, 8), scaled by per-vector res_scale at decode time
        return {
            "q_norm_sqr": float((q**2).sum()),
            "pq": pq,
            "res_lut": res_lut,
            "q_scale": float(scale),
        }

    def db1_estimated_ip(
        self,
        qctx: dict[str, Any],
        db1_bits: np.ndarray,
        db1_scales: np.ndarray,
    ) -> np.ndarray:
        """<q, x_hat_db1> = db1_scale * sum_d (Pq)_d * sign_d."""
        sign = np.where(unpack_db1(db1_bits).astype(np.int8) > 0, 1.0, -1.0)
        return np.einsum("d,nd->n", qctx["pq"], sign) * db1_scales

    def distances_from_codes(
        self,
        qctx: dict[str, Any],
        codes: np.ndarray,
        ids: np.ndarray | None = None,
        db1_scales: np.ndarray | None = None,
        norm_sqr: np.ndarray | None = None,
        res_scale: np.ndarray | None = None,
    ) -> np.ndarray:
        n = codes.shape[0]
        db1_bytes = packed_size(self.dim, 1)
        db1 = unpack_db1(codes[:, :db1_bytes])
        res3 = unpack_3bit(codes[:, db1_bytes:])
        sign = np.where(db1.astype(np.int8) > 0, 1.0, -1.0)
        if db1_scales is None:
            db1_scales = np.ones(n, dtype=np.float32)
        if norm_sqr is None:
            norm_sqr = np.ones(n, dtype=np.float32)
        if res_scale is None:
            res_scale = np.ones((n, self.n_blocks), dtype=np.float32)
        ip_db1 = np.einsum("d,nd->n", qctx["pq"], sign) * db1_scales
        sel = qctx["res_lut"][np.arange(self.dim), res3]  # (n, dim)
        sel_b = sel.reshape(n, self.n_blocks, self.residual_block).sum(axis=2)
        res_part = (sel_b * res_scale).sum(axis=1)
        res_norm_sqr = (
            ((res3.astype(np.float32) - 3.5) ** 2)
            .reshape(n, self.n_blocks, self.residual_block)
            .sum(axis=2)
            * res_scale**2
        ).sum(axis=1)
        x_hat_norm_sqr = (db1_scales**2) * self.dim + res_norm_sqr
        return qctx["q_norm_sqr"] + x_hat_norm_sqr - 2.0 * (ip_db1 + res_part)

    def resident_meta_bytes(self) -> int:
        p = self.P.nbytes if self.P is not None else 0
        s = self.res_scale.nbytes if self.res_scale is not None else 0
        return p + s


QUANTIZERS = {
    "PQ_4bit": PQQuantizer4,
    "SQ_4bit": SQQuantizer4,
    "SAQ_B4": SAQQuantizer4,
    "Ours_RaBitQ_K1": OursRaBitQ,
}
