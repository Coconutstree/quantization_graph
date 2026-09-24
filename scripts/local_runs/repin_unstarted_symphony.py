"""Replace only an unstarted SymphonyQG binary while the dispatcher is paused."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

root = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).resolve()
method = "SymphonyQG-DiskPort"
run = json.loads((root / "run.json").read_text())
for dataset in run["datasets"]:
    if (root / dataset / method).exists():
        raise SystemExit("Refusing replacement: SymphonyQG method directory already exists")
manifest = root / "binaries.json"
pins = json.loads(manifest.read_text())
target = Path(pins[method]["path"])
previous = dict(pins[method])
backup = target.with_suffix(".before_search_parity_fix")
if backup.exists():
    raise SystemExit("Backup exists; refusing repeated replacement")
shutil.copy2(target, backup)
shutil.copy2(source, target)
with target.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
pins[method] = {"path": str(target), "sha256": digest}
manifest.write_text(json.dumps(pins, indent=2) + "\n")
record = {
    "time": datetime.now(timezone.utc).isoformat(), "method": method,
    "previous": previous, "replacement": pins[method], "backup": str(backup),
    "reason": "Restore native ef, visited, ResultBuffer tie rules and update_results fallback",
    "test": "78 native/disk ordered top-10 comparisons passed on controlled fixtures",
    "acceptance": "pending; fixture parity is not full dataset parity",
}
(root / "symphony_search_fix.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
