"""Prepare the formal 03 low-RAM route before measured search starts.

PCA fits base vectors only. Dimension comes from the native memory planner;
validation still selects search width/beam. Historical 04 assets are untouched.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

POLICY = "resident_full_else_pca1bit_m32_no_residual_v1"
SEED = 20260921


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def plan_route(binary: Path, index: Path, workers: int, budget_gib: float,
               query_reservation: int) -> dict:
    result = subprocess.run([
        str(binary), "--ours-route-plan", str(index), "--ours-route-policy", POLICY,
        "--workers", str(workers), "--search-dram-budget-gib", str(budget_gib),
        "--ours-query-reservation-bytes", str(query_reservation),
    ], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def verify_assets(folder: Path, expected: dict) -> None:
    metadata = json.loads((folder / "assets.json").read_text())
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f"PCA source mismatch: {folder}: {key}")
    for name in ("pca.json", "mean.bin", "basis.bin", "sidecar.bin"):
        if metadata["files"].get(name) != sha(folder / name):
            raise ValueError(f"PCA asset hash mismatch: {folder / name}")


def ensure_assets(base: Path, binary: Path, root: Path, plan: dict) -> Path | None:
    if plan["mode"] == "full1bit":
        return None
    # Imported only for offline preparation. No Python distance kernel is measured.
    import numpy as np

    with base.open("rb") as stream:
        dim = struct.unpack("<I", stream.read(4))[0]
    count, k = plan["count"], plan["dimension"]
    if dim != plan["original_dimension"] or base.stat().st_size != count * (dim + 1) * 4:
        raise ValueError("PCA base/index shape mismatch")
    source = dict(base_sha256=sha(base), native_binary_sha256=sha(binary), dimension=k,
                  fit_seed=SEED, policy=POLICY)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / f"{source['base_sha256'][:16]}_{source['native_binary_sha256'][:16]}_d{k}"
    with (root / ".prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if folder.exists():
            verify_assets(folder, source)
            return folder
        raw = np.memmap(base, dtype="<f4", mode="r", shape=(count, dim + 1))
        # A separate basis cache prevents refitting when only the RAM budget changes.
        fit = root / f"fit_{source['base_sha256']}_seed{SEED}"
        if not fit.exists():
            stage = Path(tempfile.mkdtemp(prefix=".fit-", dir=root))
            try:
                ids = np.sort(np.random.default_rng(SEED).choice(count, min(count, 32768), replace=False))
                x = np.asarray(raw[ids, 1:], dtype=np.float64)
                mean = x.mean(axis=0)
                x -= mean
                values, vectors = np.linalg.eigh(x.T @ x / max(1, len(ids) - 1))
                values = values[::-1]
                basis = vectors[:, ::-1].T.copy()
                mean.astype("<f4").tofile(stage / "mean.bin")
                basis.astype("<f4").tofile(stage / "basis.bin")
                ids.astype("<u4").tofile(stage / "sample_ids.bin")
                dump(stage / "fit.json", dict(base_sha256=source["base_sha256"], fit_source="base only",
                     seed=SEED, sample_count=len(ids), eigenvalues=values.tolist(),
                     files={name: sha(stage / name) for name in ("mean.bin", "basis.bin", "sample_ids.bin")}))
                stage.rename(fit)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        fitted = json.loads((fit / "fit.json").read_text())
        for name, digest in fitted["files"].items():
            if sha(fit / name) != digest:
                raise ValueError(f"PCA fit cache hash mismatch: {name}")
        mean = np.fromfile(fit / "mean.bin", dtype="<f4")
        basis = np.fromfile(fit / "basis.bin", dtype="<f4").reshape(dim, dim)[:k]
        stage = Path(tempfile.mkdtemp(prefix=".encode-", dir=root))
        try:
            mean.tofile(stage / "mean.bin")
            basis.tofile(stage / "basis.bin")
            projected_basis = basis.T.astype(np.float64)
            with (stage / "projected.bin").open("wb") as stream:
                for start in range(0, count, 2048):
                    block = raw[start:start + 2048]
                    if not np.all(block[:, 0].view("<u4") == dim):
                        raise ValueError("malformed fvecs row dimension")
                    x = np.asarray(block[:, 1:], dtype=np.float32) - mean
                    (x.astype(np.float64) @ projected_basis).astype("<f4").tofile(stream)
            dump(stage / "pca.json", dict(dim=k, original_dim=dim, count=count, seed=17))
            subprocess.run([str(binary), "--pca-export", str(stage)], check=True)
            (stage / "projected.bin").unlink()
            eigenvalues = np.asarray(fitted["eigenvalues"])
            dump(stage / "assets.json", dict(**source, count=count, original_dimension=dim,
                 fit_manifest_sha256=sha(fit / "fit.json"), fit_source="base only",
                 retained_variance=float(eigenvalues[:k].sum()/eigenvalues.sum()) if eigenvalues.sum() else 0.,
                 projection="float32 centering; float64 dot; float32 output",
                 residual_norm_bytes=0, files={name: sha(stage / name) for name in
                     ("pca.json", "mean.bin", "basis.bin", "sidecar.bin")}))
            stage.rename(folder)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        verify_assets(folder, source)
        return folder


def prepare_route_args(*, binary: Path, index: Path, base: Path, query_source: Path,
                       groundtruth_source: Path, workers: int, budget_gib: float,
                       artifact: Path, frozen_route: Path | None = None) -> list[str]:
    # Use UNSPLIT inputs so validation and test reserve the same memory and select
    # the same prefix. Allow parsing copies/order/results in addition to matrices.
    query_bytes = 2 * (query_source.stat().st_size + groundtruth_source.stat().st_size)
    plan = plan_route(binary, index, workers, budget_gib, query_bytes)
    if frozen_route is not None:
        frozen = json.loads(frozen_route.read_text())
        if frozen != plan:
            raise ValueError("resident representation differs from frozen paired plan")
        plan = frozen
    asset = ensure_assets(base, binary, index / "pca_routes", plan)
    dump(artifact.with_suffix(".route_plan.json"), plan)
    args = ["--ours-route-policy", POLICY, "--ours-query-reservation-bytes", str(query_bytes)]
    if asset is not None:
        args += ["--pca-route-dir", str(asset.resolve()), "--pca-assets-sha256", sha(asset / "assets.json")]
    return args
