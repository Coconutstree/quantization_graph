import copy
import json
from pathlib import Path
import tempfile
import unittest
from cache_selection_policy import choose,verify_lock,digest,cache_audit,RULE

def runs(ratios=(1.06,1.06)):
 return [dict(strategy=s,round=r,status='passed',points=[dict(search_width=w,query_count=200,qps=q*(ratios[r] if s=='records_only' else 1)) for w,q in [(60,100),(100,70),(180,40)]]) for r in (0,1) for s in ('graph_first','records_only')]

class SelectionTests(unittest.TestCase):
 def test_threshold_and_direction(self):
  self.assertEqual(choose(runs((1.05,1.05)))['selected'],'records_only')
  self.assertEqual(choose(runs((1.049,1.049)))['selected'],'graph_first')
  self.assertEqual(choose(runs((.99,1.3)))['selected'],'graph_first')
 def test_pool_time_not_qps_average(self):
  r=runs((2,2));r[2]['points'][0]['qps']=25;r[3]['points'][0]['qps']=50
  self.assertAlmostEqual(choose(r)['merged_qps']['graph_first']['60'],40)
 def test_one_failure_and_invalid_coverage(self):
  r=runs();r[0]['status']='failed';self.assertEqual(choose(r)['selected'],'records_only')
  r[1]['status']='failed'
  with self.assertRaises(ValueError):choose(r)
  r=runs();r[0]['points'].pop()
  with self.assertRaises(ValueError):choose(r)
 def test_lock_tamper(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'input';p.write_text('a');lock=dict(identity={'workers':32},decision={'selected':'records_only','rule':RULE},files={str(p):digest(p)})
   verify_lock(lock,{'workers':32},'records_only')
   with self.assertRaises(ValueError):verify_lock(lock,{'workers':16},'records_only')
   p.write_text('b')
   with self.assertRaises(ValueError):verify_lock(lock,{'workers':32},'records_only')
 def test_actual_cache_accounting(self):
  root=Path(__file__).resolve().parents[1];s=json.loads((root/'results/diagnostics/gist_cache_three_widths_20260923/status.json').read_text())
  for j in s['jobs']:
   if j['status']!='admitted':continue
   a=json.loads(Path(j['artifact']).read_text());cache_audit(a)
   a['ours_record_cache_reserved_bytes']=a['ours_route_plan']['budget_bytes']
   with self.assertRaises(ValueError):cache_audit(a)
  a['ours_record_cache_reserved_bytes']=0;a['ours_cache_allocation']='records_only';a['ours_graph_cache_bytes']=4096
  with self.assertRaises(ValueError):cache_audit(a)

if __name__=='__main__':unittest.main()
