"""Proof-reference checks; no finite-sample claim about native safe pruning."""
import itertools
import unittest
from fractions import Fraction as F
from hard_prune_reference import certify, dot, lower_bound, sqrt_upper, can_prune


class CertificateTests(unittest.TestCase):
    def test_outward_sqrt(self):
        for n in range(50):
            for d in (1, 3, 17):
                x = F(n, d)
                u = sqrt_upper(x)
                self.assertGreaterEqual(u * u, x)
                if u:
                    self.assertLess((u - F(1, 1 << 64)) ** 2, x)

    def test_exhaustive_two_dimensional_four_bit_codes(self):
        # One sign transports into a 2D full4 code. The non-orthogonal transport
        # is intentional: reconstruction-error certification needs no isometry.
        transport = [[F(3, 5), F(4, 5)]]
        pairs = 0
        for code in itertools.product(range(16), repeat=2):
            x = [F(c) - F(15, 2) for c in code]
            signs = [1 if dot(x, transport[0]) >= 0 else -1]
            scale = abs(dot(x, transport[0]))
            hx = dot(x, x) / 4  # Norm header need not equal reconstructed norm.
            certificate = certify(x, hx, signs, transport, scale)
            for q in itertools.product((-3, 0, 2), repeat=2):
                hq = dot(q, q)
                bound = lower_bound(q, hq, signs, transport, certificate)
                actual_real_score = hx + hq - dot(q, x)
                self.assertLessEqual(bound, actual_real_score)
                self.assertFalse(can_prune(bound, actual_real_score, pool_full=True, native_roundoff_bound=0))
                pairs += 1
        self.assertEqual(pairs, 2304)

    def test_no_certificate_no_native_prune(self):
        self.assertFalse(can_prune(F(10), F(9), pool_full=True))
        self.assertFalse(can_prune(F(10), F(9), pool_full=False, native_roundoff_bound=0))
        self.assertFalse(can_prune(F(10), F(9), pool_full=True, native_roundoff_bound=1))
        self.assertTrue(can_prune(F(10), F(9), pool_full=True, native_roundoff_bound=F(1, 2)))

    def test_true_l2_and_full4_threshold_must_not_mix(self):
        raw_distance, true_l2_lower, full4_score, tau = 10, 9, 8, F(17, 2)
        self.assertLessEqual(true_l2_lower, raw_distance)
        self.assertGreater(true_l2_lower, tau)  # Invalid mixed-system prune.
        self.assertLess(full4_score, tau)       # Full4 would have inserted it.


if __name__ == '__main__':
    unittest.main()
