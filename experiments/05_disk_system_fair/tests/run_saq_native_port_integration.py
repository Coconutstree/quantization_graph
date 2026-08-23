"""Real-index integration check for the native SAQ 05A disk port.

The check uses a tiny prefix of real queries but exports the real official SAQ
index. Outputs live in a disposable directory below ``work`` and are removed;
they are validation fixtures, never formal experiment results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
_REPO = _PKG.parents[1]
if "diskfair" not in sys.modules:
    import types

    _mod = types.ModuleType("diskfair")
    _mod.__path__ = [str(_PKG)]
    sys.modules["diskfair"] = _mod

from diskfair.native_contract import METHOD_SPECS, sha256_file, validate_artifact  # noqa: E402


def _copy_vec_prefix(source: Path, destination: Path, rows: int) -> None:
    with source.open("rb") as stream:
        raw = stream.read(4)
        if len(raw) != 4:
            raise RuntimeError(f"empty vecs file: {source}")
        dimension = struct.unpack("<i", raw)[0]
        row_bytes = 4 * (dimension + 1)
        stream.seek(0)
        payload = stream.read(rows * row_bytes)
    if len(payload) != rows * row_bytes:
        raise RuntimeError(f"not enough rows in {source}")
    destination.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--dataset", default="agnews")
    parser.add_argument("--queries", type=int, default=2)
    args = parser.parse_args()
    binary = args.binary.resolve()
    data_root = _REPO / "data"
    saq_root = _REPO / "baselines" / "saq" / "data"
    fixture_parent = _REPO / "work" / "05_disk_system_fair" / "integration"
    fixture_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="saq_native_", dir=fixture_parent) as tmp:
        root = Path(tmp)
        query = root / "query.fvecs"
        gt = root / "groundtruth.ivecs"
        _copy_vec_prefix(
            data_root / args.dataset / f"{args.dataset}_query.fvecs",
            query,
            args.queries,
        )
        _copy_vec_prefix(
            data_root / args.dataset / f"{args.dataset}_groundtruth.ivecs",
            gt,
            args.queries,
        )
        order = root / "order.u32"
        order.write_bytes(b"".join(struct.pack("<I", i) for i in reversed(range(args.queries))))
        input_manifest = root / "input.json"
        input_manifest.write_text(json.dumps({"kind": "real-index-integration"}) + "\n")
        disk_root = root / "device"
        index_dir = disk_root / "05_disk_system_fair" / "05A" / args.dataset / "SAQ_B4"
        result_root = root / "artifacts"
        for path in (disk_root, index_dir, result_root):
            path.mkdir(parents=True, exist_ok=True)
        binary_hash = sha256_file(binary)
        order_hash = sha256_file(order)
        input_hash = sha256_file(input_manifest)
        split_hash = hashlib.sha256(query.read_bytes() + gt.read_bytes()).hexdigest()
        common = [
            str(binary), "--contract-version", "2", "--layer", "05a",
            "--dataset", args.dataset, "--method", "SAQ_B4",
            "--data-root", str(data_root), "--work-root", str(_REPO / "work"),
            "--saq-data-root", str(saq_root),
            "--source-results-root", str(_REPO / "results"),
            "--input-manifest", str(input_manifest),
            "--input-manifest-sha256", input_hash,
            "--disk-index-dir", str(index_dir), "--query", str(query),
            "--groundtruth", str(gt), "--query-split-sha256", split_hash,
            "--query-order", str(order), "--query-order-sha256", order_hash,
            "--query-order-seed", "20260813", "--run-id", "saq_native_integration",
            "--repeat-id", "0", "--workers", "1", "--warmup-queries", "1",
            "--search-dram-budget-gib", "2", "--cache-mode", "c0",
            "--seed", "20260813", "--page-size", "4096",
            "--max-inflight-io", "128", "--direct-io", "required",
            "--native-aio", "required",
            "--implementation-fingerprint", "saq-real-index-integration",
            "--native-binary-sha256", binary_hash, "--git-commit", "integration-test",
            "--candidate-row-offset", "0",
        ]

        def invoke(phase: str, storage: str) -> Path:
            result = result_root / f"{phase}_{storage}.json"
            trace = result_root / f"{phase}_{storage}.queries.jsonl"
            command = common + [
                "--phase", phase, "--storage-mode", storage,
                "--result-json", str(result), "--query-trace", str(trace),
            ]
            subprocess.run(command, check=True)
            return result

        invoke("export", "resident")
        spec = next(spec for spec in METHOD_SPECS if spec.key == "05a:SAQ_B4")
        artifacts = {}
        for storage in ("resident", "payload_on_ssd"):
            path = invoke("validation", storage)
            artifacts[storage] = validate_artifact(
                path,
                spec=spec,
                expected={
                    "dataset": args.dataset, "phase": "validation",
                    "run_id": "saq_native_integration", "repeat_id": 0,
                    "workers": 1, "storage_mode": storage, "cache_mode": "c0",
                    "implementation_fingerprint": "saq-real-index-integration",
                    "native_binary_sha256": binary_hash,
                    "query_split_sha256": split_hash,
                    "input_manifest_sha256": input_hash,
                    "query_order_sha256": order_hash,
                    "query_order_seed": 20260813, "warmup_queries": 1,
                    "search_dram_budget_gib": 2.0,
                },
                disk_root=disk_root,
            )
        assert artifacts["payload_on_ssd"]["parity"]["max_distance_delta"] <= 1e-5
        assert [r["recall"] for r in artifacts["resident"]["summary_rows"]] == [
            r["recall"] for r in artifacts["payload_on_ssd"]["summary_rows"]
        ]
        print("PASS real SAQ B4 resident/O_DIRECT parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
