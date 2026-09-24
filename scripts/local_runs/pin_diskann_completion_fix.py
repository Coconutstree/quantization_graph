"""Pin a tested DiskANN I/O fix after stopping the old queue."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
run = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/single_test_w32_20260911"
regression = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/diskann_completion_fix_regression"
artifact = json.loads((regression / "result.json").read_text())
assert artifact["status"] == "done"
assert sorted(r["search_width"] for r in artifact["summary_rows"]) == [500, 540, 580]
assert all(r["query_count"] == 800 for r in artifact["summary_rows"])
source = (
    ROOT
    / "experiments/03_disk_system/native_diskann/target/release/qgraph05_diskann_port"
)
def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
if digest(source) != artifact["native_binary_sha256"]:
    matches = [p for p in (source.parent / "deps").glob("qgraph05_diskann_port-*")
               if p.suffix == "" and digest(p) == artifact["native_binary_sha256"]]
    assert len(matches) == 1, "Cannot locate the exact tested binary"
    source = matches[0]
pins = json.loads((run / "binaries.json").read_text())
method = "DiskANN-PQ-Disk"
old = dict(pins[method])
target = Path(old["path"])
backup = target.with_suffix(".before_completion_fix")
if backup.exists():
    raise SystemExit("Fix already pinned; refusing duplicate replacement")
shutil.copy2(target, backup)
shutil.copy2(source, target)
pins[method] = {"path": str(target), "sha256": digest(target)}
(run / "binaries.json").write_text(json.dumps(pins, indent=2) + "\n")
report = {
    "previous": old, "replacement": pins[method],
    "regression_artifact": str(regression / "result.json"),
    "regression_sha256": digest(regression / "result.json"),
    "changes": "Track every I/O completion; drain errors before releasing buffers; reject short reads; correct errno sign",
    "evidence_limit": "Allocator heap corruption reproduced in old binary; high-width regression passing does not conclusively attribute that corruption to the completion path",
    "acceptance": "pending",
}
(run / "diskann_completion_fix.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
