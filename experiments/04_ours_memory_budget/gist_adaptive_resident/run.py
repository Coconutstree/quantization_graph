"""Execute maximum-resident-dimension routing with M=32/64, then verify memory."""
import argparse
import importlib.util
import json
import mmap
import os
import time
from pathlib import Path

from policy import ROOT, HERE, WORK, OUT, CALIBRATION, MIB, calibrated_plan, dump
from prepare import BIN, build, encode, sha

spec = importlib.util.spec_from_file_location('adaptive_joint', HERE.parent / 'gist_joint_optimization/run.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
r.OUT = OUT
r.WORK = WORK
r.s.OUT = OUT
r.s.WORK = WORK
r.s.BIN = BIN
r.s.PROFILE = ROOT / 'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'
base_inputs = r.inputs
base_precondition = r.s.pilot.precondition
CURRENT = {}


def inputs(split):
    flags = base_inputs(split)
    flags['--implementation-fingerprint'] = 'adaptive-maximum-resident-pca-ranking-full4-v1'
    if CURRENT['asset'] is not None:
        flags['--pca-route-dir'] = str(CURRENT['asset'])
        flags['--pca-tail-mode'] = 'route'
        flags['--pca-route-keep'] = str(CURRENT['keep'])
    return flags


def precondition(cmd, folder):
    base_precondition(cmd, folder)
    flags = dict(zip(cmd[1::2], cmd[2::2]))
    if '--pca-route-dir' not in flags:
        return
    path = Path(flags['--pca-route-dir']) / 'sidecar.bin'
    offset = 0
    fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
    try:
        with mmap.mmap(-1, 4 * MIB) as buffer:
            while offset < path.stat().st_size:
                read = os.preadv(fd, [buffer], offset)
                assert read > 0
                offset += read
    finally:
        os.close(fd)
    dump(folder / 'pca_storage_precondition.json', dict(path=str(path), bytes=offset,
         io='O_DIRECT', included_in_query_timing=False, device_cache_controlled=False))


r.inputs = inputs
r.s.pilot.precondition = precondition


def setup():
    build()
    dump(OUT / 'calibration/lock.json', json.loads(CALIBRATION.read_text()))


def execute(budget=538, keep=32, widths=(100,), split='tune', rep=1, tag='', reference=None):
    global CURRENT
    assert keep in (32, 64)
    if int(budget) != budget:
        raise ValueError('benchmark budget must be an integer number of MiB')
    budget = int(budget)
    plan = calibrated_plan(budget)
    CURRENT = dict(plan=plan, keep=keep, asset=encode(plan['dimension']))
    name = f'b{budget:g}_d{plan["dimension"]}_m{keep}{tag}'
    folder = r.execute(name, dict(mode='resident', record=plan['record_cache_bytes'] // MIB),
                       split, rep, widths=widths, budget=budget, reference=reference)
    actual = json.loads((folder / 'memory_plan.json').read_text())
    assert actual['mode'] in ('resident', 'hot_dynamic')
    for field in ('budget_bytes', 'codes_bytes', 'factors_bytes', 'reserve_bytes', 'admission_bytes'):
        assert plan[field] == actual[field], (field, plan[field], actual[field])
    assert plan['record_cache_bytes'] == actual['optional_cache_bytes']
    assert not actual['routing_capacity_pages'] and not actual['max_inflight_pages']
    dump(folder / 'adaptive_selection.json', dict(**plan, asset=str(CURRENT['asset']), M=keep))
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-mib', type=int, default=538)
    parser.add_argument('--shortlist', type=int, choices=(32, 64))
    parser.add_argument('--widths', type=int, nargs='+', default=[100, 180, 260])
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    setup()
    if args.smoke:
        execute(args.budget_mib, args.shortlist or 32, (100,), split='train', tag='_smoke')
        return
    protocol = dict(budget_mib=args.budget_mib, shortlists=[args.shortlist] if args.shortlist else [32, 64],
                    widths=args.widths, target_recall=.95, workers=32, validation_queries=100,
                    test_queries=800, test_repeats=2, dimension_selection='maximum fitting integer dimension',
                    operating_point_selection='fastest validation point with Recall >= .95',
                    hard_prune=False, test_does_not_select_dimension_or_width=True)
    path = OUT / f'protocol_{args.budget_mib:g}.json'
    if path.exists():
        assert json.loads(path.read_text()) == protocol
    else:
        dump(path, protocol)
    options = []
    for keep in protocol['shortlists']:
        folder = execute(args.budget_mib, keep, args.widths, tag='_grid')
        for row in json.loads((folder / 'result.json').read_text())['summary_rows']:
            options.append(dict(M=keep, width=row['search_width'], recall=row['recall'], qps=row['qps'], source=str(folder)))
    legal = [x for x in options if x['recall'] >= protocol['target_recall']]
    selected = max(legal, key=lambda x: x['qps']) if legal else None
    lock = OUT / f'selection_{args.budget_mib:g}.json'
    value = dict(selected=selected, validation=options, target_recall=protocol['target_recall'],
                 binary_sha256=sha(BIN), selected_before_test=True)
    if lock.exists():
        assert json.loads(lock.read_text()) == value
    else:
        dump(lock, value)
    if selected:
        for rep in (1, 2):
            execute(args.budget_mib, selected['M'], (selected['width'],), split='test', rep=rep, tag='_locked')
    dump(OUT / f'state_{args.budget_mib:g}.json', dict(status='completed' if selected else 'no_eligible_point', updated=time.time()))


if __name__ == '__main__':
    main()
