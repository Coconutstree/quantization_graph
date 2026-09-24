import json
import pathlib
import struct
import tempfile
import unittest

from export_ours_locality_layout import export, offset, write_records

class LayoutTests(unittest.TestCase):
    def test_roundtrip_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / 'source'
            output = pathlib.Path(tmp) / 'output'
            source.mkdir()
            (source / 'index.meta').write_text('ours_record_count=4\nrecord_bytes=12\nmax_degree=2\nours_compact_record_bytes=529\nours_residual_record_bytes=648\n')
            rows = [struct.pack('<III', 2, 2, 1), struct.pack('<III', 1, 3, 0), struct.pack('<III', 1, 1, 0), struct.pack('<III', 0, 0, 0)]
            write_records(source / 'shared_graph.pages', rows, 12)
            write_records(source / 'ours_full4_residual.pages', (bytes([i]) * 1177 for i in range(4)), 1177)
            for name in ['ours_quantizer.bin', 'ours_db1_sidecar.bin']:
                (source / name).write_bytes(b'fixture')
            export(source, output)
            self.assertEqual(struct.unpack('<4I', (output / 'slot_to_id.u32').read_bytes()), (0, 2, 1, 3))
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertTrue(manifest['all_records_verified'])
            self.assertFalse(manifest['runtime_supported'])
            with self.assertRaises(FileExistsError):
                export(source, output)

    def test_large_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'records'
            write_records(path, [b'a' * 5000, b'b' * 5000], 5000)
            self.assertEqual(offset(1, 5000), 8192)
            self.assertEqual(path.stat().st_size, 16384)
            self.assertEqual(path.read_bytes()[8192:13192], b'b' * 5000)

if __name__ == '__main__':
    unittest.main()
