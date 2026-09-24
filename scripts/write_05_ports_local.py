#!/usr/bin/env python3
"""Generate the formal 05 native-port registry from local build outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'src'))
from disk_bench.native_contract import LAYER_METHODS
from disk_bench.symphony_provenance import verify_source

DEFAULT_OUTPUT = REPO_ROOT / "src" / "disk_bench" / "ports.local.json"


PORTS = {
    "05a:PQ_4bit": {
        "command": ["build/disk/native/qgraph05_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "faiss::ProductQuantizer",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-faiss-pq4-native-odirect-v1",
    },
    "05a:SQ_4bit": {
        "command": ["build/disk/native/qgraph05_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "faiss::ScalarQuantizer(QT_4bit)",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-faiss-sq4-native-odirect-v1",
    },
    "05a:SAQ_B4": {
        "command": ["build/disk/native/qgraph05_saq_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "official SAQ B=4 distance kernel",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-saq-b4-native-odirect-v1",
    },
    "05a:Ours_RaBitQ_K1": {
        "command": ["build/disk/native/qgraph05_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "hnswlib::RaBitQSpace K=1",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-rabitq-k1-native-odirect-v1",
    },
    "05b:PQ-DiskANN-Disk": {
        "command": ["src/graph_core/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "DiskANN FixedChunkPQTable 4-bit",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-shared-graph-pq4-release-native-odirect-v1",
    },
    "05b:SQ-DiskANN-Disk": {
        "command": ["src/graph_core/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "DiskANN ScalarQuantizer<4>",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-shared-graph-sq4-release-native-odirect-v1",
    },
    "05b:SAQ-DiskANN-Disk": {
        "command": ["src/graph_core/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "DiskANN spherical::Impl<4>",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-shared-graph-saq4-release-native-odirect-v1",
    },
    "05b:Ours-Disk": {
        "command": ["src/graph_core/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-ours-release-native-odirect-v2",
    },
    "05c:Ours-Disk": {
        "command": ["src/graph_core/target/release/qgraph05_shared_graph_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "ExRaBitQ4 symmetric Vamana; resident DB1 or budget-selected PCA+1bit M32 (no residual)",
        "port_kind": "native_disk_system",
        "implementation_fingerprint": "05c-ours-pca1bit-hot-dynamic-v5",
    },
    "05c:SymphonyQG-DiskPort": {
        "command": ["build/disk/native/qgraph05_symphonyqg_disk_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "official SymphonyQG FastScan LUT+SIMD",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05c-symphonyqg-fastscan-native-odirect-export-v5",
    },
    "05c:OG-LVQ-DiskPort": {
        "command": ["build/disk/native/qgraph05_og_lvq_disk_port"],
        "source_suite": "03_system_fair",
        "status": "blocked",
        "blocked_reason": "Official LVQ distance implementation unavailable; local scalar decoder is not an approved baseline.",
        "source_kernel": "local LVQ4 decoder; official distance parity pending",
        "port_kind": "experimental_disk_port_pending_official_parity",
        "implementation_fingerprint": "05c-og-lvq-svs-layout-native-odirect-v1",
    },
    "05c:Glass-NSG-DiskPort": {
        "command": ["build/disk/native/qgraph05_glass_disk_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "official Glass NSG SQ4U distance kernel",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05c-glass-nsg-sq4u-official-linear-pool-search-v3",
    },
    "05c:DiskANN-PQ-Disk": {
        "command": [
            "experiments/03_disk_system/native_diskann/target/release/qgraph05_diskann_port"
        ],
        "source_suite": "03_system_fair",
        "source_kernel": "official diskann-disk PQ search",
        "port_kind": "official_native_disk",
        "implementation_fingerprint": "05c-official-diskann-pq-release-io-uring-v4",
    },
}


# Native CLI availability is separate from formal measurement acceptance.
PORTS.update({
    "05c:AiSAQ-Disk": {
        "status": "pending",
        "command": [
            "experiments/03_disk_system/adapters/run_official_disk_baseline.py",
            "--method",
            "AiSAQ-Disk"
        ],
        "source_suite": "official_disk_baselines",
        "source_kernel": "official AiSAQ PQFlashIndex with use_aisaq",
        "port_kind": "official_native_disk",
        "implementation_fingerprint": "aisaq-f0a48e984c685bd498e3c4f88386b47e0e4ab1ac-storage-sysfs-v1",
        "blocked_reason": "Official CLI integration; formal per-query artifact, memory accounting and acceptance validation are not yet complete."
    },
    "05c:Starling-Disk": {
        "status": "pending",
        "command": [
            "experiments/03_disk_system/adapters/run_official_disk_baseline.py",
            "--method",
            "Starling-Disk"
        ],
        "source_suite": "official_disk_baselines",
        "source_kernel": "official Starling PQ page search with navigation graph",
        "port_kind": "official_native_disk",
        "implementation_fingerprint": "starling-17dc3e8a011533a62374445f53963e951b72883a",
        "blocked_reason": "Official CLI integration; formal per-query artifact, memory accounting and acceptance validation are not yet complete."
    }
})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--symphony-build-dir", type=Path,
                        help="Isolated, source-audited SymphonyQG build directory")
    args = parser.parse_args()

    if args.symphony_build_dir:
        build = args.symphony_build_dir.resolve()
        spec = PORTS["05c:SymphonyQG-DiskPort"]
        spec["command"] = [str(build / "qgraph05_symphonyqg_disk_port")]
        spec["implementation_fingerprint"] = "05c-symphonyqg-pristine-6124ddb-export-v6"

    missing = []
    ready_ports = {}
    for key, spec in PORTS.items():
        command = spec["command"]
        binary = REPO_ROOT / command[0]
        if spec.get("status") in ("pending", "blocked"):
            ready_ports[key] = dict(spec)
            continue
        if not binary.is_file():
            missing.append(f"{key}: {command[0]}")
            continue
        if key == "05c:SymphonyQG-DiskPort":
            try:
                verify_source(binary)
            except (ValueError, OSError, KeyError) as exc:
                ready_ports[key] = dict(spec, status="pending", blocked_reason=str(exc))
                continue
        ready_ports[key] = {
            "status": "ready",
            **spec,
            "binary_sha256": sha256_file(binary),
        }
    if missing:
        print("Missing formal 05 binaries. Run `bash scripts/build_formal_local.sh` first.")
        for item in missing:
            print(f"  - {item}")
        return 2

    document = {
        "schema_version": 2,
        "note": (
            "Generated by scripts/write_05_ports_local.py from repository-local "
            "formal 05 build outputs. Re-run after rebuilding native ports."
        ),
        "ports": ready_ports,
        "primary_05c_methods": list(LAYER_METHODS["05c"]),
    }
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")
    try:
        display_path = output.relative_to(REPO_ROOT)
    except ValueError:
        display_path = output
    print(f"wrote {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
