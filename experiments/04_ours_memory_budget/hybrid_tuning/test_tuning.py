import importlib.util,json,sys,unittest
from pathlib import Path
P=Path(__file__).with_name('run.py')
spec=importlib.util.spec_from_file_location('hybrid_tuning_driver',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TuningTests(unittest.TestCase):
 def test_pooled_throughput_not_arithmetic_mean(self):
  self.assertAlmostEqual(m.throughput([{'query_count':100,'qps':100},{'query_count':100,'qps':200}]),400/3)
 def test_predeclared_tie_prefers_lower_record_quota(self):
  self.assertEqual(m.select_ratio([(0,98),(1250,99),(2500,100)]),1250)
  self.assertEqual(m.select_ratio([(2500,100),(1250,98.9)]),2500)
 def test_search_parameters_preserved(self):
  m.BINARY=m.protocol.BINARY
  f=m.protocol.flags(m.make_cmd('test','hybrid',None,Path('/tmp/rank'),1247580160))
  old=m.protocol.flags(m.load(m.PRIOR/'baseline/command.json'))
  changed={'--memory-policy','--memory-cache-bytes','--memory-profile-dir','--memory-stats-dir','--result-json','--query-trace','--run-id','--native-binary-sha256','--implementation-fingerprint'}
  self.assertEqual({k:v for k,v in f.items()if k not in changed},{k:v for k,v in old.items()if k not in changed})
 def test_split_order_is_valid_disjoint(self):
  import struct
  ids=struct.unpack('<200I',m.protocol.VALIDATION_ORDER.read_bytes())
  self.assertEqual(set(ids),set(range(200)));self.assertFalse(set(ids[:100])&set(ids[100:]))
if __name__=='__main__':unittest.main()
