#!/usr/bin/env python3
"""Run disk experiments 01/02/03, or the independent 05 memory-budget experiment."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "disk_bench"))
from orchestrator import run
if __name__ == "__main__":
    raise SystemExit(run())
