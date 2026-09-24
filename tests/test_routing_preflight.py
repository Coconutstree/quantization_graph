import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'experiments/04_ours_memory_budget/routing_preflight.py'
spec = importlib.util.spec_from_file_location('routing_preflight', MODULE)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class RoutingPreflightTest(unittest.TestCase):
    def test_boundaries_and_factors(self):
        layout = dict(record_count=2, centroid_bytes=32, routing_bytes=40)
        required = 32 + 2 * (2 + 12 * 1024**2) + 100 + 10 + 40
        for delta, state in [(-1, 'does_not_fit'), (0, 'fits_exactly'), (1, 'fits_with_spare')]:
            result = preflight.assess(layout, required + delta, 2, 100, 10)
            self.assertEqual(result['state'], state)
            self.assertEqual(result['fits'], delta >= 0)
        self.assertFalse(preflight.assess(layout, 1, 2, 100, 10)['nonrouting_fits'])

    def test_headers_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            meta, side = Path(directory) / 'meta', Path(directory) / 'side'
            meta.write_bytes(struct.pack('<8sI7Q', b'QG05OUR1', 0, 64, 1, 2, 32, 16, 8, 12) + bytes(256))
            valid = struct.pack('<8sQQ', b'QG05OSC1', 16, 24) + bytes(40)
            side.write_bytes(valid)
            result = preflight.inspect_sidecar(meta, side)
            self.assertEqual(result['routing_bytes'], 40)
            self.assertEqual(result['factors_bytes'], 24)
            for bad in [valid[:-1], valid + b'x', valid[:12], b'BADMAGIC' + valid[8:],
                        struct.pack('<8sQQ', b'QG05OSC1', 17, 23) + bytes(40)]:
                side.write_bytes(bad)
                with self.assertRaises(ValueError):
                    preflight.inspect_sidecar(meta, side)

    def test_invalid_reservations(self):
        for args in [(0, 1, 0, 0), (1, 0, 0, 0), (1, 1, -1, 0), (1, 1, 0, -1)]:
            with self.assertRaises(ValueError):
                preflight.assess({}, *args)


if __name__ == '__main__':
    unittest.main()
