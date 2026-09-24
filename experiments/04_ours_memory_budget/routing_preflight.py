"""Header-only DB1 admission estimate; does not load or modify the index."""
import argparse
import json
import struct
from pathlib import Path


def inspect_sidecar(metadata: Path, sidecar: Path) -> dict:
    with metadata.open('rb') as stream:
        header = stream.read(68)
    if len(header) != 68:
        raise ValueError('truncated Ours metadata header')
    magic, seed, dim, centroids, count, compact, residual, msb, factors = struct.unpack('<8sI7Q', header)
    if magic != b'QG05OUR1' or not all((dim, centroids, count, compact, msb, factors)):
        raise ValueError('invalid Ours metadata header')
    centroid_bytes = dim * centroids * 4
    if metadata.stat().st_size != 68 + centroid_bytes:
        raise ValueError('Ours metadata file length mismatch')
    with sidecar.open('rb') as stream:
        header = stream.read(24)
    if len(header) != 24:
        raise ValueError('truncated Ours sidecar header')
    magic, codes_size, factors_size = struct.unpack('<8sQQ', header)
    if magic != b'QG05OSC1' or (codes_size, factors_size) != (count * msb, count * factors):
        raise ValueError('Ours sidecar header does not match metadata')
    if sidecar.stat().st_size != 24 + codes_size + factors_size:
        raise ValueError('Ours sidecar file length mismatch')
    return dict(record_count=count, dimension=dim, msb_stride_bytes=msb,
                factor_stride_bytes=factors, codes_bytes=codes_size,
                factors_bytes=factors_size, routing_bytes=codes_size + factors_size,
                centroid_bytes=centroid_bytes)


def assess(layout: dict, budget: int, workers: int, other: int, safety: int) -> dict:
    if budget <= 0 or workers <= 0 or other < 0 or safety < 0:
        raise ValueError('budget/workers must be positive; reserves must be nonnegative')
    # Matches native Ours admission: byte-per-node visited + 12 MiB per worker.
    scratch = workers * (layout['record_count'] + 12 * 1024**2)
    nonrouting = layout['centroid_bytes'] + scratch + other + safety
    required = nonrouting + layout['routing_bytes']
    balance = budget - required
    state = 'does_not_fit' if balance < 0 else 'fits_exactly' if balance == 0 else 'fits_with_spare'
    return dict(**layout, budget_bytes=budget, workers=workers,
                worker_scratch_reservation_bytes=scratch,
                other_reservation_bytes=other, safety_reservation_bytes=safety,
                nonrouting_reservation_bytes=nonrouting,
                routing_available_bytes=max(0, budget - nonrouting),
                required_resident_bytes=required, state=state,
                fits=balance >= 0, spare_bytes=max(0, balance), deficit_bytes=max(0, -balance),
                nonrouting_fits=nonrouting <= budget,
                estimate_only=True, native_paging_available=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--sidecar', type=Path, required=True)
    parser.add_argument('--budget-bytes', type=int, required=True)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--other-reservation-bytes', type=int, required=True,
                        help='Peak non-routing overhead excluding centroids and worker scratch; include inputs, libraries, stacks, allocator, layout, I/O and initialization temporaries')
    parser.add_argument('--safety-reservation-bytes', type=int, required=True)
    args = parser.parse_args()
    try:
        report = assess(inspect_sidecar(args.metadata, args.sidecar), args.budget_bytes,
                        args.workers, args.other_reservation_bytes, args.safety_reservation_bytes)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0 if report['fits'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
