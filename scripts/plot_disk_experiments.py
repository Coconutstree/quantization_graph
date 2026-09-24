#!/usr/bin/env python3
"""Plot contract-validated 01/02/03 or independent 05 memory-budget results."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "disk_bench"))
from plot_05_disk_suite import main
if __name__ == "__main__":
    raise SystemExit(main())
