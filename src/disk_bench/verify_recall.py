"""Retired Python parity checker.

Formal parity must be produced by each native port against the corresponding
01/02/03 memory implementation, then verified by ``native_contract.py``.
"""

import sys


if __name__ == "__main__":
    print(
        "ERROR: Python reference parity is not a formal 05 validation; use --phase validate",
        file=sys.stderr,
    )
    raise SystemExit(2)
