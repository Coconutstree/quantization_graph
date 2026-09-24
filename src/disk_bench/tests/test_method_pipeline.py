"""Method scheduling must freeze independent locks and defer full aggregation."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator as o


class MethodPipelineTests(unittest.TestCase):
    def test_fixed_pipeline_only_profiles_ours_then_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            methods = ('Ours-Disk', 'DiskANN-PQ-Disk', 'Glass-NSG-DiskPort', 'SymphonyQG-DiskPort')
            args = argparse.Namespace(methods=methods, phase='run', fixed_beam=4, storage_modes=('auto',))
            calls = []
            preparation = dict(method='Ours-Disk', storage_mode='hybrid_disk', ours_route_plan={'mode':'full1bit'},
                               ours_hot_profile_manifest='/validation/profile.json', ours_hot_profile_sha256='abc',
                               ours_record_cache_policy='validation_hot_then_dynamic_fifo16_v1', pca_assets_sha256='')
            lock_contents = []
            def run(**kw):
                a = kw['args']
                prep = getattr(a, 'fixed_preparation', False)
                calls.append((a.methods, a.phase, prep))
                if prep:
                    self.assertEqual(a.methods, ('Ours-Disk',))
                    self.assertEqual(a.phase, 'tune')
                    return [(root/'preparation.json', preparation)]
                self.assertEqual(a.phase, 'run')
                lock = a.tuning_lock_override
                lock_contents.append(lock.read_bytes())
                d = json.loads(lock.read_text())
                self.assertEqual(d['selection_policy'], 'user_fixed_no_validation_performance_sweep')
                self.assertEqual(d['selected']['Ours-Disk::hybrid_disk']['config_id'], 'beam4')
                self.assertEqual(d['selected']['DiskANN-PQ-Disk::hybrid_disk']['config_id'], 'beam4')
                self.assertIsNone(d['selected']['Ours-Disk::hybrid_disk']['validation_artifact'])
                self.assertEqual(d['selected']['Ours-Disk::hybrid_disk']['ours_hot_profile_manifest'], '/validation/profile.json')
            with patch.object(o, '_run_layer_dataset', side_effect=run), patch.object(o, 'dataset_run_root', return_value=root):
                o._run_method_pipeline(args=args, ports={}, run_root=root, layer='05c', dataset='gist')
                self.assertEqual(calls, [(('Ours-Disk',), 'tune', True)] +
                                 [((m,), 'run', False) for m in methods] + [(methods, 'run', False)])
                self.assertTrue(all(b == lock_contents[0] for b in lock_contents))
                args.fixed_beam = 8
                with self.assertRaisesRegex(o.ContractError, 'fixed parameter lock changed'):
                    o._fixed_parameter_lock(args=args, run_root=root, layer='05c', dataset='gist',
                                            preparations={'Ours-Disk::hybrid_disk':preparation})

    def test_fixed03_preflights_before_frozen_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            args=argparse.Namespace(methods=('DiskANN-PQ-Disk','Starling-Disk','AiSAQ-Disk'),
                phase='run',fixed_beam=4,storage_modes=('auto',),system03_fixed=True)
            def run(**kw):
                a=kw['args'];calls.append((a.phase,a.methods))
                if a.phase=='validate':
                    path=root/(a.methods[0]+'.json');path.write_text('{}')
                    return [(path,dict(storage_mode='hybrid_disk'))]
                lock=json.loads(a.tuning_lock_override.read_text())
                for method in args.methods:
                    entry=lock['selected'][method+'::hybrid_disk']
                    self.assertTrue(Path(entry['resource_preflight']['path']).is_file())
                    self.assertEqual(entry['config_id'],'beam4')
                return []
            with patch.object(o,'_run_layer_dataset',side_effect=run), patch.object(o,'dataset_run_root',return_value=root):
                o._run_fixed_pipeline(args=args,ports={},run_root=root,layer='05c',dataset='gist')
            self.assertEqual([p for p,_ in calls],['validate']*3+['run']*4)

    def test_tune_reuses_validate_artifact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = o.specs_for('05c', ['Ours-Disk'])[0]
            item = o.WorkItem(spec, 'hybrid_disk', 0, 32, 4., 'standard')
            with patch.object(o, 'dataset_run_root', return_value=root):
                path = o._artifact_path(root, '05c', 'gist', 'validate', item)
                path.parent.mkdir(parents=True)
                path.write_text('{}')
                self.assertEqual(o._artifact_path(root, '05c', 'gist', 'validation', item), path)
                self.assertNotEqual(o._artifact_path(root, '05c', 'gist', 'test', item), path)

    def test_order_and_immutable_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifests = root / 'manifests'
            manifests.mkdir()
            calls = []
            frozen_contents = {}
            methods = ('Ours-Disk', 'DiskANN-PQ-Disk')
            args = argparse.Namespace(methods=methods, phase='run')

            def run(**kw):
                a = kw['args']
                calls.append((a.methods, a.phase))
                if a.phase == 'tune':
                    frontier = manifests / 'tuning_frontier.csv'
                    frontier.write_text(','.join(a.methods))
                    (manifests / 'tuning.lock.json').write_text(json.dumps(dict(
                        selected={m: {'config_id': 'beam4'} for m in a.methods},
                        frontier_csv=str(frontier))))
                elif len(a.methods) == 1:
                    self.assertTrue(a.defer_aggregate)
                    lock = a.tuning_lock_override
                    d = json.loads(lock.read_text())
                    self.assertEqual(set(d['selected']), set(a.methods))
                    self.assertEqual(Path(d['frontier_csv']).read_text(), a.methods[0])
                    frozen_contents[lock] = lock.read_bytes()
                for lock, content in frozen_contents.items():
                    self.assertEqual(lock.read_bytes(), content)

            with patch.object(o, '_run_layer_dataset', side_effect=run), patch.object(o, 'dataset_run_root', return_value=root):
                o._run_method_pipeline(args=args, ports={}, run_root=root, layer='05c', dataset='gist')
            self.assertEqual(calls, [((methods[0],), 'tune'), ((methods[0],), 'run'),
                                     ((methods[1],), 'tune'), ((methods[1],), 'run'),
                                     (methods, 'tune'), (methods, 'run')])
            # Replaying the same validation cannot mutate already frozen locks.
            with patch.object(o, '_run_layer_dataset', side_effect=run), patch.object(o, 'dataset_run_root', return_value=root):
                o._run_method_pipeline(args=args, ports={}, run_root=root, layer='05c', dataset='gist')


if __name__ == '__main__':
    unittest.main()
