#!/usr/bin/env python3
"""Small generated-data regression for the graph-only CLI."""
from pathlib import Path
import random
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
binary = root / "src/graph_core/target/release/run_diskann_fair"
with tempfile.TemporaryDirectory(prefix="qgraph-build-only-") as directory:
    work = Path(directory)
    data = work / "data/toy"
    data.mkdir(parents=True)
    rng = random.Random(42)
    with (data / "toy_base.fvecs").open("wb") as stream:
        for _ in range(512):
            stream.write(struct.pack("<i64f", 64, *(rng.random() for _ in range(64))))
    (data / "toy_query.fvecs").write_bytes(struct.pack("<i64f", 64, *([0.0] * 64)))
    (data / "toy_groundtruth.ivecs").write_bytes(struct.pack("<i10i", 10, *range(10)))
    for method in ["PQ", "Ours"]:
        output = work / method
        result = subprocess.run([str(binary), "--dataset", "toy", "--data-root", str(work / "data"),
            "--out-root", str(output), "--methods", method, "--build-only", "--shared-graph",
            "--max-degree", "16", "--build-beam", "32", "--build-threads", "2",
            "--threads", "2", "--search-list-sizes", "10,20", "--refine-passes", "1"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:])
        assert "metrics=not_run" in result.stdout, result.stdout[-2000:]
        graphs = list(output.rglob("*.graph.bin"))
        assert len(graphs) == 1 and graphs[0].stat().st_size > 0, graphs
        assert not list(output.rglob("diskann_fair_raw.csv")), "build-only must not publish query measurements"
        print(f"PASS {method}: graph produced, query sweep disabled, no performance CSV")
