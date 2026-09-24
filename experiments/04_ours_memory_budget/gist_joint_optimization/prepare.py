"""Build an isolated joint-quota diagnostic; never edit the frozen baseline."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
WORK = ROOT / 'work/ours_memory_budget/gist_joint_optimization'
OLD = ROOT / 'work/ours_memory_budget/gist_fixed_factors_budget'


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    native = WORK / 'native'
    if not native.exists():
        shutil.copytree(OLD / 'native', native)
        p = native / 'Cargo.toml'
        p.write_text(p.read_text().replace('ours-gist-fixed-factors-budget', 'ours-gist-joint-optimization').replace('ours_gist_fixed_factors_budget', 'ours_gist_joint_optimization'))
    assert not (WORK / 'build.json').exists(), 'Preserve frozen build'
    shutil.copy2(HERE / 'auto_memory.rs', native / 'auto_memory.rs')
    cmd = ['cargo', 'build', '--release', '--offline', '--manifest-path', str(native / 'Cargo.toml'), '--target-dir', str(ROOT / 'src/graph_core/target')]
    with (WORK / 'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    binary = WORK / 'ours_gist_joint_optimization'
    shutil.copy2(ROOT / 'src/graph_core/target/release' / binary.name, binary)
    (WORK / 'build.json').write_text(json.dumps(dict(binary_sha256=sha(binary), parent_build=sha(OLD/'build.json'), sources={str(p):sha(p) for p in native.iterdir() if p.is_file()}), indent=2)+'\n')


if __name__ == '__main__':
    main()
