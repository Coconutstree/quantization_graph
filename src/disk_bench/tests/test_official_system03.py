"""Numerical and provenance rejection for the native official bridge."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from disk_bench.official_system03 import summarize,configuration
from disk_bench.common import write_fvecs,write_ivecs
from disk_bench.native_contract import ContractError

class Official03Tests(unittest.TestCase):
    def test_ordered_reference_and_distances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=np.arange(20,dtype='f4').reshape(20,1)
            write_fvecs(root/'base.fvecs',base)
            write_fvecs(root/'query.fvecs',np.array([[1.],[0.]],dtype='f4'))
            ids=np.array([np.argsort((base[:,0]-q)**2)[:10] for q in (1.,0.)],dtype='<u4')
            ds=np.array([(base[r,0]-q)**2 for r,q in zip(ids,(1.,0.))],dtype='<f4')
            write_ivecs(root/'gt.ivecs',ids[::-1])
            def save(name,a): (root/name).write_bytes(struct.pack('<II',2,10)+a.tobytes())
            save('result_ids.bin',ids);save('reference.bin',ids);save('result_distances.bin',ds)
            traces=[dict(query_id=i,search_width=20,search_wall_seconds=1.,latency_us=100.,io_requests=2,distance_evaluations=20) for i in range(2)]
            (root/'stats.jsonl').write_text('\n'.join(map(json.dumps,traces)))
            args=(root,root/'gt.ivecs',root/'base.fvecs',root/'reference.bin',np.array([1,0]),20,32)
            summary,rows=summarize(*args)
            self.assertEqual(summary['recall'],1.)
            self.assertEqual([r['query_id'] for r in rows],[1,0])
            self.assertIsNone(summary['bytes_read_per_query'])
            save('result_distances.bin',ds+1)
            with self.assertRaisesRegex(ContractError,'distances'):summarize(*args)
            save('result_distances.bin',ds);save('reference.bin',ids[:,::-1].copy())
            with self.assertRaisesRegex(ContractError,'ordered results'):summarize(*args)

    def test_pinned_official_configuration_sources(self):
        s=configuration('Starling-Disk','gist');a=configuration('AiSAQ-Disk','gist')
        self.assertEqual((s['degree'],s['build_width'],s['nav_width']),(48,128,0))
        self.assertEqual((a['degree'],a['build_width'],a['inline_pq'],a['rearrange']),(64,125,64,False))
        self.assertEqual(s['node_cache'],0)
        self.assertIn('config_sample.sh',s['parameter_source'])
        self.assertIn('AiSAQ_index.md',a['parameter_source'])

if __name__=='__main__':unittest.main()
