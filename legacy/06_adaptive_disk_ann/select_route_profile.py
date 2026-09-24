"""Select a frozen configuration using validation data only (accounting, not hard RAM)."""
import math


def select_profile(profiles, budget_gib, reference_recall, max_recall_drop=0.001):
    if not math.isfinite(budget_gib) or budget_gib <= 0:
        raise ValueError('budget must be finite and positive')
    if not 0 <= reference_recall <= 1 or not 0 <= max_recall_drop <= 1:
        raise ValueError('invalid recall requirement')
    eligible = []
    reasons = {}
    for p in profiles:
        reason = None
        if p.get('split') != 'validation':
            raise ValueError('test results cannot be used for selection')
        if p['budget_gib'] != budget_gib:
            continue  # Cache capacity changes with the budget; never transfer timings.
        if p['status'] != 'done':
            reason = p['status']
        elif not all(math.isfinite(p[k]) for k in ('recall', 'qps', 'peak_rss_bytes', 'accounted_bytes')):
            reason = 'nonfinite_metric'
        elif p['accounted_bytes'] > budget_gib * 2**30 or p['peak_rss_bytes'] > budget_gib * 2**30:
            reason = 'memory_exceeded'
        elif p['recall'] + 1e-12 < reference_recall-max_recall_drop:
            reason = 'recall_below_target'
        elif p['qps'] <= 0:
            reason = 'invalid_qps'
        else:
            eligible.append(p)
        reasons[p['variant']] = reason or 'eligible'
    best = min(eligible, key=lambda p: (-p['qps'], p['accounted_bytes'], p['variant'])) if eligible else None
    return dict(status='selected' if best else 'no_feasible_config', budget_gib=budget_gib,
                target_recall=reference_recall-max_recall_drop,
                selected=best, candidates=reasons, hard_budget_verified=False)


def select_from_archive(directory, budget_gib, max_recall_drop=None):
    """Only consume validation + frozen metadata; deliberately never read test.json."""
    import hashlib
    import json
    from pathlib import Path
    directory = Path(directory)
    validation = directory/'validation.json'
    frozen = json.loads((directory/'frozen_selection.json').read_text())
    if frozen['reference'].get('split') != 'validation':
        raise ValueError('reference recall must come from validation')
    profiles = json.loads(validation.read_text())
    config = json.loads((directory/'config.json').read_text())
    tolerance = config['max_recall_drop'] if max_recall_drop is None else max_recall_drop
    result = select_profile(profiles, budget_gib, frozen['reference']['recall'], tolerance)
    if not any(p['budget_gib'] == budget_gib for p in profiles):
        result['status'] = 'unprofiled_budget'
    result['validation_sha256'] = hashlib.sha256(validation.read_bytes()).hexdigest()
    result['profile_directory'] = str(directory.resolve())
    result['max_recall_drop'] = tolerance
    result['test_results_used_for_selection'] = False
    result['warning'] = 'Single-run development profiles; no hard RAM guarantee or timing extrapolation.'
    return result


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-dir', type=Path, required=True)
    parser.add_argument('--budget-gib', type=float, required=True)
    parser.add_argument('--max-recall-drop', type=float)
    parser.add_argument('--output', type=Path, help='New decision JSON; never overwrite an existing decision')
    args = parser.parse_args()
    decision = select_from_archive(args.profile_dir, args.budget_gib, args.max_recall_drop)
    content = json.dumps(decision, indent=2)
    if args.output:
        with args.output.open('x') as f:
            f.write(content+'\n')
    print(content)
