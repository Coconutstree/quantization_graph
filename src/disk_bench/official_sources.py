"""Verify pinned upstream code and explicitly recorded storage-only patches."""
import hashlib
import json
from pathlib import Path
import subprocess


def verify_source(repo: Path, name: str, apply_patches=False):
    dep = json.loads((repo/'baselines/DEPENDENCY_LOCK.json').read_text())['dependencies'][name]
    source = repo/dep['path']
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], text=True)
    if git('rev-parse', 'HEAD').strip() != dep['commit']:
        raise RuntimeError(f'{name}: upstream commit does not match lock')
    for subpath, commit in dep.get('submodules', {}).items():
        actual = subprocess.check_output(['git', '-C', str(source/subpath), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != commit:
            raise RuntimeError(f'{name}: wrong submodule {subpath}')
    patches = dep.get('patches', [])
    expected_diff = ''
    for patch in patches:
        path = repo/patch['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != patch['sha256']:
            raise RuntimeError(f'{name}: recorded patch changed')
        expected_diff += path.read_text()
    diff = git('diff', '--no-ext-diff')
    if apply_patches and not diff and patches:
        for patch in patches:
            subprocess.run(['git', '-C', str(source), 'apply', str(repo/patch['path'])], check=True)
        diff = git('diff', '--no-ext-diff')
    status = git('status', '--porcelain')
    if diff != expected_diff or git('diff', '--cached') or any(line.startswith('??') for line in status.splitlines()):
        raise RuntimeError(f'{name}: source changes differ from the recorded storage-only patches')
    # Also catch dirty/untracked nested submodule contents.
    for subpath in dep.get('submodules', {}):
        if subprocess.check_output(['git', '-C', str(source/subpath), 'status', '--porcelain'], text=True).strip():
            raise RuntimeError(f'{name}: dirty official submodule {subpath}')
    return dep
