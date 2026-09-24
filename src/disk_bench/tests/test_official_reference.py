"""Reject modified or incomplete independent official-reference evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from disk_bench.official_reference import ids, verify
from disk_bench.protocol import sha256


class OfficialReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'qgraph05_glass_real_reference'
        self.binary.write_bytes(b'pinned official executable')
        self.command = [str(self.root / 'qgraph05_glass_disk_port'), '--method',
                        'Glass-NSG-DiskPort', '--disk-index-dir', '/index', '--query', '/query']
        self.native = dict(io_backend='reference_mmap_not_performance',
                           summary_rows=[dict(search_width=10)], resident_bytes=123)
        self.save('reference.native.json', self.native)
        self.save('result.json', dict(self.native, implementation_parity='passed', parity={}))
        row = dict(search_width=10, query_id=0, result_ids=list(range(10)))
        self.save('official.jsonl', row)
        self.save('queries.jsonl', row)
        self.resource = dict(status='completed', exit_code=0, reference_only=True, finished_unix=123,
                             binary_sha256=sha256(self.binary),
                             command=[str(self.binary), '/index', '/query',
                                      str(self.root / 'official.jsonl'),
                                      str(self.root / 'temporary_official.index'), '10', '1'])
        self.save('official.resources.json', self.resource)
        self.manifest = {'external_parity': 'performed', 'files': {p.name: {'path': str(p)} for p in self.root.iterdir()}}

    def save(self, name, obj):
        (self.root / name).write_text(json.dumps(obj) + '\n')

    def test_valid_evidence(self):
        self.assertEqual(verify(self.manifest, self.command), 123)

    def test_missing_or_skipped_official_pass(self):
        for value in (None, 'skipped_user'):
            self.manifest['external_parity'] = value
            with self.assertRaisesRegex(ValueError, 'must be performed'):
                verify(self.manifest, self.command)

    def test_missing_official_trace(self):
        del self.manifest['files']['official.jsonl']
        with self.assertRaisesRegex(ValueError, 'files missing'):
            verify(self.manifest, self.command)

    def test_failed_official_process(self):
        self.resource['exit_code'] = 1
        self.save('official.resources.json', self.resource)
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            verify(self.manifest, self.command)

    def test_changed_official_order(self):
        self.save('official.jsonl', dict(search_width=10, query_id=0, result_ids=list(reversed(range(10)))))
        with self.assertRaisesRegex(ValueError, 'result mismatch'):
            verify(self.manifest, self.command)

    def test_changed_official_binary(self):
        self.binary.write_bytes(b'different executable')
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            verify(self.manifest, self.command)

    def test_changed_native_reference(self):
        self.save('result.json', dict(self.native, resident_bytes=124))
        with self.assertRaisesRegex(ValueError, 'modified reference'):
            verify(self.manifest, self.command)

    def test_wrong_query_in_official_command(self):
        self.resource['command'][2] = '/different-query'
        self.save('official.resources.json', self.resource)
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            verify(self.manifest, self.command)

    def test_duplicate_or_empty_queries(self):
        p = self.root / 'official.jsonl'
        p.write_text(p.read_text() * 2)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            ids(p)
        p.write_text('')
        with self.assertRaisesRegex(ValueError, 'empty'):
            ids(p)


if __name__ == '__main__':
    unittest.main()
