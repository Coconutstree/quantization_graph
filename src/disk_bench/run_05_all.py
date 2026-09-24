"""Compatibility alias for the serial, strict formal-suite orchestrator.

The former driver ran datasets concurrently on one device and mixed smoke and
formal outputs.  That behavior is intentionally removed.
"""

from orchestrator import run


if __name__ == "__main__":
    raise SystemExit(run())
