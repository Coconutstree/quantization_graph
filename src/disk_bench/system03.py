"""Immutable fixed-beam 03 policy. Historical 05/06 contracts are unchanged."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

PROTOCOL = '03_official_beam4_single_rss4g_v1'
METHODS = ('Ours-Disk', 'DiskANN-PQ-Disk', 'Starling-Disk', 'AiSAQ-Disk')
WIDTHS = (10, 20, 40, 60, 100, 160, 240, 400, 580)
PRIMARY_DATASETS = ('gist', 'bigann10m')
SUPPLEMENTARY_DATASETS = ('agnews', 'dbpedia')
BUDGET_BYTES = 4 * (1 << 30)


def apply_defaults(args):
    """Apply only to the new 03 entrypoint/explicit opt-in, not shared 05 defaults."""
    if not getattr(args, 'system03_fixed', False):
        return
    if args.experiment_group != 'primary':
        raise ValueError('fixed official 03 requires primary; use independent 05/06 for diagnostics')
    if args.phase == 'tune':
        raise ValueError('03 has no performance tuning phase; use run --method-pipeline')
    if args.fixed_beam not in (None, 4) or args.repeats != 1:
        raise ValueError('03 requires beam=4 and repeats=1')
    if args.cache_mode != 'standard' or args.ours_cache_allocation != 'graph_first':
        raise ValueError('03 requires standard/graph_first; cache ablations belong in 04/05')
    if args.search_widths not in (None, WIDTHS):
        raise ValueError('03 requires the frozen nine-width grid')
    names = tuple(x for x in args.methods.split(',') if x) if isinstance(args.methods, str) else tuple(args.methods)
    if names and (set(names) - set(METHODS) or len(names) != len(set(names))):
        raise ValueError('03 supports only Ours, DiskANN, Starling and AiSAQ, without duplicates')
    args.methods = ','.join(names or METHODS)
    args.fixed_beam = 4
    args.search_widths = WIDTHS
    if args.phase == 'run':
        args.method_pipeline = True


def storage_errors(path):
    from .common import lsblk_rotational
    if path is None or not Path(path).is_dir():
        return ['SSD/NVMe disk-root must be an existing explicit directory']
    rotational = lsblk_rotational(Path(path))
    if rotational is not False:
        return ['SSD/NVMe not verified: backing device is rotational or unknown; no HDD fallback']
    return []


def unsupported_reason(method, dimension, degree=64):
    if method == 'Starling-Disk' and (dimension * 4 + (degree + 1) * 4 > 4096):
        return 'official Starling FP32 node exceeds 4 KiB; no input truncation or layout patch allowed'
    return None


def query_fingerprints(path):
    import struct
    result = []
    with Path(path).open('rb') as f:
        dimension = None
        while raw := f.read(4):
            if len(raw) != 4:
                raise ValueError('truncated fvecs header')
            d, = struct.unpack('<i', raw)
            if d <= 0 or (dimension is not None and d != dimension):
                raise ValueError('invalid/mixed fvecs dimensions')
            dimension = d
            data = f.read(4*d)
            if len(data) != 4*d:
                raise ValueError('truncated fvecs row')
            result.append(hashlib.sha256(data).hexdigest())
    if not result:
        raise ValueError('empty query file')
    return dimension, result


def verify_disjoint(warmup, test, count=100):
    wd, warm = query_fingerprints(warmup)
    td, measured = query_fingerprints(test)
    if wd != td or count <= 0 or len(warm) < count:
        raise ValueError('warmup dimensions/count differ from frozen protocol')
    if set(warm[:count]) & set(measured):
        raise ValueError('warmup/test query overlap')
    return dict(warmup_query=str(Path(warmup).resolve()),
                warmup_query_sha256=hashlib.sha256(Path(warmup).read_bytes()).hexdigest(),
                warmup_queries=count, test_query_sha256=hashlib.sha256(Path(test).read_bytes()).hexdigest(),
                warmup_test_disjoint=True,
                query_history='existing queries; independence from prior development not asserted')


def check_rows(rows, methods=METHODS):
    """Reject incomplete/duplicate curves and protocol drift before plotting."""
    expected = {(m, w) for m in methods for w in WIDTHS}
    seen = set()
    for row in rows:
        key = (row['method'], int(row['search_width']))
        if key in seen or key not in expected:
            raise ValueError('duplicate or unexpected method/width')
        seen.add(key)
        if (int(row['beam_width']) != 4 or int(row['workers']) != 32
                or int(row['repeat_id']) != 0 or float(row['search_dram_budget_gib']) != 4):
            raise ValueError('fixed 03 configuration drift')
        if row.get('system03_protocol') != PROTOCOL:
            raise ValueError('historical result cannot enter new 03 curve')
    if seen != expected:
        raise ValueError('incomplete method/width coverage')


def freeze(path, payload):
    from .native_contract import atomic_write_json
    path = Path(path)
    if path.exists():
        if json.loads(path.read_text()) != payload:
            raise ValueError('frozen 03 configuration changed; use a new run-id')
    else:
        atomic_write_json(path, payload)


def verify_source_snapshot(path, digest):
    from .native_contract import sha256_file
    path=Path(path)
    if sha256_file(path)!=digest:
        raise ValueError('03 build source manifest changed')
    files=json.loads(path.read_text())['files']
    if not files or any(sha256_file(Path(p))!=h for p,h in files.items()):
        raise ValueError('03 source changed since binary build; rebuild and use a new registry')


def validate_point(artifact):
    """Recheck protocol sidecars against the actual measured command, on reuse/plot."""
    from .storage_precondition import option
    if artifact.get('system03_protocol') != PROTOCOL:
        raise ValueError('unknown system03 protocol')
    if artifact.get('phase') != 'test' or artifact.get('repeat_id') != 0:
        raise ValueError('03 requires test repeat zero')
    verify_source_snapshot(artifact['source_snapshot_path'],artifact['source_snapshot_sha256'])
    resource = json.loads(Path(artifact['resource_measurement_path']).read_text())
    command = resource['command']
    w = artifact['measurement_width']
    if (w not in WIDTHS or option(command, '--measurement-width') != str(w)
            or option(command, '--fixed-beam') != '4'
            or len(artifact['summary_rows']) != 1
            or artifact['summary_rows'][0]['search_width'] != w):
        raise ValueError('03 point does not describe one frozen width/beam process')
    proof = verify_disjoint(option(command, '--warmup-query'), option(command, '--query'))
    if proof != artifact.get('independent_warmup') or option(command, '--warmup-query-sha256') != proof['warmup_query_sha256']:
        raise ValueError('03 independent warmup evidence changed')
