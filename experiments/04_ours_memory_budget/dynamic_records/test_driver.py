import importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('dynamic_driver',Path(__file__).with_name('run.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
class DriverTests(unittest.TestCase):
 def test_original_search_flags_both_datasets(self):
  allowed={'--memory-policy','--memory-cache-bytes','--memory-profile-dir','--memory-stats-dir','--result-json','--query-trace','--run-id','--native-binary-sha256','--implementation-fingerprint'}
  for dataset in ['gist','agnews']:
   s=r.c.sources(dataset);template=r.c.load(s['baseline']/'command.json');before=r.c.protocol.flags(template)
   cmd=r.command(template,Path('/tmp/check-dynamic'),Path('/tmp/check-profile'),'hot_dynamic',s['budget'],'hash')
   after=r.c.protocol.flags(cmd)
   self.assertEqual({k:v for k,v in before.items() if k not in allowed},{k:v for k,v in after.items() if k not in allowed})
   self.assertEqual(after['--memory-policy'],'hot_dynamic');self.assertEqual(int(after['--memory-cache-bytes']),s['budget'])
 def test_new_scheme_not_existing_control(self):
  self.assertEqual(r.c.GROUPS['hot_dynamic'],['hot_dynamic_r1','hot_dynamic_r2'])
  self.assertNotEqual(r.c.BINARY,r.c.ROOT/'work/ours_memory_budget/hybrid_tuning/ours_hybrid_tuning')
if __name__=='__main__':unittest.main()
