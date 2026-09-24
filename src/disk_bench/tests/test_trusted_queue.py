"""Preparation must never start performance timing or admit pending baselines."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/run_03_trusted_queue.py'
spec = importlib.util.spec_from_file_location('trusted_queue', SCRIPT)
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


class TrustedQueueTests(unittest.TestCase):
    def test_preparation_only_and_pending_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ports = root / 'ports.json'
            ports.write_text(json.dumps({'ports': {
                '05c:' + method: {'status': 'pending' if slug == 'starling' else 'ready',
                                 'blocked_reason': 'incomplete evidence' if slug == 'starling' else ''}
                for method, slug in queue.METHODS}}))
            argv = ['queue', '--tag', 'test', '--ports', str(ports),
                    '--disk-root', str(root / 'disk'), '--cpu-affinity', '0',
                    '--numa-node', '0', '--prepare-only', '--execute']
            with patch.object(queue, 'ROOT', root), patch('sys.argv', argv), \
                    patch.object(queue.subprocess, 'call', return_value=0) as run:
                self.assertEqual(queue.main(), 2)
                self.assertEqual(run.call_count, 12)
                for call in run.call_args_list:
                    command = call.args[0]
                    self.assertEqual(command[command.index('--phase') + 1], 'export')
                    self.assertEqual(command[command.index('--fixed-beam')+1], '4')
                    self.assertNotIn('QG05_SKIP_EXTERNAL_PARITY', call.kwargs['env'])
            status = json.loads((root / 'results/diagnostics/03_trusted_queue_test/status.json').read_text())
            self.assertFalse(status['full_comparison_ready'])
            self.assertEqual(sum(j['status'] == 'index_prepared' for j in status['jobs']), 12)
            self.assertEqual(sum(j['status'] == 'blocked_registry' for j in status['jobs']), 4)


if __name__ == '__main__':
    unittest.main()
