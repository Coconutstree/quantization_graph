"""Restore pending queue children only after the acceptance supervisor exits."""
import os
from pathlib import Path
import signal
import time
import sys

def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None

if __name__ == "__main__":
    supervisor = 1420771
    start = identity(supervisor)
    pending = [(pid, identity(pid)) for pid in (1497399, 1497329, 1407979)]
    # An already-running executable cannot adopt a new layout after SIGCONT.
    native = Path("/proc/1497399/cmdline")
    if native.exists() and b"--locality-layout-dir\0" not in native.read_bytes():
        print("BLOCKED: pending DBpedia Ours is legacy; relaunch with verified optimized layout.", flush=True)
        sys.exit(2)
    while start is not None and identity(supervisor) == start:
        time.sleep(5)
    for pid, original in pending:
        if original is not None and identity(pid) == original:
            os.kill(pid, signal.SIGCONT)
