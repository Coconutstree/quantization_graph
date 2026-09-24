#!/usr/bin/env python3
"""Run unchanged Starling CLI; export legacy-shaped, explicitly diagnostic artifacts.

No upstream source edits. Missing native metrics remain null, never inferred from
batch averages. This does not grant formal 05-suite acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import struct
import subprocess
import time

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src/disk_bench"))
from official_sources import verify_source
from run_official_disk_baseline import commands, fvecs_to_bin, parser, sha256, REPO


def write(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def native_rows(path):
    rows = {}
    for line in path.read_text().splitlines():
        words = line.split()
        if len(words) != 10:
            continue
        try:
            width, beam = int(words[0]), int(words[1])
            values = list(map(float, words[2:]))
        except ValueError:
            continue
        rows[width] = dict(beam_width=beam, qps=values[0], latency_mean_us=values[1],
                           latency_p999_us=values[2], io_requests_per_query=values[3],
                           native_reported_peak_mem_mb=values[7])
    return rows


def geometry(path):
    with path.open('rb') as stream:
        dim, = struct.unpack('<i', stream.read(4))
    assert dim > 0 and path.stat().st_size % (4 * (dim + 1)) == 0
    return path.stat().st_size // (4 * (dim + 1)), dim


def run_command(cmd, log_path, limit=False, budget_bytes=2**31):
    preparation = None
    if Path(cmd[0]).name == 'search_disk_index':
        from storage_precondition import prepare_search
        preparation = log_path.with_suffix('.storage_precondition.json')
        prepare_search(cmd, preparation, 'Starling-Disk')
    def bounds():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if limit:
            resource.setrlimit(resource.RLIMIT_AS, (budget_bytes, budget_bytes))
    rss = vm = 0
    start = time.monotonic()
    with log_path.open('x') as log:
        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                   preexec_fn=bounds, cwd=REPO,
                                   env=dict(os.environ, MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                                            MALLOC_ARENA_MAX='2', OMP_DYNAMIC='FALSE'))
        while process.poll() is None:
            try:
                status = Path(f'/proc/{process.pid}/status').read_text()
                for line in status.splitlines():
                    if line.startswith(('VmRSS:', 'VmHWM:')):
                        rss = max(rss, int(line.split()[1]) * 1024)
                    if line.startswith(('VmSize:', 'VmPeak:')):
                        vm = max(vm, int(line.split()[1]) * 1024)
            except FileNotFoundError:
                pass
            time.sleep(0.1)
    return dict(storage_precondition_path=str(preparation) if preparation else None,
                exit_code=process.returncode, wall_seconds=time.monotonic()-start,
                sampled_peak_rss_bytes=rss, sampled_vm_peak_bytes=vm,
                rlimit_as_bytes=budget_bytes if limit else None, sampling_interval_seconds=0.1)


def starling_memory_lower_bound(index, query, nav, workers):
    """Known live allocations for pinned float Starling on the current 64-bit ABI.

    Excludes libraries, stacks, allocator slack, visited sets and navigation edges.
    A bound below budget is NOT evidence that the run fits.
    """
    def header(path):
        with Path(path).open('rb') as f:
            return struct.unpack('<II', f.read(8))
    n, pq_bytes = header(str(index)+'_pq_compressed.bin')
    nq, dim = header(query)
    nav_n, nav_dim = header(str(nav)+'.data')
    assert nav_dim == dim
    with Path(str(index)+'_partition.bin').open('rb') as f:
        _, pages, partition_n = struct.unpack('<QQQ', f.read(24))
    assert partition_n == n
    aligned = (dim+7)//8*8
    per_worker = (4*16384*aligned + 128*4096 + 512*256 +
                  256*256*4 + 512*4 + 2*aligned*4)
    categories = dict(worker_fixed_buffers=workers*per_worker, pq_codes=n*pq_bytes,
                      partition_vectors=24*pages, partition_ids=4*n, id_to_page=4*n,
                      navigation_vectors=nav_n*aligned*4, queries=nq*aligned*4,
                      pq_centroids=256*dim*4)
    return dict(known_lower_bound_bytes=sum(categories.values()), categories=categories,
                per_worker_fixed_buffers=per_worker, accounting_complete=False,
                abi='Linux x86_64; sizeof(std::vector<unsigned>)=24',
                note='A lower bound only; excludes stacks, libraries, allocator overhead and other index structures.')


def export(ds, out, work, result, reference, query, gt, order, widths, reference_trace=None):
    with (work/'index_pq_compressed.bin').open('rb') as stream:
        pq_n, pq_bytes = struct.unpack('<II', stream.read(8))
    assert pq_n == result['base_count']
    result['pq_bytes'] = pq_bytes
    nqueries, dim = geometry(query)
    nqgt, ngt = geometry(gt)
    assert nqgt == nqueries and ngt >= 10
    truth = np.memmap(gt, dtype='<u4', mode='r', shape=(nqueries, ngt+1))[:, 1:11]
    metrics = native_rows(work/'05_search_disk_index.log')
    refrow = reference['summary_rows'][0]
    with (reference_trace or out.parent/'Ours-Disk/queries.jsonl').open() as f:
        trace_keys = set(json.loads(f.readline()))
    summaries = []
    with (out/'queries.jsonl').open('w') as trace:
        for width in widths:
            file = work/f'result_{width}_idx_uint32.bin'
            shape = np.fromfile(file, dtype='<u4', count=2)
            assert tuple(shape) == (nqueries, 10)
            ids = np.fromfile(file, dtype='<u4', offset=8).reshape(nqueries, 10)
            assert (ids < result['base_count']).all()
            recalls = []
            for i, qid in enumerate(order):
                assert len(set(map(int, ids[i]))) == 10
                recall = len(set(map(int, ids[i])) & set(map(int, truth[qid]))) / 10
                recalls.append(recall)
                row = dict.fromkeys(trace_keys)
                row.update(layer='05c', storage_mode='hybrid_disk', cache_mode='official_native',
                           dataset=ds, method='Starling-Disk', config_id='official_R48_beam1',
                           ablation='official_native', repeat_id=0, query_id=int(qid),
                           result_ids=ids[i].tolist(), search_width=width, beam_width=1,
                           workers=result.get('workers',32),
                           search_dram_budget_gib=result.get('search_dram_budget_gib',2), cache_nodes=0,
                           recall_at_10=recall, metrics_available=['result_ids', 'recall_at_10'])
                trace.write(json.dumps(row, allow_nan=False)+'\n')
            row = dict.fromkeys(refrow)
            row.update(metrics[width])
            row.update(config_id='official_R48_beam1', ablation='official_native',
                       search_param=width, search_width=width, effective_search_width=width,
                       recall=float(np.mean(recalls)), query_count=nqueries)
            summaries.append(row)
    result.update(status='done', summary_rows=summaries,
                  query_trace_sha256=sha256(out/'queries.jsonl'),
                  native_result_hashes={str(p): sha256(p) for p in work.glob('result*')})


def run_dataset(ds, root, workroot):
    lock = verify_source(REPO, 'starling')
    assert not lock.get('patches')
    out = root/ds/'Starling-Disk'
    out.mkdir(parents=True, exist_ok=False)
    reference = json.loads((root/ds/'Ours-Disk/result.json').read_text())
    base = REPO/'data'/ds/f'{ds}_base.fvecs'
    n, dim = geometry(base)
    result = dict.fromkeys(reference)
    result.update(schema_version=2, status='preflight', layer='05c', dataset=ds,
                  method='Starling-Disk', storage_mode='hybrid_disk', phase='test',
                  workers=32, search_dram_budget_gib=2.0, cache_mode='official_native',
                  source_suite='official_disk_baselines', source_kernel='official Starling page_search',
                  port_kind='official_native_disk', git_commit=lock['commit'], source_patches=[],
                  formal_ready=False, throughput_comparable=False, implementation_parity='not_verified',
                  memory_accounting_complete=False, base_count=n, dimension=dim,
                  query_trace_path=str(out/'queries.jsonl'), summary_rows=[],
                  acceptance_gaps=['native CLI has no per-query timing export',
                                   'native CLI has WARMUP=false; differs from historical 100-query warmup',
                                   'official cache semantics differ from historical c0',
                                   'configuration is not validation-tuned', 'single repeat diagnostic'])
    write(out/'result.json', result)
    (out/'queries.jsonl').touch()
    write(out/'command.json', [])
    write(out/'layout_revision.json', dict(status='official_native_layout',
          source_commit=lock['commit'], ours_layout_revision=None,
          note='Uses upstream Starling partitioner/index_relayout; Ours layout revision does not apply.'))
    for name in ['measured_parity.json', 'result.parity.json']:
        write(out/name, dict(status='not_verified', formal_ready=False,
                             reason='Unchanged official native CLI; no historical adapter parity measurement.'))
    write(out/'memory_measurement.json', dict(status='not_measured', category_attribution_complete=False))
    write(out/'storage_protocol.json', dict(protocol='official_native_cli', warmup_queries=0,
          cache_mode='official_native', node_cache=0, device_cache_controlled=False,
          cold_storage_claim=False, historical_protocol_equivalent=False))
    pre = REPO/'work/starling_target_dimension_preflight_20260916'/ds/'run_R64'
    shutil.copytree(pre, out/'preflight_R64', ignore=shutil.ignore_patterns('*.bin', '*.index', '*.data', '*sample*'))
    # Preserve exact native failure and dimension, not fabricated test measurements.
    if ds != 'gist':
        result.update(status='blocked', blocked_reason='Official FP32 node exceeds 4096-byte page; target-dimension R64 native build exits with SIGFPE.',
                      preflight_only=True, graph_degree=64)
        write(out/'command.json', json.loads((pre/'official_run.json').read_text())['commands'])
        shutil.copyfile(pre/'00_build_disk_index.log', out/'terminal.log')
        write(out/'result.json', result)
        print(ds, 'blocked: official page capacity', flush=True)
        return
    # R48 is an upstream parameter, not a source modification. High-dimensional
    # R48 full pipeline was separately validated before this full-data run.
    work = workroot/ds
    work.mkdir(parents=True, exist_ok=False)
    sourcecmd = json.loads((root/ds/'Ours-Disk/command.json').read_text())
    query = Path(sourcecmd[sourcecmd.index('--query')+1])
    gt = Path(sourcecmd[sourcecmd.index('--groundtruth')+1])
    nq, qdim = geometry(query)
    assert qdim == dim
    # Reconstruct the exact legacy input permutation from its first width trace.
    order = []
    with (root/ds/'Ours-Disk/queries.jsonl').open() as stream:
        for line in stream:
            order.append(json.loads(line)['query_id'])
            if len(order) == nq:
                break
    assert sorted(order) == list(range(nq))
    np.asarray(order, dtype='<u4').tofile(work/'order.u32')
    # Trace row order can be completion order; require the original input-order hash.
    assert sha256(work/'order.u32') == sourcecmd[sourcecmd.index('--query-order-sha256')+1], 'Trace order does not match legacy query order'
    querydata = np.memmap(query, dtype='<u4', mode='r', shape=(nq, dim+1))
    with (work/'query.bin').open('xb') as f:
        f.write(struct.pack('<II', nq, dim))
        for qid in order:
            assert int(querydata[qid, 0]) == dim
            f.write(querydata[qid, 1:].tobytes())
    widths = [int(x) for x in sourcecmd[sourcecmd.index('--integration-widths')+1].split(',')]
    args = parser().parse_args(['--method', 'Starling-Disk', '--base', str(base), '--query', str(query),
                               '--work-dir', str(work), '--degree', '48', '--build-width', '400',
                               '--build-threads', '16', '--workers', '32', '--beam', '1',
                               '--widths', *map(str, widths)])
    plan = commands(args, n)
    write(out/'command.json', plan)
    result.update(status='preparing_inputs', graph_degree=48, build_width=400, requested_pq_bytes=32, pq_bytes=None,
                  parameters={k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                  work_dir=str(work), query_split_sha256=sha256(query), query_order_sha256=sha256(work/'order.u32'),
                  binary_hashes={c[0]: sha256(c[0]) for c in plan})
    write(out/'result.json', result)
    fvecs_to_bin(base, work/'base.bin')
    result['input_hashes'] = {str(p):sha256(p) for p in [base,query,gt]}
    with (out/'terminal.log').open('w') as terminal:
        for i, cmd in enumerate(plan):
            verify_source(REPO, 'starling')
            result.update(status='running', running_stage=Path(cmd[0]).name)
            write(out/'result.json', result)
            log = work/f'{i:02d}_{Path(cmd[0]).name}.log'
            if i == 5:
                bound = starling_memory_lower_bound(work/'index', work/'query.bin', work/'nav_index', 32)
                write(out/'memory_preflight.json', bound)
                if bound['known_lower_bound_bytes'] > 2**31:
                    result.update(status='blocked_memory', blocked_reason='Known native allocations exceed 2 GiB before other process overhead.',
                                  failed_stage=None, returncode=None)
                    write(out/'result.json', result)
                    print(ds, 'blocked before search: native memory lower bound exceeds budget', flush=True)
                    return
            print(ds, f'{i+1}/{len(plan)}', cmd[0], flush=True)
            measurement = run_command(cmd, log, limit=i==5)
            terminal.write(log.read_text()); terminal.flush()
            if i==5:
                result.update(storage_precondition_path=measurement['storage_precondition_path'],
                              storage_precondition_sha256=sha256(measurement['storage_precondition_path']))
                measurement.update(category_attribution_complete=False, limit_scope='whole_process_virtual_address_space',
                                   enforcement='RLIMIT_AS in child before exec', status='measured')
                write(out/'memory_measurement.json', measurement)
            if measurement['exit_code']:
                result.update(status='failed', failed_stage=i, returncode=measurement['exit_code'])
                write(out/'result.json', result)
                return
    export(ds,out,work,result,reference,query,gt,order,widths)
    write(out/'result.json',result)
    print(ds,'completed diagnostic export',flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--result-root', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--datasets', nargs='+', choices=['gist','agnews','dbpedia'], default=['agnews','dbpedia','gist'])
    args = p.parse_args()
    for ds in args.datasets:
        if (args.result_root/ds/'Starling-Disk').exists():
            raise FileExistsError(f'Refusing to overwrite existing Starling results for {ds}')
    for ds in args.datasets:
        try:
            run_dataset(ds,args.result_root.resolve(),args.work_root.resolve())
        except Exception as exc:
            path = args.result_root/ds/'Starling-Disk/result.json'
            if path.exists():
                result = json.loads(path.read_text())
                if result['status'] not in ('done','blocked','failed'):
                    result.update(status='failed', error=str(exc)); write(path,result)
            raise


if __name__ == '__main__':
    main()
