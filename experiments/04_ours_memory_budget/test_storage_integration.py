"""Preparation failures must prevent search; performance guard rejects slow baselines."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import run

class PreparationIntegrationTests(unittest.TestCase):
 def test_failed_preparation_never_starts_search(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with patch.object(run,'OUT',root), patch.object(run,'assert_build_current'), patch.object(run,'assert_inputs_unchanged'), patch.object(run,'make_command',return_value=['fake']), patch.object(run,'driver_hashes',return_value={}), patch.object(run,'sha',return_value='digest'), patch.object(run,'prepare_search',side_effect=OSError('direct read failed')), patch.object(run,'load_measure') as measure:
    with self.assertRaisesRegex(OSError,'direct read failed'):
     run.execute('baseline',{'input_hashes':{},'reference_command_sha256':'digest'},0)
    measure.assert_not_called()
    self.assertFalse((root/'baseline/acceptance.json').exists())
 def test_preparation_precedes_search_and_is_recorded(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);events=[]
   def prepare(cmd,path):
    events.append('prepare');path.write_text('{}');return {'status':'completed','files':[]}
   def measure(cmd,folder,budget):
    events.append('search')
    identity=json.loads((folder/'identity.json').read_text())
    self.assertEqual(identity['storage_precondition_protocol'],run.STORAGE_PROTOCOL)
    return {'user_address_space_budget_passed':False}
   with patch.object(run,'OUT',root), patch.object(run,'assert_build_current'), patch.object(run,'assert_inputs_unchanged'), patch.object(run,'make_command',return_value=['fake']), patch.object(run,'driver_hashes',return_value={}), patch.object(run,'sha',return_value='digest'), patch.object(run,'prepare_search',side_effect=prepare), patch.object(run,'load_measure',return_value=Mock(measure=measure)):
    with self.assertRaisesRegex(ValueError,'memory budget'):
     run.execute('baseline',{'input_hashes':{},'reference_command_sha256':'digest'},0)
   self.assertEqual(events,['prepare','search'])
 def test_performance_guard_accepts_recovered_rejects_tenfold_drop(self):
  ref=run.ROOT/'results/diagnostics/03_disk_system/layout_state_control/gist/20260919_uniform03_current_full/result.json'
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);result=json.loads(ref.read_text());(root/'result.json').write_text(json.dumps(result))
   run.check_baseline_performance(root)
   for row in result['summary_rows']:row['qps']/=10
   (root/'result.json').write_text(json.dumps(result))
   with self.assertRaisesRegex(ValueError,'performance drift'):run.check_baseline_performance(root)
   self.assertFalse(json.loads((root/'performance_gate.json').read_text())['passed'])

if __name__=='__main__':unittest.main()
