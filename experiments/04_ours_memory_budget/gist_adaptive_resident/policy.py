"""Choose the highest resident route dimension using the native padded layout.

This admission policy maximizes dimension, not QPS. Search quality is validated
separately. Byte accounting includes the locked process floor and safety reserve.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
WORK = ROOT / 'work/ours_memory_budget/gist_adaptive_resident'
OUT = ROOT / 'results/04_ours_memory_budget/gist_adaptive_resident'
ASSETS = ROOT / 'work/ours_memory_budget/gist_pca_budget'
CALIBRATION = ROOT / 'results/04_ours_memory_budget/gist_pca_routing/calibration/lock.json'
MIB = 1 << 20


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def padded_dimension(dim):
    if dim < 1:
        raise ValueError('dimension must be positive')
    rounded = ((dim + 63) // 64) * 64
    return 1 << (rounded - 1).bit_length()


def ledger(dim, *, count, original_dim, fixed_bytes, reserve_bytes,
           factor_stride=20, workers=32, certificate_bytes=0):
    if not 1 <= dim <= original_dim or count < 1 or workers != 32:
        raise ValueError('invalid dimensions/count or unsupported worker calibration')
    if min(fixed_bytes, reserve_bytes, certificate_bytes, factor_stride) < 0:
        raise ValueError('negative byte allowance')
    dd = padded_dimension(dim)
    pca = dim != original_dim
    parts = dict(fixed_bytes=fixed_bytes, reserve_bytes=reserve_bytes,
                 codes_bytes=count * (dd // 8), factors_bytes=count * factor_stride,
                 projection_bytes=4 * original_dim * dim if pca else 0,
                 mean_bytes=4 * original_dim if pca else 0,
                 rotation_reserved_bytes=16 * dd * dd if pca else 0,
                 query_scratch_reserved_bytes=workers * 16 * original_dim if pca else 0,
                 residual_statistics_bytes=0, certificate_bytes=certificate_bytes)
    return dict(dimension=dim, padded_dimension=dd, code_stride=dd // 8,
                factor_stride=factor_stride, original_dim=original_dim, count=count,
                route='pca' if pca else 'original', **parts,
                resident_required_bytes=sum(parts.values()))


def choose(budget_bytes, *, count=1_000_000, original_dim=960, minimum_dim=64,
           fixed_bytes=426 * MIB, reserve_bytes=64 * MIB, factor_stride=20,
           workers=32, cache_floor_bytes=0, minimum_record_bytes=4_001_177,
           certificate_bytes=0):
    if not 1 <= minimum_dim <= original_dim:
        raise ValueError('invalid minimum dimension')
    if budget_bytes < 0 or cache_floor_bytes < 0 or minimum_record_bytes < 1:
        raise ValueError('invalid budget/cache minimum')
    if 0 < cache_floor_bytes < minimum_record_bytes:
        raise ValueError('cache floor cannot hold the ID map and one record')
    candidates = [ledger(k, count=count, original_dim=original_dim,
                         fixed_bytes=fixed_bytes, reserve_bytes=reserve_bytes,
                         factor_stride=factor_stride, workers=workers,
                         certificate_bytes=certificate_bytes)
                  for k in range(minimum_dim, original_dim + 1)]
    fits = [x for x in candidates
            if x['resident_required_bytes'] + cache_floor_bytes <= budget_bytes]
    if not fits:
        minimum = min(x['resident_required_bytes'] + cache_floor_bytes for x in candidates)
        raise ValueError(f'no resident dimension fits: minimum {minimum} bytes, '
                         f'deficit {minimum - budget_bytes} bytes; no paging fallback')
    best = fits[-1]
    spare = budget_bytes - best['resident_required_bytes']
    # Existing benchmark interface allocates record cache in whole MiB.
    cache = spare // MIB * MIB
    if cache < minimum_record_bytes:
        cache = 0
    if cache < cache_floor_bytes:
        # Honor an explicit floor even when it is not a whole MiB.
        cache = cache_floor_bytes
    rejected = [dict(dimension=x['dimension'],
                     required_bytes=x['resident_required_bytes'] + cache_floor_bytes,
                     deficit_bytes=x['resident_required_bytes'] + cache_floor_bytes - budget_bytes)
                for x in candidates if x['dimension'] > best['dimension']]
    return dict(schema_version=1, objective='maximum resident dimension, then record cache',
                budget_bytes=budget_bytes, minimum_dimension=minimum_dim,
                cache_floor_bytes=cache_floor_bytes, workers=workers,
                minimum_record_bytes=minimum_record_bytes, **best,
                record_cache_bytes=cache,
                admission_bytes=best['resident_required_bytes'] + cache,
                unused_bytes=spare - cache, mode='resident', route_io_expected=0,
                hard_prune=False, rejected_higher_dimensions=rejected)


def calibrated_plan(budget_mib, **kwargs):
    calibration = json.loads(CALIBRATION.read_text())
    if calibration.get('locked') is not True:
        raise ValueError('calibration must be locked')
    return choose(int(budget_mib * MIB), fixed_bytes=calibration['fixed_bytes'],
                  reserve_bytes=calibration['reserve_bytes'], **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-mib', type=float, required=True)
    parser.add_argument('--cache-floor-mib', type=float, default=0)
    parser.add_argument('--minimum-dim', type=int, default=64)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    plan = calibrated_plan(args.budget_mib, cache_floor_bytes=int(args.cache_floor_mib * MIB),
                           minimum_dim=args.minimum_dim)
    if args.output:
        dump(args.output, plan)
    print(json.dumps({k: v for k, v in plan.items() if k != 'rejected_higher_dimensions'}, indent=2))


if __name__ == '__main__':
    main()
