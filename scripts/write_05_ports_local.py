#!/usr/bin/env python3
"""Generate the formal 05 native-port registry from local build outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "experiments" / "05_disk_system_fair" / "ports.local.json"


PORTS = {
    "05a:PQ_4bit": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "faiss::ProductQuantizer",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-faiss-pq4-native-odirect-v1",
    },
    "05a:SQ_4bit": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "faiss::ScalarQuantizer(QT_4bit)",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-faiss-sq4-native-odirect-v1",
    },
    "05a:SAQ_B4": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_saq_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "official SAQ B=4 distance kernel",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-saq-b4-native-odirect-v1",
    },
    "05a:Ours_RaBitQ_K1": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_quantizer_port"],
        "source_suite": "01_quantizer_fair",
        "source_kernel": "hnswlib::RaBitQSpace K=1",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05a-rabitq-k1-native-odirect-v1",
    },
    "05b:PQ-DiskANN-Disk": {
        "command": ["experiments/02_diskann_fair/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "DiskANN FixedChunkPQTable 4-bit",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-shared-graph-pq4-release-native-odirect-v1",
    },
    "05b:SQ-DiskANN-Disk": {
        "command": ["experiments/02_diskann_fair/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "DiskANN ScalarQuantizer<4>",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-shared-graph-sq4-release-native-odirect-v1",
    },
    "05b:SAQ-DiskANN-Disk": {
        "command": ["experiments/02_diskann_fair/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "DiskANN spherical::Impl<4>",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-shared-graph-saq4-release-native-odirect-v1",
    },
    "05b:Ours-Disk": {
        "command": ["experiments/02_diskann_fair/target/release/qgraph05_shared_graph_port"],
        "source_suite": "02_diskann_fair",
        "source_kernel": "ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05b-ours-release-native-odirect-v2",
    },
    "05c:Ours-Disk": {
        "command": ["experiments/02_diskann_fair/target/release/qgraph05_shared_graph_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "ExRaBitQ4 symmetric Vamana + DB1 x INT8 production search",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05c-ours-release-native-odirect-v2",
    },
    "05c:SymphonyQG-DiskPort": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_symphonyqg_disk_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "official SymphonyQG FastScan LUT+SIMD",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05c-symphonyqg-fastscan-native-odirect-edgepayload-v3",
    },
    "05c:OG-LVQ-DiskPort": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_og_lvq_disk_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "official SVS LVQ4 distance kernel",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05c-og-lvq-svs-layout-native-odirect-v1",
    },
    "05c:Glass-NSG-DiskPort": {
        "command": ["build/formal_local/05_disk_system_fair/qgraph05_glass_disk_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "official Glass NSG SQ4U distance kernel",
        "port_kind": "algorithm_preserving_disk_port",
        "implementation_fingerprint": "05c-glass-nsg-sq4u-native-odirect-v1",
    },
    "05c:DiskANN-PQ-Disk": {
        "command": ["baselines/diskann/target/release/qgraph05_diskann_port"],
        "source_suite": "03_system_fair",
        "source_kernel": "official diskann-disk PQ search",
        "port_kind": "official_native_disk",
        "implementation_fingerprint": "05c-official-diskann-pq-release-io-uring-v1",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    missing = []
    ready_ports = {}
    for key, spec in PORTS.items():
        command = spec["command"]
        binary = REPO_ROOT / command[0]
        if not binary.is_file():
            missing.append(f"{key}: {command[0]}")
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
