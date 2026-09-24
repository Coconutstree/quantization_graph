import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'experiments/03_disk_system/adapters'))
from run_starling_existing_layout import export, native_rows, starling_memory_lower_bound


class StarlingExportTest(unittest.TestCase):
    def test_gist_memory_bound_rejects_2g_32_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p/'index_pq_compressed.bin').write_bytes(struct.pack('<II',1000000,31))
            (p/'index_partition.bin').write_bytes(struct.pack('<QQQ',1,1000000,1000000))
            (p/'query.bin').write_bytes(struct.pack('<II',800,960))
            (p/'nav.data').write_bytes(struct.pack('<II',10091,960))
            bound = starling_memory_lower_bound(p/'index',p/'query.bin',p/'nav',32)
            self.assertEqual(bound['known_lower_bound_bytes'],2148741824)
            self.assertGreater(bound['known_lower_bound_bytes'],2**31)
            self.assertLess(bound['known_lower_bound_bytes'],4*2**30)
            self.assertFalse(bound['accounting_complete'])

    def test_native_log_retains_p999_not_p99(self):
        rows = native_rows(ROOT/'docs/analysis/official_disk_baselines_20260916/starling_search.log')
        self.assertEqual(set(rows), {10, 40})
        self.assertEqual(rows[10]['latency_p999_us'], 943)
        self.assertNotIn('latency_p99_us', rows[10])

    def test_order_recall_and_missing_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            out = p/'Starling-Disk'; out.mkdir()
            ref = p/'Ours-Disk'; ref.mkdir()
            work = p/'work'; work.mkdir()
            keys = dict(latency_us=1, io_requests=1, query_id=0, result_ids=[])
            (ref/'queries.jsonl').write_text(json.dumps(keys)+'\n')
            (work/'05_search_disk_index.log').write_text('10 1 100 10 30 5 0 20 21 22\n')
            (work/'index_pq_compressed.bin').write_bytes(struct.pack('<II', 20, 31))
            query = p/'query.fvecs'; query.write_bytes(struct.pack('<iffiff', 2, 0, 1, 2, 1, 0))
            gt = p/'gt.ivecs'
            np.array([[10,*range(10)],[10,*range(1,11)]], dtype='<u4').tofile(gt)
            ids = np.array([list(range(10)),list(range(10))],dtype='<u4')
            (work/'result_10_idx_uint32.bin').write_bytes(struct.pack('<II',2,10)+ids.tobytes())
            result = dict(base_count=20,formal_ready=False,workers=16,search_dram_budget_gib=4)
            reference = dict(summary_rows=[dict(latency_p50_us=1,latency_p95_us=1,latency_p99_us=1)])
            export('gist',out,work,result,reference,query,gt,[1,0],[10])
            trace = [json.loads(x) for x in (out/'queries.jsonl').read_text().splitlines()]
            self.assertEqual([x['query_id'] for x in trace],[1,0])
            self.assertEqual([x['recall_at_10'] for x in trace],[0.9,1])
            self.assertEqual(trace[0]['workers'],16)
            self.assertEqual(trace[0]['search_dram_budget_gib'],4)
            self.assertTrue(all(x['latency_us'] is None and x['io_requests'] is None for x in trace))
            self.assertAlmostEqual(result['summary_rows'][0]['recall'],0.95)
            self.assertIsNone(result['summary_rows'][0]['latency_p99_us'])
            self.assertEqual(result['pq_bytes'],31)
            self.assertFalse(result['formal_ready'])


if __name__ == '__main__':
    unittest.main()
