"""Measured-resource evidence and single-run result validation for the new disk protocol."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
try:
    from .results_paths import resolve_result_path
except ImportError:
    from results_paths import resolve_result_path

PROTOCOL_ID = 'disk_ram_budget_rss_20260921'
FORMAL_REPEATS = 1
DEFAULT_BUDGET_GIB = 2.0
# Public experiment 03 uses one common budget selected by memory preflight.
# Keep 01/02 and the default 05 scan point independent of this choice.
SYSTEM_PRIMARY_BUDGET_GIB = 4.0
# All methods receive the same planned RAM budget, without an added OS cap.
BASELINE_NATIVE_BUDGET_GIB = DEFAULT_BUDGET_GIB
BUDGET_GRID_GIB = (0.5, 1.0, 2.0, 4.0, 8.0)
GROUP_FIELDS = ('protocol_id', 'run_id', 'layer', 'dataset', 'method', 'storage_mode',
                'cache_mode', 'search_dram_budget_gib', 'workers', 'config_id',
                'search_param', 'search_width', 'beam_width', 'ablation')
PROVENANCE_FIELDS = ('native_binary_sha256', 'input_manifest_sha256',
                     'source_index_manifest_sha256', 'query_split_sha256',
                     'source_graph_sha256', 'shared_graph_sha256', 'graph_role',
                     'memory_enforcement', 'memory_limit_scope', 'memory_limit_bytes')
RESOURCE_FIELDS = ('protocol_id', 'memory_enforcement', 'memory_limit_scope',
                   'memory_limit_bytes', 'resource_measurement_path', 'resource_measurement_sha256',
                   'process_peak_rss_bytes', 'cgroup_memory_peak_bytes', 'sampled_vm_peak_bytes',
                   'peak_rss_scope', 'native_reported_peak_rss_bytes', 'resource_status',
                   'memory_policy', 'native_config_budget_gib', 'planned_budget_bytes', 'budget_admitted',
                   'observed_peak_rss_bytes', 'sampled_swap_bytes', 'rss_samples_path', 'rss_samples_sha256', 'rss_budget',
                   'process_read_bytes', 'process_write_bytes', 'process_io_scope')
METRIC_FIELDS = ('recall', 'qps', 'latency_mean_us', 'latency_p50_us', 'latency_p95_us',
                 'latency_p99_us', 'index_size_mb', 'resident_bytes', 'cache_bytes',
                 'peak_rss_bytes', 'process_peak_rss_bytes', 'cgroup_memory_peak_bytes',
                 'sampled_vm_peak_bytes', 'io_requests_per_query', 'sectors_4k_per_query',
                 'bytes_read_per_query', 'io_wait_us', 'distance_compute_us', 'query_prep_us',
                 'queue_compute_us', 'rerank_us', 'traversal_wall_us', 'visited_nodes', 'distance_evaluations',
                 'db1_checks', 'db1_survivors', 'full4_candidates', 'full4_page_reads',
                 'rerank_candidates', 'rerank_page_reads', 'mean_relative_error',
                 'p95_relative_error', 'pairwise_flip_rate', 'fixed_candidate_recall_at_10',
                 'code_bytes_per_vector', 'effective_bits_per_dim', 'read_amplification')


def sha256(path):
    with resolve_result_path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def requires_memory_limit(method, storage_mode):
    # Kept for callers: the default protocol never requires OS enforcement.
    return False


def method_budget_gib(method, ours_budget_gib=DEFAULT_BUDGET_GIB, baseline_budgets=None):
    value = (baseline_budgets or {}).get(method, ours_budget_gib)
    if not math.isfinite(value) or value <= 0 or value != ours_budget_gib:
        raise ValueError('all methods must use the same planned RAM budget')
    return value


def validate_resource_evidence(artifact, expected):
    if artifact.get('protocol_id') != PROTOCOL_ID:
        raise ValueError('new formal protocol requires measured resource evidence; legacy rows are diagnostic')
    file=resolve_result_path(artifact.get('resource_measurement_path',''))
    if not file.is_absolute() or not file.is_file():raise ValueError('resource measurement file missing')
    if sha256(file)!=artifact.get('resource_measurement_sha256'):raise ValueError('resource evidence hash mismatch')
    e=json.loads(file.read_text())
    if e.get('protocol_id')!=PROTOCOL_ID or e.get('status')!='completed' or e.get('exit_code')!=0:
        raise ValueError('resource measurement did not complete successfully')
    if e.get('binary_sha256')!=expected.get('native_binary_sha256',artifact.get('native_binary_sha256')):
        raise ValueError('resource evidence belongs to a different native binary')
    command=e.get('command',[])
    # Bind the external measurement to this artifact, not a different successful run.
    if '--result-json' not in command or resolve_result_path(command[command.index('--result-json')+1])!=resolve_result_path(expected.get('artifact_path','')):
        raise ValueError('resource command does not produce this artifact')
    rss=e.get('process_peak_rss_bytes')
    if not isinstance(rss,int) or rss<=0 or e.get('process_peak_rss_source') not in ('wait4_ru_maxrss_linux_kib', 'max_wait4_sampled_rss_hwm_linux_bytes'):
        raise ValueError('missing kernel-measured process peak RSS')
    if artifact.get('peak_rss_bytes') != rss or artifact.get('process_peak_rss_bytes') != rss:
        raise ValueError('artifact RSS differs from external measurement')
    for field in ('memory_enforcement', 'memory_limit_scope', 'memory_limit_bytes', 'cgroup_memory_peak_bytes'):
        if artifact.get(field) != e.get(field):
            raise ValueError(f'artifact differs from measured resource field: {field}')
    if artifact.get('storage_mode')=='resident':
        if not e.get('reference_only') or e.get('memory_enforcement')!='none':
            raise ValueError('resident reference must be explicitly unconstrained')
    elif e.get('rss_budget'):
        if sha256(e.get('rss_samples_path', '')) != e.get('rss_samples_sha256'):
            raise ValueError('RSS sample evidence hash mismatch')
        budget = int(float(expected.get('search_dram_budget_gib', artifact.get('search_dram_budget_gib', 0)))*(1<<30))
        if (budget <= 0 or e.get('planned_budget_bytes') != budget
                or artifact.get('planned_budget_bytes') != budget
                or e.get('budget_admitted') is not True or artifact.get('budget_admitted') is not True
                or e.get('memory_enforcement') != 'none' or e.get('memory_limit_bytes') is not None
                or e.get('budget_verified') or e.get('observe_only') or e.get('reference_only')
                or e.get('memory_limit_scope') != 'planned_process_ram_budget_rss_admission'):
            raise ValueError('invalid planned RAM budget admission')
        if (rss > budget or e.get('observed_peak_rss_bytes') != rss
                or e.get('sampled_swap_bytes') != 0 or e.get('launch', {}).get('rlimit_as') != 'unlimited'):
            raise ValueError('RSS budget exceeded, swap observed, or inherited address-space cap')
    elif e.get('observe_only'):
        raise ValueError('unbudgeted native observation cannot enter the RAM budget protocol')
    else:
        raise ValueError('cgroup evidence belongs to a separate protocol, not planned RAM/RSS')
    return e


def attach_resource_evidence(artifact_path, evidence_path, evidence):
    path=Path(artifact_path);native=path.with_suffix('.native.json')
    if native.exists():raise FileExistsError(native)
    native.write_bytes(path.read_bytes())
    a=json.loads(path.read_text())
    a.update(protocol_id=PROTOCOL_ID, resource_measurement_path=str(Path(evidence_path).resolve()),
             resource_measurement_sha256=sha256(evidence_path),
             native_reported_peak_rss_bytes=a.get('peak_rss_bytes'),
             peak_rss_bytes=evidence['process_peak_rss_bytes'],
             peak_rss_scope=evidence['measurement_scope'],resource_status=evidence['status'])
    a['memory_policy'] = ('resident_reference' if evidence.get('reference_only') else
                          'ram_budget_rss' if evidence.get('rss_budget') else
                          'native_observed' if evidence.get('observe_only') else 'cgroup_capped')
    a['native_config_budget_gib'] = a.get('search_dram_budget_gib')
    for key in RESOURCE_FIELDS:
        if key in evidence:a[key]=evidence[key]
    for row in a.get('summary_rows',[]):
        row['native_reported_peak_rss_bytes']=row.get('peak_rss_bytes')
        row['peak_rss_bytes']=evidence['process_peak_rss_bytes']
        row['peak_rss_scope']=evidence['measurement_scope']
    # This only corrects resources; it does not grant algorithm/timing acceptance.
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(a,indent=2,allow_nan=False)+'\n');temp.replace(path)
    return a


def aggregate_repeats(rows):
    """Validate one real run per operating point; preserve its measured values.

    Kept under this API name for callers; no averaging, median, IQR or CV is computed.
    """
    if not rows:
        raise ValueError('no measured rows')
    seen = set()
    output = []
    for row in rows:
        if row.get('protocol_id') != PROTOCOL_ID:
            raise ValueError('legacy or missing protocol cannot enter formal results')
        if str(row.get('formal_ready')).lower() != 'true':
            raise ValueError('non-formal row cannot enter formal results')
        if float(row.get('repeat_id', -1)) != 0:
            raise ValueError('single-run protocol requires repeat_id=0')
        key = tuple(str(row.get(f, '')) for f in GROUP_FIELDS)
        if key in seen:
            raise ValueError(f'duplicate operating point in single-run results: {key}')
        seen.add(key)
        qps, recall = float(row.get('qps', 0)), float(row.get('recall', -1))
        if not math.isfinite(qps) or qps <= 0 or not 0 <= recall <= 1:
            raise ValueError('each point requires positive finite QPS and recall in [0,1]')
        for field in METRIC_FIELDS:
            value = row.get(field)
            if value not in (None, '') and not math.isfinite(float(value)):
                raise ValueError(f'nonfinite metric: {field}')
        out = dict(row, repeat_id=0)
        # Do not retain statistics from a previously aggregated row.
        for field in ('qps_iqr', 'qps_cv', 'latency_p95_us_iqr', 'latency_p95_us_cv',
                      'source_artifact_paths'):
            out.pop(field, None)
        output.append(out)
    return output
