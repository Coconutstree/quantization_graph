"""Retired invalid 05C entry point.

Memory-only 03 rows must never be copied into a 05C disk comparison. Run the
strict five-port suite through ``run_disk_suite.py`` instead.
"""

import sys


if __name__ == "__main__":
    print(
        "ERROR: memory baselines cannot enter 05C; use five native disk ports via run_disk_suite.py",
        file=sys.stderr,
    )
    raise SystemExit(2)
