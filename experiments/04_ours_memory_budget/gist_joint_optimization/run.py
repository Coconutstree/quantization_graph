"""Serial, resumable paired validation of joint quotas and routing service knobs."""
import argparse
import importlib.util
import json
import os
import signal
from pathlib import Path
import struct
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MIB = 2**20
OUT = ROOT/'results/04_ours_memory_budget/gist_joint_optimization'
WORK = ROOT/'work/ours_memory_budget/gist_joint_optimization'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


s = module('joint_driver', HERE.parent/'gist_fixed_factors_budget/driver_base.py')
s.OUT, s.WORK, s.BIN = OUT, WORK, WORK/'ours_gist_joint_optimization'
s.PROFILE = ROOT/'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'
measure = module('joint_measure', HERE.parent/'gist_width40_test100/measurement.py')

# Fixed before observing new results. All use full 960-dimensional routing.
CONFIGS = {
    'A1_resident_r0': dict(mode='resident', record=0),
    'A2_resident_r8': dict(mode='resident', record=8),
    'A3_paged_p16_r0': dict(mode='paged', record=0),
    'A4_paged_p16_r8': dict(mode='paged', record=8),
    'B1_paged_p16_r96': dict(mode='paged', record=96),
    'H1_paged_hot10_r8': dict(mode='paged', record=8, hot=True),
    'S1_paged_i16_r8': dict(mode='paged', record=8, inflight=16),
    'S2_paged_i32_r8': dict(mode='paged', record=8, inflight=32),
    'S3_paged_lru_r8': dict(mode='paged', record=8, policy='lru'),
}


def read(p):
    return json.loads(p.read_text())


def inputs(split):
    f = s.base('full' if split == 'test' else 'calibration')
    if split != 'test':
        directory = ROOT/'results/04_ours_memory_budget/hybrid_tuning/splits'/split
        for flag, name in [('--query','validation_query.fvecs'), ('--groundtruth','validation_gt.ivecs')]:
            f[flag] = str(directory/name)
        order = OUT/'inputs/order100.u32'
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_bytes(struct.pack('<100I', *range(100)))
        f['--query-order'] = str(order)
    f['--query-order-sha256'] = s.sha(f['--query-order'])
    f['--query-split-sha256'] = s.sha(f['--query'])
    f['--implementation-fingerprint'] = 'joint-quota-full960'
    f['--run-id'] = 'native_integration_joint_optimization'
    return f


def verify(folder, reference, count):
    mem = read(folder/'memory_measurement.json')
    plan = read(folder/'memory_plan.json')
    assert mem['user_address_space_budget_passed'] and mem['hard_limit_verified']
    assert mem['sampled_high_water_bytes']['VmPeak'] <= plan['expected_peak_bytes'], 'peak exceeds fixed accounting'
    assert plan['expected_peak_bytes'] + plan['reserve_bytes'] == plan['admission_bytes'] <= plan['budget_bytes']
    traces = s.pilot.traces(folder)
    widths = sorted({r['search_width'] for r in traces})
    assert len(traces) == count*len(widths)
    routing = s.stats(folder)
    ref = {(r['search_width'],r['query_id']):r for r in s.pilot.traces(reference)} if reference else None
    for width in widths:
        rows = [r for r in traces if r['search_width']==width]
        assert len({r['query_id'] for r in rows}) == count
        st = read(folder/'memory_stats'/f'L{width}.json')
        assert st['cache_reserved_bytes'] <= plan['optional_cache_bytes']
        if plan['optional_cache_bytes']:
            assert st['payload_nodes'] > 0 and st['record_hits'] > 0, 'record cache not enabled'
        rs = routing.get(width, {})
        if rs:
            assert rs['peak_active'] <= rs['inflight'] == plan['max_inflight_pages'] <= 256
            assert rs['peak_pages'] <= rs['capacity'] == plan['routing_capacity_pages']
            assert rs['requests'] == rs['reads']+rs['hits']+rs['merges']
        for key, op, rk in [('sectors_4k','read_pages','reads'),('bytes_read','read_bytes','bytes'),('io_requests','io_requests','reads')]:
            assert sum(r[key] for r in rows) == sum(v[op] for v in st['operations'].values())+rs.get(rk,0), (width,key)
        if ref:
            for r in rows:
                for key in s.FIELDS:
                    assert r[key] == ref[width,r['query_id']][key], (width,r['query_id'],key)
    s.dump(folder/'routing_stats.json', routing)
    s.dump(folder/'acceptance.json', dict(passed=True, query_count=len(traces), reference=str(reference), binary_sha256=s.sha(s.BIN), fields=s.FIELDS,
        files={str(p.relative_to(folder)):s.sha(p) for p in folder.rglob('*') if p.is_file() and p.name!='acceptance.json'}))


def execute(name, config, split, rep, reference=None, widths=(100,), budget=640, profile=False):
    folder = OUT/split/f'{name}_r{rep}'
    if (folder/'acceptance.json').exists():
        a=read(folder/'acceptance.json')
        assert a['binary_sha256']==s.sha(s.BIN)
        for p,h in a['files'].items(): assert s.sha(folder/p)==h, p
        return folder
    assert not folder.exists(), f'Preserve failed/unaccepted run {folder}'
    (folder/'memory_stats').mkdir(parents=True)
    f=inputs(split)
    f.update({'--memory-policy':config['mode'], '--record-cache-budget-bytes':str(config['record']*MIB),
        '--route-page-budget-bytes':str(config.get('pages',16)*MIB), '--routing-max-inflight-pages':str(config.get('inflight',64)),
        '--routing-cache-policy':config.get('policy','clock'), '--search-dram-budget-gib':repr(budget/1024),
        '--repeat-id':str(rep), '--integration-widths':','.join(map(str,widths)),
        '--result-json':str(folder/'result.json'), '--query-trace':str(folder/'queries.jsonl'),
        '--memory-stats-dir':str(folder/'memory_stats'), '--auto-plan-output':str(folder/'memory_plan.json')})
    if profile: f['--routing-profile-output']=str(folder/'page_counts.u64')
    if config.get('hot'): f['--routing-hot-pages']=str(OUT/'inputs/hot10.u64')
    cmd=[str(s.BIN)]+[v for k,x in f.items() for v in (k,x)]
    s.dump(folder/'config.json',dict(config=config,split=split,budget_mib=budget,widths=widths,repeat=rep))
    s.progress(str(folder.relative_to(OUT)))
    s.dump(folder/'environment_start.json',dict(load=os.getloadavg(),time=time.time(),diskstats=Path('/proc/diskstats').read_text()))
    try:
        s.pilot.precondition(cmd,folder)
        measure.measure(cmd,folder,budget=budget*MIB,timeout=3600)
        s.dump(folder/'environment_end.json',dict(load=os.getloadavg(),time=time.time(),diskstats=Path('/proc/diskstats').read_text()))
        verify(folder,reference,800 if split=='test' else 100)
    except BaseException as e:
        s.dump(folder/'failure.json',dict(error=str(e),traceback=traceback.format_exc()))
        raise
    return folder


def setup():
    OUT.mkdir(parents=True, exist_ok=True)
    build=read(WORK/'build.json')
    assert s.sha(s.BIN)==build['binary_sha256']
    for p,h in build['sources'].items(): assert s.sha(p)==h
    s.dump(OUT/'build.json',build)
    s.dump(OUT/'calibration/lock.json',read(ROOT/'results/04_ours_memory_budget/gist_memory_auto_policy/calibration/lock.json'))
    a,b=inputs('train'),inputs('tune')
    def vectors(p):
        raw=Path(p).read_bytes();stride=4*(struct.unpack_from('<I',raw)[0]+1)
        return set(raw[i:i+stride] for i in range(0,len(raw),stride))
    assert not vectors(a['--query']) & vectors(b['--query'])
    old=read(s.PROFILE/'command.json');old=s.flags(old)
    assert Path(old['--query']).read_bytes()==Path(a['--query']).read_bytes()
    manifest=dict(configs=CONFIGS,binary_sha256=s.sha(s.BIN),calibration_source='auto-policy v2 inherited; actual VmPeak gate mandatory',
        training_query_sha=s.sha(a['--query']),evaluation_query_sha=s.sha(b['--query']),overlap=0,
        record_profiles={n:s.sha(s.PROFILE/n) for n in ['graph_scores.f64','payload_scores.f64']},repeats=2,widths=[100])
    p=OUT/'experiment.json'
    if p.exists(): assert read(p)==manifest
    else: s.dump(p,manifest)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--validation',action='store_true')
    parser.add_argument('--allocation',action='store_true')
    parser.add_argument('--scheduler',action='store_true')
    parser.add_argument('--confirm',action='store_true')
    parser.add_argument('--low-budget',action='store_true')
    args=parser.parse_args()
    if args.low_budget:
        selection=read(OUT/'confirmation_lock.json')
        limit=selection['configs']['T6_paged_scheduler_r8']['inflight']
        configs={
            'L0_538_p16_r0':dict(mode='paged',record=0),
            'L1_538_p8_r8':dict(mode='paged',pages=8,record=8),
            'L2_538_hot10':dict(mode='paged',record=0,hot=True),
            'L3_538_scheduler':dict(mode='paged',record=0,inflight=limit),
        }
        s.BIN=WORK/'ours_gist_joint_scheduler'
        assert s.sha(s.BIN)==read(WORK/'scheduler_build.json')['binary_sha256']
        s.dump(OUT/'low_budget_matrix.json',dict(configs=configs,budget_mib=538,repeats=2,split='tune'))
        for rep in [1,2]:
            order=list(configs) if rep==1 else list(reversed(configs))
            for name in order: execute(name,configs[name],'tune',rep,reference=OUT/'tune/A1_resident_r0_r1',budget=538)
        s.dump(OUT/'low_budget_completed.json',dict(passed=True))
        return
    if args.confirm:
        assert read(OUT/'validation_completed.json')['passed']
        assert read(OUT/'allocation_completed.json')['passed']
        assert read(OUT/'scheduler_completed.json')['passed']
        def throughput(name):
            q=[]
            for rep in [1,2]:
                result=read(OUT/'tune'/f'{name}_r{rep}'/'result.json')
                q.append(result['summary_rows'][0]['qps'])
            return 2/sum(1/x for x in q)
        candidates=[f'D{i}_paged_i{n}_r8' for i,n in enumerate([64,128,256],1)]
        selected=max(candidates,key=throughput)
        scheduler=read(OUT/'tune'/f'{selected}_r1'/'config.json')['config']
        configs={
            'T0_resident_r0':dict(mode='resident',record=0),
            'T1_resident_r8':dict(mode='resident',record=8),
            'T2_paged_p112_r0':dict(mode='paged',pages=112,record=0),
            'T3_paged_p16_r96':dict(mode='paged',record=96),
            'T4_paged_p16_r8':dict(mode='paged',record=8),
            'T5_paged_hot10_r8':dict(mode='paged',record=8,hot=True),
            'T6_paged_scheduler_r8':scheduler,
        }
        selection=dict(configs=configs,selected_scheduler=selected,validation_qps={n:throughput(n) for n in candidates},repeats=2,widths=[100],test_queries=800)
        path=OUT/'confirmation_lock.json'
        if path.exists(): assert read(path)==selection
        else:s.dump(path,selection)
        s.BIN=WORK/'ours_gist_joint_scheduler'
        assert s.sha(s.BIN)==read(WORK/'scheduler_build.json')['binary_sha256']
        reference=execute('T0_resident_r0',configs['T0_resident_r0'],'test',1)
        for rep in [1,2]:
            order=list(configs) if rep==1 else list(reversed(configs))
            for name in order:execute(name,configs[name],'test',rep,reference=reference if not(name=='T0_resident_r0' and rep==1) else None)
        s.dump(OUT/'confirmation_completed.json',dict(passed=True,selection=selection))
        return
    if args.scheduler:
        s.BIN=WORK/'ours_gist_joint_scheduler'
        build=read(WORK/'scheduler_build.json')
        assert s.sha(s.BIN)==build['binary_sha256']
        for p,h in build['sources'].items():assert s.sha(p)==h
        s.dump(OUT/'scheduler_build.json',build)
        configs={f'D{i}_paged_i{limit}_r8':dict(mode='paged',record=8,inflight=limit) for i,limit in enumerate([64,128,256],1)}
        s.dump(OUT/'scheduler_matrix.json',dict(configs=configs,repeats=2))
        reference=OUT/'tune/A1_resident_r0_r1'
        for rep in [1,2]:
            order=list(configs) if rep==1 else list(reversed(configs))
            for name in order:execute(name,configs[name],'tune',rep,reference=reference)
        s.dump(OUT/'scheduler_completed.json',dict(passed=True))
        return
    setup()
    reference=execute('A1_resident_r0',CONFIGS['A1_resident_r0'],'tune',1)
    if args.smoke: return
    if args.allocation:
        # Equal 112 MiB route-page + record quota: isolate the allocation frontier.
        configs={
            'C1_paged_p112_r0':dict(mode='paged',pages=112,record=0),
            'C2_paged_p80_r32':dict(mode='paged',pages=80,record=32),
            'C3_paged_p48_r64':dict(mode='paged',pages=48,record=64),
            'B1_paged_p16_r96':CONFIGS['B1_paged_p16_r96'],
        }
        s.dump(OUT/'allocation_matrix.json',dict(configs=configs,repeats=2,budget_mib=640,quota_sum_mib=112))
        for rep in [1,2]:
            order=list(configs) if rep==1 else list(reversed(configs))
            for name in order: execute(name,configs[name],'tune',rep,reference=reference)
        s.dump(OUT/'allocation_completed.json',dict(passed=True))
        return
    profile=execute('route_profile',dict(mode='paged',record=0),'train',0,profile=True)
    raw=(profile/'page_counts.u64').read_bytes();counts=struct.unpack('<'+'Q'*(len(raw)//8),raw)
    n=(16*MIB//4352)//10
    chosen=sorted(sorted(range(len(counts)),key=lambda i:(-counts[i],i))[:n])
    (OUT/'inputs/hot10.u64').write_bytes(struct.pack('<'+'Q'*n,*chosen))
    s.dump(OUT/'inputs/hot_protocol.json',dict(training=str(profile),hot_pages=n,capacity=16*MIB//4352,coverage=sum(counts[i] for i in chosen)/sum(counts),counts_sha=s.sha(profile/'page_counts.u64')))
    for rep in [1,2]:
        order=list(CONFIGS) if rep==1 else list(reversed(CONFIGS))
        for name in order:
            execute(name,CONFIGS[name],'tune',rep,reference=reference if not(name=='A1_resident_r0' and rep==1) else None)
    s.dump(OUT/'validation_completed.json',dict(passed=True,configs=list(CONFIGS),repeats=2))
    s.progress('validation_completed_test_confirmation_pending')


if __name__=='__main__':
    # Only pause the specifically identified workspace build; identity-check PID.
    pid=2388331
    proc=Path(f'/proc/{pid}/cmdline')
    paused=False
    try:
        if proc.exists():
            command=proc.read_bytes().replace(b'\0',b' ').decode()
            if 'work/05_disk_system_fair/msmarco_graph_build_20260915/run_diskann_fair' in command:
                status=Path(f'/proc/{pid}/status').read_text()
                assert '\nState:\tT' not in status, 'Build already paused by another task'
                os.kill(pid,signal.SIGSTOP);paused=True
                s.dump(OUT/'competing_build.json',dict(pid=pid,command=command,paused=time.time()))
        def terminate(signum, frame):
            raise KeyboardInterrupt(f'signal {signum}')
        signal.signal(signal.SIGTERM,terminate)
        main()
    finally:
        if paused:
            os.kill(pid,signal.SIGCONT)
            note=read(OUT/'competing_build.json');note['resumed']=time.time()
            s.dump(OUT/'competing_build.json',note)
