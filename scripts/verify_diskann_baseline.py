#!/usr/bin/env python3
"""Verify that baselines/diskann is the pinned official tree plus declared patches only.

Two modes:

  --write-manifest --source <pristine checkout>
        Record sha256 of every upstream file (excluding VCS, build output and LFS
        test fixtures) into baselines/diskann.manifest.json.

  (default)
        Walk baselines/diskann and require every file to be either byte-identical
        to the manifest (pinned upstream content) or listed as a patched file with
        the recorded post-patch hash.  Then reverse-check each declared patch with
        `git apply --check -R` so the patch set provably equals the deviation.

Any undeclared difference, missing file or extra file fails the check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREE = ROOT / "baselines/diskann"
MANIFEST = ROOT / "baselines/diskann.manifest.json"
LOCK = ROOT / "baselines/DEPENDENCY_LOCK.json"


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0] in {".git", "target"} or parts[0].startswith("target"):
        return True
    return "test_data" in parts


def walk(root: Path) -> list[str]:
    found = []
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if excluded(rel):
            continue
        found.append(rel)
    return sorted(found)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def diskann_entry() -> dict:
    lock = json.loads(LOCK.read_text())
    try:
        return lock["dependencies"]["diskann"]
    except KeyError as exc:  # pragma: no cover - configuration error
        raise SystemExit(f"DEPENDENCY_LOCK.json is missing dependencies.diskann: {exc}")


def write_manifest(source: Path) -> int:
    entry = diskann_entry()
    tracked = subprocess.run(
        ["git", "-C", str(source), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    files = {}
    for rel in tracked:
        if excluded(rel):
            continue
        path = source / rel
        if not path.is_file():
            continue
        files[rel] = sha256(path)
    payload = {
        "schema_version": 1,
        "note": "sha256 of the pinned upstream tree; regenerate with scripts/verify_diskann_baseline.py --write-manifest",
        "source": {
            "url": "https://github.com/microsoft/DiskANN",
            "commit": entry["commit"],
            "version": "0.55.0",
        },
        "excluded": [".git/", "target/", "**/test_data/"],
        "files": dict(sorted(files.items())),
    }
    MANIFEST.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {MANIFEST.relative_to(ROOT)} with {len(files)} files")
    return 0


def verify() -> int:
    manifest = json.loads(MANIFEST.read_text())
    pinned = manifest["files"]
    entry = diskann_entry()
    patches = entry.get("patches") or []
    if not patches:
        raise SystemExit("DEPENDENCY_LOCK.json declares no diskann patches")

    patched: dict[str, str] = {}
    new_files: set[str] = set()
    for patch in patches:
        for rel, digest in patch["patched_files"].items():
            if rel in patched and patched[rel] != digest:
                raise SystemExit(f"conflicting post-patch hashes for {rel}")
            patched[rel] = digest
            if rel not in pinned:
                new_files.add(rel)

    problems: list[str] = []
    present = set(walk(TREE))
    expected = set(pinned) | new_files
    for rel in sorted(expected - present):
        problems.append(f"missing: {rel}")
    for rel in sorted(present - expected):
        problems.append(f"undeclared extra file: {rel}")
    for rel in sorted(present & expected):
        digest = sha256(TREE / rel)
        want = patched.get(rel, pinned.get(rel))
        if digest != want:
            kind = "patched" if rel in patched else "pinned"
            problems.append(f"{kind} content differs: {rel}")

    for patch in patches:
        check = subprocess.run(
            ["git", "apply", "--check", "-R", patch["path"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            problems.append(
                f"patch is not exactly applied: {patch['path']}: {check.stderr.strip()}"
            )

    if problems:
        print("diskann baseline verification FAILED:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print(
        "diskann baseline OK: "
        f"{len(pinned)} pinned files + {len(patched)} patched files "
        f"({len(patches)} declared patches) at {entry['commit'][:12]}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.write_manifest:
        if not args.source:
            raise SystemExit("--write-manifest requires --source <pristine checkout>")
        return write_manifest(args.source)
    return verify()


if __name__ == "__main__":
    sys.exit(main())
