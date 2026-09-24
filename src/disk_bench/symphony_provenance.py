"""Fail-closed provenance checks for the SymphonyQG disk adaptation."""
import json
import hashlib
from pathlib import Path
import subprocess

from .protocol import sha256

COMMIT = '6124ddb34ee4d176edea1bd7ad38d1672343df28'
ROOT = Path(__file__).resolve().parents[2]


def reviewed_changes(path):
    """Load a code-review record, never infer permission from a diff's keywords."""
    if path is None:
        return {}
    document = json.loads(Path(path).read_text())
    if document.get('upstream_commit') != COMMIT:
        raise ValueError('SymphonyQG patch review belongs to a different commit')
    changes = document.get('changes', {})
    for name, change in changes.items():
        if (change.get('category') not in ('instrumentation', 'thread_api') or
                not change.get('rationale') or not change.get('reviewer') or
                any(len(str(change.get(k, ''))) != 64 for k in ('upstream_sha256', 'local_sha256'))):
            raise ValueError(f'SymphonyQG patch review incomplete: {name}')
    return changes


def verify_export(index):
    index = Path(index)
    proof = index / 'export.sha256'
    if not proof.is_file():
        raise ValueError('SymphonyQG export provenance missing; rebuild in a new index directory')
    fields = proof.read_text().split()
    if len(fields) != 5 or fields[0] != 'symphonyqg_export_v1':
        raise ValueError('invalid SymphonyQG export provenance')
    if any(len(h) != 64 or any(c not in '0123456789abcdef' for c in h) for h in fields[1:]):
        raise ValueError('invalid SymphonyQG export digest')
    for name, digest in zip(('node_rows.pages', 'rotator.bin', 'index.meta'), fields[2:]):
        if sha256(index / name) != digest:
            raise ValueError(f'SymphonyQG export changed: {name}')
    return proof


def verify_source(binary):
    """Use a build-bound audit, never an unchecked ready flag in the registry."""
    binary = Path(binary).resolve()
    evidence = binary.with_name('symphonyqg.source.json')
    if not evidence.is_file():
        raise ValueError('SymphonyQG upstream source audit missing; run scripts/audit_symphony_source.py')
    report = json.loads(evidence.read_text())
    if report.get('status') != 'verified' or report.get('upstream_commit') != COMMIT:
        raise ValueError('SymphonyQG upstream source audit is not verified')
    required = {str(binary), str(binary.with_name('qgraph05_symphonyqg_real_reference'))}
    if not required <= set(report.get('binaries', {})):
        raise ValueError('SymphonyQG source audit does not bind both executables')
    for section in ('binaries', 'sources', 'build_files'):
        records = report.get(section, {})
        if not records:
            raise ValueError(f'SymphonyQG source audit lacks {section}')
        for path, digest in records.items():
            if sha256(path) != digest:
                raise ValueError(f'SymphonyQG audited file changed: {path}')
    local = Path(report.get('source_root', ROOT / 'baselines/symphonyqg')).resolve()
    if report.get('source_root'):
        cache_path = binary.parent / 'CMakeCache.txt'
        if str(cache_path) not in report['build_files']:
            raise ValueError('SymphonyQG source root lacks build binding')
        configured = [line.split('=', 1)[1] for line in cache_path.read_text().splitlines()
                      if line.startswith('QGRAPH_SYMPHONY_ROOT:PATH=')]
        if len(configured) != 1 or Path(configured[0]).resolve() != local:
            raise ValueError('SymphonyQG compiled source root differs from audit')
    headers = {str(p.resolve()) for p in (local / 'symqglib').rglob('*') if p.is_file()}
    if not headers <= set(report['sources']):
        raise ValueError('SymphonyQG source audit does not cover all vendor headers')
    # Recheck pinned Git objects, not just a self-asserted "verified" label.
    upstream = report.get('upstream_checkout')
    if not upstream:
        raise ValueError('SymphonyQG source audit lacks upstream Git objects')
    try:
        review_path = report.get('patch_review_path')
        if review_path and sha256(review_path) != report.get('patch_review_sha256'):
            raise ValueError('SymphonyQG patch review changed')
        reviews = reviewed_changes(review_path)
        used_reviews = set()
        command = ['git', '-C', upstream]
        names = subprocess.check_output(command + ['ls-tree', '-r', '--name-only', COMMIT,
                                                   '--', 'symqglib'], stderr=subprocess.DEVNULL).decode().splitlines()
        if not names or {str((local / n).resolve()) for n in names} != headers:
            raise ValueError('SymphonyQG upstream/local source inventory differs')
        for name in names:
            original = subprocess.check_output(command + ['show', COMMIT + ':' + name], stderr=subprocess.DEVNULL)
            before, after = hashlib.sha256(original).hexdigest(), sha256(local / name)
            if before != after:
                review = reviews.get(name, {})
                if review.get('upstream_sha256') != before or review.get('local_sha256') != after:
                    raise ValueError(f'SymphonyQG unreviewed upstream difference: {name}')
                used_reviews.add(name)
        if used_reviews != set(reviews):
            raise ValueError('SymphonyQG patch review does not match current differences')
    except subprocess.CalledProcessError as exc:
        raise ValueError('SymphonyQG pinned upstream Git objects unavailable') from exc
    return evidence
