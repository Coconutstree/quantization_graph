"""Correctness options must not reintroduce reference work into measurement."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from disk_bench import admission as a


class ProcessSeparationTests(unittest.TestCase):
    def test_native_methods_always_measure_without_internal_reference(self):
        for method in ('Ours-Disk', 'DiskANN-PQ-Disk'):
            for skip in ('0', '1'):
                with self.subTest(method=method, skip=skip), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    command = ['/native', '--layer', '05c', '--method', method,
                               '--result-json', str(root / 'measured.json'),
                               '--query-trace', str(root / 'measured.jsonl')]
                    original = list(command)
                    def reference(cmd, **kwargs):
                        self.assertTrue(kwargs['reference'])
                        self.assertNotIn('budget_bytes', kwargs)
                        self.assertEqual(a.option(cmd, '--parity-mode'), 'internal')
                        self.assertEqual(kwargs['env']['QG05_REFERENCE_MMAP'], '0')
                        folder = Path(a.option(cmd, '--result-json')).parent
                        for name in ('result.json', 'result.parity.json', 'queries.jsonl',
                                     'resources.json', 'storage.json'):
                            (folder / name).write_text('{}')
                        return {'status': 'completed'}
                    with ExitStack() as stack:
                        stack.enter_context(patch.dict(os.environ, QG05_FAST='0',
                            QG05_SKIP_EXTERNAL_PARITY=skip, QG05_REFERENCE_MMAP='1'))
                        for name, result in (('prepare_search', {}), ('input_snapshot', {}),
                                             ('unchanged', None), ('parity_document', {})):
                            stack.enter_context(patch.object(a, name, return_value=result))
                        run = stack.enter_context(patch.object(a, 'run_measured', side_effect=reference))
                        official = stack.enter_context(patch.object(a.official_reference, 'prepare'))
                        measured, manifest = a.prepare_reference(command, root / 'measured.json')
                        self.assertEqual(run.call_count, 1)
                        official.assert_not_called()
                    self.assertEqual(command, original)
                    self.assertEqual(a.option(measured, '--parity-mode'), 'external')
                    self.assertEqual(a.canonical(command), a.canonical(measured))
                    doc = json.loads(manifest.read_text())
                    self.assertFalse(doc['performance_sample'])
                    self.assertEqual(doc['external_parity'], 'not_applicable')

    def test_measurement_guard_and_environment(self):
        for method in sorted(a.METHODS):
            cmd = ['/native', '--layer', '05c', '--method', method]
            for mode in (None, 'internal'):
                bad = cmd + (['--parity-mode', mode] if mode else [])
                with self.assertRaisesRegex(ValueError, 'separate reference'):
                    a.measurement_environment(bad, {})
            env = dict(QG05_REFERENCE_MMAP='1', QG05_FAST='1', QG05_FAST_WIDTHS='10',
                       QG05_FAST_WIDTH='10', OMP_NUM_THREADS='32')
            clean = a.measurement_environment(cmd + ['--parity-mode', 'external'], env)
            self.assertEqual(clean['QG05_REFERENCE_MMAP'], '0')
            self.assertEqual(clean['QG05_FAST'], '0')
            self.assertNotIn('QG05_FAST_WIDTHS', clean)
            self.assertNotIn('QG05_FAST_WIDTH', clean)
            self.assertEqual(clean['OMP_NUM_THREADS'], '32')
            self.assertEqual(env['QG05_REFERENCE_MMAP'], '1')


if __name__ == '__main__':
    unittest.main()
