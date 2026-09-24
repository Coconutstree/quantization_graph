"""Tiny native timing/schema regression. This is diagnostic, not formal admission.

Reuses only the 256-vector fixture/command builders from existing integrations.
The old integration acceptance callback is replaced with explicit assertions that
these unaccepted artifacts remain non-formal; no registry hashes are updated.
"""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent


class NativeTimingTests(unittest.TestCase):
    def run_fixture(self, method):
        file=HERE/f'run_{method}_disk_port_integration.py'
        spec=importlib.util.spec_from_file_location(f'fixture_{method}',file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        checked=[]
        def check_diagnostic(path, **unused):
            a=json.loads(path.read_text())
            self.assertFalse(a['formal_ready'])
            self.assertEqual(a['timing_semantics'],'search_wall_excludes_recall_evaluation')
            for row in a['summary_rows']:
                self.assertIsNone(row['distance_compute_us'])
                self.assertIsNone(row['queue_compute_us'])
                self.assertEqual(row['rerank_us'],0)
                self.assertGreater(row['traversal_wall_us'],0)
                self.assertGreater(row['peak_rss_bytes'],0)
                self.assertGreater(row['qps'],0)
                self.assertTrue(0<=row['recall']<=1)
            traces=[json.loads(line) for line in Path(a['query_trace_path']).read_text().splitlines()]
            self.assertEqual(len(traces),4)
            for t in traces:
                self.assertIsNone(t['distance_compute_us']);self.assertIsNone(t['queue_compute_us'])
                self.assertEqual(t['rerank_us'],0)
                self.assertAlmostEqual(t['latency_us'],t['query_prep_us']+t['traversal_wall_us'],delta=.1)
            self.assertAlmostEqual(a['summary_rows'][0]['recall'],sum(t['recall_at_10'] for t in traces)/len(traces),places=5)
            checked.append(True)
            return a
        with patch.object(module,'validate_artifact',side_effect=check_diagnostic):
            self.assertEqual(module.main(),0)
        self.assertEqual(checked,[True])

    def test_glass(self):self.run_fixture('glass')
    def test_symphonyqg(self):self.run_fixture('symphonyqg')

if __name__=='__main__':unittest.main(verbosity=2)
