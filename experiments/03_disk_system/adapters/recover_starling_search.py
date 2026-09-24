#!/usr/bin/env python3
"""Resume official Starling search with an explicitly separate diagnostic budget."""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src/disk_bench"))
from official_sources import verify_source
from run_official_disk_baseline import REPO, sha256
from run_starling_existing_layout import export, run_command, starling_memory_lower_bound, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--budget-gib', type=float, default=4)
    parser.add_argument('--workers', type=int, default=32)
    args = parser.parse_args()
    original, out, work = [p.resolve() for p in (args.original,args.out,args.work)]
    if args.budget_gib <= 0 or args.workers < 1:
        raise ValueError('Positive budget and worker count required')
    lock = verify_source(REPO,'starling')
    assert not lock.get('patches')
    prior = json.loads((original/'result.json').read_text())
    index_dir = Path(prior['work_dir'])
    command = json.loads((original/'command.json').read_text())[-1]
    assert sha256(command[0]) == prior['native_binary_sha256']
    out.mkdir(parents=True,exist_ok=False)
    work.mkdir(parents=True,exist_ok=False)
    result = dict(prior)
    result.update(status='preflight',run_id=out.name,formal_ready=False,throughput_comparable=False,
                  workers=args.workers,search_dram_budget_gib=args.budget_gib,
                  source_artifact=str(original/'result.json'),summary_rows=[],
                  query_trace_path=str(out/'queries.jsonl'),query_trace_sha256=None,
                  failed_stage=None,returncode=None,failure_classification=None,
                  failure_explanation=None,recovery_diagnostics=None,running_stage=None,
                  diagnostic_only=True,work_dir=str(work),reused_index_dir=str(index_dir))
    result['parameters'] = dict(prior['parameters'],workers=args.workers,search_dram_budget_gib=args.budget_gib)
    (out/'queries.jsonl').touch()
    write(out/'result.json',result)
    for name in ['storage_protocol.json','measured_parity.json','result.parity.json','layout_revision.json']:
        shutil.copyfile(original/name,out/name)
    write(out/'memory_measurement.json',dict(status='not_measured',category_attribution_complete=False))
    command[command.index('-T')+1] = str(args.workers)
    command[command.index('--result_path')+1] = str(work/'result')
    write(out/'command.json',[command])
    lower = starling_memory_lower_bound(index_dir/'index',index_dir/'query.bin',index_dir/'nav_index',args.workers)
    budget = int(args.budget_gib*2**30)
    lower['budget_bytes'] = budget
    write(out/'memory_preflight.json',lower)
    if lower['known_lower_bound_bytes'] > budget:
        result.update(status='blocked_memory',blocked_reason='Known native allocations exceed requested budget.')
        write(out/'result.json',result)
        (out/'terminal.log').write_text(result['blocked_reason']+'\n')
        return
    (work/'index_pq_compressed.bin').symlink_to(index_dir/'index_pq_compressed.bin')
    log = work/'05_search_disk_index.log'
    (out/'terminal.log').symlink_to(log)
    result.update(status='running',running_stage='search_disk_index')
    write(out/'result.json',result)
    try:
        measurement = run_command(command,log,limit=True,budget_bytes=budget)
        result.update(storage_precondition_path=measurement['storage_precondition_path'],
                      storage_precondition_sha256=sha256(measurement['storage_precondition_path']))
        measurement.update(status='measured',category_attribution_complete=False,
                           enforcement='RLIMIT_AS before exec',limit_scope='whole_process_virtual_address_space')
        write(out/'memory_measurement.json',measurement)
        if measurement['exit_code']:
            result.update(status='failed',returncode=measurement['exit_code'])
        else:
            reference_dir = original.parent/'Ours-Disk'
            reference = json.loads((reference_dir/'result.json').read_text())
            refcommand = json.loads((reference_dir/'command.json').read_text())
            query = Path(refcommand[refcommand.index('--query')+1])
            gt = Path(refcommand[refcommand.index('--groundtruth')+1])
            order = np.fromfile(index_dir/'order.u32',dtype='<u4').tolist()
            widths = list(map(int,command[command.index('-L')+1:command.index('-W')]))
            verify_source(REPO,'starling')
            export('gist',out,work,result,reference,query,gt,order,widths,reference_dir/'queries.jsonl')
            result['running_stage'] = None
        write(out/'result.json',result)
        print('Final status:',result['status'],flush=True)
    except Exception as exc:
        result.update(status='failed',error=str(exc))
        write(out/'result.json',result)
        raise


if __name__ == '__main__':
    main()
