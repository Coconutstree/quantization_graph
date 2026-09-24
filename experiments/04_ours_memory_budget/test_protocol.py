import copy,json,tempfile,unittest
from pathlib import Path
import protocol,run

class ProtocolTests(unittest.TestCase):
 def setUp(self):
  self.v=json.loads((protocol.OUT/'verification.json').read_text())
  self.cmd=run.make_command('baseline',self.v)
 def test_original_full_configuration(self):
  f=protocol.flags(self.cmd)
  self.assertEqual(list(map(int,f['--integration-widths'].split(','))),protocol.WIDTHS)
  self.assertEqual(len(protocol.WIDTHS),40)
  self.assertEqual(f['--integration-beams'],'1');self.assertEqual(f['--workers'],'32')
  self.assertEqual(protocol.shape(f['--query']),(800,960))
  protocol.check_locked(self.cmd)
 def test_parameter_changes_are_rejected(self):
  for flag,value in [('--integration-widths','30,100,300'),('--workers','16'),('--warmup-queries','0'),('--integration-beams','4'),('--query',str(protocol.ROOT/'data/gist/gist_query.fvecs')),('--locality-layout-dir','/tmp/other'),('--memory-cache-bytes','268435456')]:
   with self.subTest(flag=flag):
    cmd=self.cmd.copy();cmd[cmd.index(flag)+1]=value
    with self.assertRaises(ValueError):protocol.check_locked(cmd)
 def test_profile_uses_original_validation_not_test(self):
  cmd=run.make_command('profile',self.v);f=protocol.flags(cmd)
  self.assertEqual(protocol.shape(f['--query']),(200,960));self.assertEqual(f['--query-order'],str(protocol.VALIDATION_ORDER));protocol.check_locked(cmd,'profile')
 def test_cache_allowance_uses_vmpeak_not_rss(self):
  m={'user_address_space_budget_passed':True,'rlimit_as_bytes':protocol.LIMIT,'sampled_high_water_bytes':{'VmPeak':800<<20},'kernel_wait4_peak_rss_bytes':400<<20}
  self.assertEqual(protocol.cache_allowance(m),(2048-800-64)<<20)
  m['user_address_space_budget_passed']=False
  with self.assertRaises(ValueError):protocol.cache_allowance(m)
 def test_trace_mismatch_and_duplicate_are_detected(self):
  row=dict(search_width=30,query_id=1,result_ids=list(range(10)),recall_at_10=1.,visited_nodes=20,distance_evaluations=30,db1_checks=40,db1_survivors=29,full4_candidates=30)
  with tempfile.TemporaryDirectory()as d:
   a,b=Path(d)/'a.jsonl',Path(d)/'b.jsonl';a.write_text(json.dumps(row)+'\n');b.write_text(a.read_text());self.assertTrue(run.parity(a,b)['passed'])
   changed=copy.deepcopy(row);changed['result_ids'][0]=99;b.write_text(json.dumps(changed)+'\n');self.assertFalse(run.parity(a,b)['passed'])
   b.write_text(a.read_text()*2)
   with self.assertRaises(ValueError):run.parity(a,b)
 def test_profile_evidence_recomputes_scores_and_rejects_bias(self):
  import numpy as np
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d, patch.object(run,'WIDTHS',[10,580]):
   folder=Path(d);(folder/'memory_stats').mkdir()
   (folder/'profile_policy.json').write_text(json.dumps({'schema':2,'widths':[10,580],'exclude_warmup':True}))
   for w,counts in [(10,[1,0]),(580,[0,100])]:
    for kind in ['graph','compact','residual']:
     np.array(counts,dtype='<u8').tofile(folder/f'L{w}_{kind}_counts.u64')
    (folder/'memory_stats'/f'L{w}.json').write_text(json.dumps({'operations':{k:{'logical_requests':sum(counts)} for k in ['graph','compact','residual']}}))
   for kind in ['graph','compact','residual','payload']:
    np.array([0.5,0.5],dtype='<f8').tofile(folder/f'{kind}_scores.f64')
   run.validate_profile(folder,expected_nodes=2)
   np.array([1/101,100/101],dtype='<f8').tofile(folder/'graph_scores.f64')
   with self.assertRaisesRegex(ValueError,'normalization'):run.validate_profile(folder,expected_nodes=2)
   np.array([0.5,0.5],dtype='<f8').tofile(folder/'graph_scores.f64')
   np.array([0.9,0.1],dtype='<f8').tofile(folder/'payload_scores.f64')
   with self.assertRaisesRegex(ValueError,'payload'):run.validate_profile(folder,expected_nodes=2)
if __name__=='__main__':unittest.main()
