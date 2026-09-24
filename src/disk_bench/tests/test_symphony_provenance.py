import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from disk_bench.protocol import sha256, aggregate_repeats, PROTOCOL_ID
from disk_bench.symphony_provenance import verify_export, verify_source, reviewed_changes, COMMIT


class SymphonyProvenanceTests(unittest.TestCase):
    def test_export_hashes_and_legacy_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'rebuild'):
                verify_export(root)
            names = ('node_rows.pages', 'rotator.bin', 'index.meta')
            for name in names:
                (root / name).write_bytes(name.encode())
            (root / 'export.sha256').write_text('\n'.join(['symphonyqg_export_v1', 'a'*64] +
                [sha256(root / name) for name in names]))
            verify_export(root)
            for name in names:
                original = (root / name).read_bytes()
                (root / name).write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'export changed'):
                    verify_export(root)
                (root / name).write_bytes(original)

    def test_source_audit_missing_or_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'qgraph05_symphonyqg_disk_port'
            with self.assertRaisesRegex(ValueError, 'audit missing'):
                verify_source(binary)
            (root / 'symphonyqg.source.json').write_text(json.dumps({'status': 'blocked_local_changes'}))
            with self.assertRaisesRegex(ValueError, 'not verified'):
                verify_source(binary)

    def test_skip_environment_cannot_admit_diagnostic_rows(self):
        with patch.dict(os.environ, QG05_SKIP_EXTERNAL_PARITY='1'):
            with self.assertRaisesRegex(ValueError, 'non-formal'):
                aggregate_repeats([dict(protocol_id=PROTOCOL_ID, formal_ready=False)])

    def test_changed_binary_rejected_by_source_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'qgraph05_symphonyqg_disk_port'
            reference = root / 'qgraph05_symphonyqg_real_reference'
            binary.write_bytes(b'build-one'); reference.write_bytes(b'reference-one')
            report = dict(status='verified', upstream_commit=COMMIT,
                          binaries={str(p): sha256(p) for p in (binary, reference)})
            (root / 'symphonyqg.source.json').write_text(json.dumps(report))
            binary.write_bytes(b'build-two')
            with self.assertRaisesRegex(ValueError, 'audited file changed'):
                verify_source(binary)

    def test_algorithm_change_cannot_be_marked_as_reviewed_instrumentation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.json'
            change = dict(category='algorithm_change', rationale='different pruning', reviewer='audit',
                          upstream_sha256='a'*64, local_sha256='b'*64)
            path.write_text(json.dumps(dict(upstream_commit=COMMIT, changes={'qg.hpp':change})))
            with self.assertRaisesRegex(ValueError, 'review incomplete'):
                reviewed_changes(path)

    def test_pristine_checkout_is_bound_to_compiled_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'official'
            header = source / 'symqglib/qg.hpp'
            header.parent.mkdir(parents=True)
            header.write_bytes(b'official pinned bytes')
            build = root / 'build'
            build.mkdir()
            binary = build / 'qgraph05_symphonyqg_disk_port'
            reference = build / 'qgraph05_symphonyqg_real_reference'
            binary.write_bytes(b'measured'); reference.write_bytes(b'official-reference')
            cache = build / 'CMakeCache.txt'
            cache.write_text(f'QGRAPH_SYMPHONY_ROOT:PATH={source}\n')
            report = dict(status='verified', upstream_commit=COMMIT,
                          source_root=str(source), upstream_checkout=str(source),
                          sources={str(header): sha256(header)},
                          binaries={str(p): sha256(p) for p in (binary, reference)},
                          build_files={str(cache): sha256(cache)})
            manifest = build / 'symphonyqg.source.json'
            manifest.write_text(json.dumps(report))
            def git(argv, **kwargs):
                return b'symqglib/qg.hpp\n' if 'ls-tree' in argv else b'official pinned bytes'
            with patch('disk_bench.symphony_provenance.subprocess.check_output', side_effect=git):
                self.assertEqual(verify_source(binary), manifest)
                header.write_bytes(b'changed distance kernel')
                report['sources'][str(header)] = sha256(header)
                manifest.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'unreviewed upstream difference'):
                    verify_source(binary)
                header.write_bytes(b'official pinned bytes')
                report['sources'][str(header)] = sha256(header)
                cache.write_text(f'QGRAPH_SYMPHONY_ROOT:PATH={root / "legacy"}\n')
                report['build_files'][str(cache)] = sha256(cache)
                manifest.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'compiled source root differs'):
                    verify_source(binary)


if __name__ == '__main__':
    unittest.main()
