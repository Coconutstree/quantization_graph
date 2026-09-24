import unittest
from select_route_profile import select_profile, select_from_archive


class SelectorTest(unittest.TestCase):
    def profile(self, name='d256', recall=.99, qps=10, **kw):
        return dict(variant=name, recall=recall, qps=qps, split='validation',
                    budget_gib=.5, status='done', accounted_bytes=200, peak_rss_bytes=300, **kw)

    def test_fastest_feasible(self):
        p = [self.profile(), self.profile('d64', .8, 100), self.profile('baseline', .99, 5)]
        self.assertEqual(select_profile(p, .5, .99)['selected']['variant'], 'd256')

    def test_no_feasible(self):
        self.assertEqual(select_profile([self.profile(recall=.9)], .5, .99)['status'], 'no_feasible_config')

    def test_memory_and_budget(self):
        p = self.profile()
        p['peak_rss_bytes'] = 2**30
        self.assertIsNone(select_profile([p], .5, .99)['selected'])
        self.assertIsNone(select_profile([self.profile()], .25, .99)['selected'])

    def test_no_test_leakage(self):
        p = self.profile()
        p['split'] = 'test'
        with self.assertRaises(ValueError):
            select_profile([p], .5, .99)

    def test_archive_ignores_test_and_distinguishes_unprofiled(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'validation.json').write_text(json.dumps([self.profile()]))
            (path/'frozen_selection.json').write_text(json.dumps({'reference': self.profile()}))
            (path/'config.json').write_text(json.dumps({'max_recall_drop': .001}))
            # Intentionally invalid JSON: the selector must never open it.
            (path/'test.json').write_text('DO NOT READ TEST RESULTS')
            self.assertEqual(select_from_archive(path, .5)['status'], 'selected')
            self.assertEqual(select_from_archive(path, .3)['status'], 'unprofiled_budget')
            self.assertFalse(select_from_archive(path, .5)['test_results_used_for_selection'])

    def test_original_is_allowed_when_fastest(self):
        candidates = [self.profile(), self.profile('baseline', .99, 20)]
        self.assertEqual(select_profile(candidates, .5, .99)['selected']['variant'], 'baseline')

if __name__ == '__main__':
    unittest.main()
