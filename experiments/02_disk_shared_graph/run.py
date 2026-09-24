#!/usr/bin/env python3
"""Disk search on a shared graph."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "disk_bench"))
from orchestrator import run
if __name__ == "__main__":
    raise SystemExit(run(default_layer="02"))
