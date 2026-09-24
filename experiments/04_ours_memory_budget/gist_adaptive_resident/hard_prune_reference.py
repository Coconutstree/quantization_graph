"""Exact-arithmetic reference for a future same-full4-score certificate.

This is an OFFLINE proof/checking reference, not an enabled search optimization.
It does not certify native SIMD floating-point error or construct GIST assets.
Fractions represent actual stored floats exactly when passed via Fraction(float).
"""
from fractions import Fraction as F
from math import isqrt


def dot(a, b):
    if len(a) != len(b):
        raise ValueError('dimension mismatch')
    return sum((F(x) * F(y) for x, y in zip(a, b)), F(0))


def sqrt_upper(value, bits=64):
    """A rational upper bound, proved by integer arithmetic, not a tolerance."""
    value = F(value)
    if value < 0 or bits < 0:
        raise ValueError('negative squared norm or precision')
    scale = 1 << bits
    numerator = value.numerator * scale * scale
    root = isqrt(numerator // value.denominator)
    if root * root * value.denominator < numerator:
        root += 1
    return F(root, scale)


def anchor(signs, transport, scale):
    """a T^T s; rows of T transport padded low-code signs into full-code space."""
    if not transport or len(signs) != len(transport) or any(s not in (-1, 1) for s in signs):
        raise ValueError('invalid sign code or transport')
    size = len(transport[0])
    if any(len(row) != size for row in transport):
        raise ValueError('ragged transport')
    return [F(scale) * sum((F(s) * F(row[j]) for s, row in zip(signs, transport)), F(0))
            for j in range(size)]


def certify(encoded_full_vector, norm_x, signs, transport, scale):
    center = anchor(signs, transport, scale)
    if len(center) != len(encoded_full_vector):
        raise ValueError('dimension mismatch')
    residual = [F(x) - y for x, y in zip(encoded_full_vector, center)]
    return dict(norm_x=F(norm_x), scale=F(scale), radius=sqrt_upper(dot(residual, residual)))


def lower_bound(query, norm_q, signs, transport, certificate):
    """Bounds h_x+h_q-Q·X in real arithmetic, not raw true squared L2."""
    center = anchor(signs, transport, certificate['scale'])
    return (certificate['norm_x'] + F(norm_q) - dot(query, center)
            - sqrt_upper(dot(query, query)) * certificate['radius'])


def can_prune(lb, tau, *, pool_full, native_roundoff_bound=None):
    # None must fail open: the real-arithmetic proof alone does not authorize
    # pruning against the native rounded four-bit score.
    if not pool_full or native_roundoff_bound is None:
        return False
    margin = F(native_roundoff_bound)
    if margin < 0:
        raise ValueError('negative numerical-error certificate')
    return lb - margin > F(tau)
