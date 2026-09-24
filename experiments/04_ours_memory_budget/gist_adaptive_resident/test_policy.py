"""Admission boundaries and global maximality, including nonmonotone full-D cost."""
import unittest
from policy import MIB, choose, ledger, padded_dimension


class AdmissionTests(unittest.TestCase):
    def test_native_padding(self):
        self.assertEqual([padded_dimension(k) for k in (1, 64, 65, 128, 129, 255, 256, 257, 512, 960)],
                         [64, 64, 128, 128, 256, 256, 256, 512, 512, 1024])

    def test_known_low_budget(self):
        plan = choose(538 * MIB)
        self.assertEqual(plan['dimension'], 128)
        self.assertEqual(plan['record_cache_bytes'], 12 * MIB)
        self.assertEqual(plan['codes_bytes'], 16_000_000)
        self.assertEqual(plan['factors_bytes'], 20_000_000)
        self.assertEqual(plan['residual_statistics_bytes'], 0)

    def test_global_maximum_at_boundaries(self):
        args = dict(count=1_000_000, original_dim=960, fixed_bytes=426 * MIB, reserve_bytes=64 * MIB)
        candidates = [ledger(k, **args) for k in range(64, 961)]
        # Full 960 has no projection overhead: a monotone binary search is wrong.
        self.assertLess(candidates[-1]['resident_required_bytes'], candidates[-2]['resident_required_bytes'])
        budgets = {538 * MIB, 542 * MIB, 550 * MIB, 580 * MIB, 632 * MIB}
        for k in (64, 65, 128, 129, 255, 256, 257, 512, 513, 959, 960):
            threshold = candidates[k - 64]['resident_required_bytes']
            budgets.update((threshold - 1, threshold, threshold + 1))
        for budget in sorted(budgets):
            legal = [x for x in candidates if x['resident_required_bytes'] <= budget]
            if not legal:
                with self.assertRaises(ValueError):
                    choose(budget)
                continue
            p = choose(budget)
            self.assertEqual(p['dimension'], max(x['dimension'] for x in legal))
            self.assertLessEqual(p['admission_bytes'], budget)
            self.assertTrue(all(x['deficit_bytes'] > 0 for x in p['rejected_higher_dimensions']))

    def test_mandatory_certificate_and_cache(self):
        a = choose(538 * MIB, certificate_bytes=13 * MIB)
        self.assertLess(a['dimension'], 128)
        b = choose(538 * MIB, cache_floor_bytes=13 * MIB)
        self.assertGreaterEqual(b['record_cache_bytes'], 13 * MIB)
        self.assertLess(b['dimension'], 128)
        with self.assertRaises(ValueError):
            choose(538 * MIB, cache_floor_bytes=1)
        with self.assertRaises(ValueError):
            choose(490 * MIB)


if __name__ == '__main__':
    unittest.main()
