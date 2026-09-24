import importlib.util,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('agnews_tuning',Path(__file__).with_name('run_agnews.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class AGNewsTests(unittest.TestCase):
 def info(self):
  f=m.protocol.flags(m.load(m.REF/'command.json'))
  return {'resolved':{},'binary_sha256':'pinned','validation_order':str(m.ROOT/'results/archive/03_disk_system_unadmitted_20260921/agnews/legacy_snapshot_20260918/raw/legacy_snapshot/manifests/query_order_validate_16842eeb55cc_seed20260813.u32')}
 def test_all_six_ratios(self):self.assertEqual(m.RATIOS,[0,1250,2500,3750,5000,7500])
 def test_train_and_tune_disjoint(self):
  with tempfile.TemporaryDirectory()as d,patch.object(m,'OUT',Path(d)):
   splits=m.split_validation(self.info())
   self.assertFalse(set(splits['train']['source_ids'])&set(splits['tune']['source_ids']))
   for split in splits.values():self.assertEqual(m.protocol.shape(split['query']),(100,1024))
 def test_command_keeps_original_search_settings(self):
  v=self.info();old=m.protocol.flags(m.load(m.REF/'command.json'));new=m.protocol.flags(m.command('test','hybrid',v))
  for flag in ['--dataset','--integration-widths','--integration-beams','--workers','--warmup-queries','--locality-layout-dir','--max-inflight-io','--direct-io','--native-aio']:
   self.assertEqual(old[flag],new[flag])
 def test_tuning_and_training_use_their_own_query_files(self):
  with tempfile.TemporaryDirectory()as d,patch.object(m,'OUT',Path(d)):
   v=self.info();splits=m.split_validation(v)
   for mode,key in [('profile','train'),('hybrid','tune')]:
    f=m.protocol.flags(m.command('a',mode,v,splits[key]));self.assertEqual(f['--query'],splits[key]['query']);self.assertEqual(f['--memory-profile-dir'],str(Path(d)/'runs/a') if mode=='profile' else str(Path(d)/'profiles/a'))
if __name__=='__main__':unittest.main()
