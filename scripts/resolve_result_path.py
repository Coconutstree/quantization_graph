#!/usr/bin/env python3
"""Print the current location of a path recorded in historical result evidence."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/disk_bench'))
from results_paths import resolve_result_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    args = parser.parse_args()
    path = resolve_result_path(args.path)
    print(path)
    raise SystemExit(0 if path.exists() else 1)
