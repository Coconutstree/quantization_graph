#!/usr/bin/env python3
"""Record toolchain/package versions without collecting environment variables."""
import argparse
import importlib.metadata as metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('numpy', 'h5py', 'matplotlib', 'pybind11', 'scipy', 'tqdm',
            'glass', 'scalable-vs', 'pypdf', 'cmake')

def command(argv):
    if not shutil.which(argv[0]):
        return {'available': False}
    p = subprocess.run(argv, text=True, capture_output=True, timeout=30)
    return {'returncode': p.returncode, 'output': (p.stdout + p.stderr).strip()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    report = {'schema_version': 1, 'python': sys.version,
              'platform': platform.platform(), 'machine': platform.machine(),
              'os_release': Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else None,
              'packages': packages,
              'tools': {name: command([name, '--version']) for name in
                        ('g++-11', 'g++', 'cmake', 'ninja', 'cargo', 'rustc', 'numactl')},
              'cpu': command(['lscpu']),
              'git_head': command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
              'git_dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True)),
              'scope': 'Observed environment; not evidence of a clean-machine installation or formal benchmark.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')

if __name__ == '__main__':
    main()
