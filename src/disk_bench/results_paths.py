"""Resolve migrated historical paths without rewriting signed/hashed evidence."""
from functools import lru_cache
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _migration():
    path = REPO_ROOT / 'results/manifests/path_migration_20260918.json'
    if not path.is_file():
        return str(REPO_ROOT), {}
    doc = json.loads(path.read_text())
    return doc['repository'], doc['prefix_mappings']


def resolve_result_path(value, *, repo_root=None, migration=None):
    """Existing paths win; otherwise apply the longest recorded old prefix."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = Path(value)
    current = path if path.is_absolute() else root / path
    if current.exists():
        return current
    original_root, mappings = _migration() if migration is None else migration
    text = str(path)
    if path.is_absolute():
        for prefix in (str(root), original_root):
            if text.startswith(prefix + '/'):
                text = text[len(prefix) + 1:]
                break
        else:
            return path
    for old, new in sorted(mappings.items(), key=lambda item: len(item[0]), reverse=True):
        if text == old or text.startswith(old + '/'):
            return root / (new + text[len(old):])
    return current
