"""100 independent test queries; L100/300/580, two rounds, cold-start and warm.
Runs use original BFS files and preserve every result in a separate directory.
"""
import argparse, importlib.util, json, math, mmap, os, struct, sys, time
from pathlib import Path
from build import ROOT, WORK, sha, build
sys.path.insert(0,str(ROOT/'src/disk_bench'))
from storage_precondition import prepare_search
spec=importlib.util.spec_from_file_location('measure',ROOT/'scripts/local_runs/measure_05_process.py')
measurement=importlib.util.module_from_spec(spec);spec.loader.exec_module(measurement)
OUT=ROOT/'results/04_ours_memory_budget/routing_paged'
WIDTHS=[100,300,580]; MIB=1024**2

def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def flags(command):return dict(zip(command[1::2],command[2::2]))
def progress(message):
    print(time.strftime('%F %T'),message,flush=True)
    (ROOT/'ours_routing_paged_tests.md').write_text('# 低内存 routing 异步分页试跑\n\n'+message+'\n\n实验脚本：`experiments/04_ours_memory_budget/routing_paged/`\n\n结果：`results/04_ours_memory_budget/routing_paged/`\n\n100 条独立测试查询；L100/300/580；beam1、workers32；两轮。cold 为每档 L 空 routing cache 启动的整批查询，非每条查询冷缓存；warm 为同批 100 查询预热。设备缓存不受控。\n')

def inputs(dataset):
    template=ROOT/f'results/04_ours_memory_budget/hot_records_comparison/{dataset}/runs/hot_records_r1/command.json'
    f=flags(json.loads(template.read_text()));folder=OUT/dataset/'inputs';folder.mkdir(parents=True,exist_ok=True)
    order=struct.unpack('<800I',Path(f['--query-order']).read_bytes())[:100]
    identities={}
    for flag,name in [('--query','query.fvecs'),('--groundtruth','gt.ivecs')]:
        source=Path(f[flag]);raw=source.read_bytes();dim=struct.unpack_from('<I',raw)[0];stride=4*(dim+1)
        data=b''.join(raw[i*stride:(i+1)*stride] for i in order)
        target=folder/name
        if target.exists():assert target.read_bytes()==data
        else:target.write_bytes(data)
        identities[str(source)]=sha(source);f[flag]=str(target)
    target=folder/'order.u32';target.write_bytes(struct.pack('<100I',*range(100)))
    f['--query-order']=str(target);f['--query-order-sha256']=sha(target)
    f['--query-split-sha256']=sha(f['--query']) # Explicit subset identity, not original 800-query split.
    dump(folder/'selection.json',dict(original_test_query_ids=order,source_sha256=identities,subset_sha256={p.name:sha(p) for p in folder.iterdir() if p.suffix in ['.fvecs','.ivecs','.u32']}))
    for key in list(f):
        if key.startswith('--memory-'):del f[key]
    index=Path(f['--disk-index-dir']);header=(index/'ours_quantizer.bin').read_bytes()[:68]
    _,_,dim,centroids,count,_,_,msb,factor=struct.unpack('<8sI7Q',header)
    routing=count*(msb+factor);nonrouting=dim*centroids*4+32*(count+12*MIB)+256*MIB+64*MIB
    paging_overhead=9*count+32*64*(2*(msb+factor)+1024)+10*MIB
    return f,dict(count=count,routing_bytes=routing,nonrouting_bytes=nonrouting,paging_overhead_bytes=paging_overhead,
                  other_reservation_bytes=256*MIB,safety_reservation_bytes=64*MIB)

def precondition(command,folder):
    prepare_search(command,folder/'storage_precondition.json')
    # Routing sidecar is a disk search file in paged mode. Use the same direct pass
    # for resident controls too, and allow the original unaligned tail.
    path=Path(flags(command)['--disk-index-dir'])/'ours_db1_sidecar.bin'
    before=path.stat();digest=__import__('hashlib').sha256();offset=0;started=time.time()
    fd=os.open(path,os.O_RDONLY|os.O_DIRECT)
    try:
        with mmap.mmap(-1,4*MIB) as buffer:
            while offset<before.st_size:
                n=os.preadv(fd,[buffer],offset)
                if n<=0:raise RuntimeError('routing pre-read EOF')
                digest.update(memoryview(buffer)[:n]);offset+=n
    finally:os.close(fd)
    after=path.stat();assert (before.st_size,before.st_ino,before.st_mtime_ns)==(after.st_size,after.st_ino,after.st_mtime_ns)
    dump(folder/'routing_storage_precondition.json',dict(path=str(path),sha256=digest.hexdigest(),bytes=offset,io='O_DIRECT',included_in_query_timing=False,device_cache_controlled=False,seconds=time.time()-started))

def traces(folder):return [json.loads(line) for line in (folder/'queries.jsonl').read_text().splitlines()]
def routing_stats(folder):
    parsed={}
    for line in (folder/'terminal.log').read_text().splitlines():
        if line.startswith('routing_stats '):
            fields=dict(item.split('=',1) for item in line.split()[1:]);width=int(fields.pop('L'));phase=fields.pop('phase')
            parsed[(width,phase)]={k:int(v) for k,v in fields.items()}
    result={}
    for w in WIDTHS:
        if (w,'measurement_start') not in parsed:continue
        a,b=parsed[w,'measurement_start'],parsed[w,'measurement_end']
        result[w]={k:b[k]-a[k] for k in ['requests','hits','merges','reads','bytes','evictions','wait_ns']}
        result[w].update({k:b[k] for k in ['peak_active','peak_pages','capacity','inflight','reserved_bytes']})
    return result

def verify(folder,reference,mode,budget):
    data=traces(folder);ref=traces(reference);assert len(data)==len(ref)==300
    expected={(r['search_width'],r['query_id']):r for r in ref}
    fields=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates']
    for r in data:
        baseline=expected[r['search_width'],r['query_id']]
        for key in fields:assert r[key]==baseline[key],(key,r['search_width'],r['query_id'])
    stats=routing_stats(folder)
    if mode=='resident':assert not stats
    else:
        assert len(stats)==3
        for w,s in stats.items():
            assert s['peak_active']<=s['inflight']<=64 and s['peak_pages']<=s['capacity']
            rows=[r for r in data if r['search_width']==w];base=[r for r in ref if r['search_width']==w]
            for key,counter in [('sectors_4k','reads'),('bytes_read','bytes'),('io_requests','reads')]:
                assert sum(r[key] for r in rows)-sum(r[key] for r in base)==s[counter],(key,w)
    mem=json.loads((folder/'memory_measurement.json').read_text());assert mem['user_address_space_budget_passed']
    dump(folder/'routing_stats.json',stats)
    dump(folder/'acceptance.json',dict(passed=True,mode=mode,budget_bytes=budget,queries=300,fields=fields,reference=str(reference),binary_sha256=sha(WORK/'ours_routing_paged'),files={p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!='acceptance.json'}))

def execute(dataset,f,memory,mode,temperature,round_id):
    folder=OUT/dataset/'runs'/f'{temperature}_{mode}_r{round_id}'
    reference=OUT/dataset/'runs'/f'{temperature}_resident_r{round_id}'
    if (folder/'acceptance.json').exists():
        accepted=json.loads((folder/'acceptance.json').read_text());assert accepted['binary_sha256']==sha(WORK/'ours_routing_paged')
        for name,digest in accepted['files'].items():assert sha(folder/name)==digest
        return
    if folder.exists():raise RuntimeError('unaccepted run exists; inspect before retry: '+str(folder))
    folder.mkdir(parents=True)
    f=f.copy();routing=memory['routing_bytes']
    if mode=='resident':budget=2*1024**3
    else:
        fraction={'p25':.25,'p75':.75}[mode]
        capacity=math.floor(math.ceil((routing+24)/4096)*fraction)
        budget=memory['nonrouting_bytes']+memory['paging_overhead_bytes']+capacity*(4096+256)
        assert budget<memory['nonrouting_bytes']+routing,'requested paged footprint would fit resident routing'
    f.update({'--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),
        '--run-id':'native_integration_routing_paged','--repeat-id':str(round_id),'--workers':'32',
        '--warmup-queries':'100' if temperature=='warm' else '0','--search-dram-budget-gib':repr(budget/1024**3),
        '--integration-widths':'100,300,580','--integration-beams':'1','--parity-mode':'external',
        '--native-binary-sha256':sha(WORK/'ours_routing_paged'),'--implementation-fingerprint':'ours-routing-paged-v1',
        '--routing-other-reservation-bytes':str(memory['other_reservation_bytes']),
        '--routing-safety-reservation-bytes':str(memory['safety_reservation_bytes']),
        '--routing-max-inflight-pages':'64'})
    command=[str(WORK/'ours_routing_paged')]+[x for k,v in f.items() for x in [k,v]]
    dump(folder/'budget.json',dict(**memory,effective_budget_bytes=budget,mode=mode))
    progress(f'运行中：{dataset}/{temperature}_{mode}_r{round_id}')
    precondition(command,folder)
    measurement.measure(command,folder,budget=budget,timeout=3600)
    verify(folder,reference,mode,budget)

def summarize():
    rows=[]
    for dataset in ['gist','agnews']:
        for folder in sorted((OUT/dataset/'runs').glob('*')):
            if not (folder/'acceptance.json').exists():continue
            stats=json.loads((folder/'routing_stats.json').read_text());mem=json.loads((folder/'memory_measurement.json').read_text())
            for r in json.loads((folder/'result.json').read_text())['summary_rows']:
                w=r['search_width'];rows.append(dict(dataset=dataset,run=folder.name,L=w,recall=r['recall'],qps=r['qps'],p95_us=r['latency_p95_us'],p99_us=r['latency_p99_us'],total_pages_per_query=r['sectors_4k_per_query'],routing=stats.get(str(w)),VmPeak=mem['sampled_high_water_bytes']['VmPeak'],RSS=mem['kernel_wait4_peak_rss_bytes']))
    dump(OUT/'summary.json',rows)
    return rows

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build',action='store_true');parser.add_argument('--run',action='store_true');parser.add_argument('--dataset',choices=['gist','agnews']);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.build:build()
    if args.run:
        manifest=json.loads((WORK/'build.json').read_text());assert sha(WORK/'ours_routing_paged')==manifest['binary_sha256']
        for path,digest in manifest['source_sha256'].items():assert sha(ROOT/path)==digest,'source drift '+path
        dump(OUT/'build.json',manifest)
        for dataset in ([args.dataset] if args.dataset else ['gist','agnews']):
            f,memory=inputs(dataset);dump(OUT/dataset/'protocol.json',dict(**memory,widths=WIDTHS,workers=32,query_count=100,rounds=2))
            for round_id in [1,2]:
                for temperature in ['cold','warm']:
                    for mode in (['resident','p25','p75'] if round_id==1 else ['resident','p75','p25']):
                        execute(dataset,f,memory,mode,temperature,round_id);summarize()
        progress('全部试跑与一致性验收完成。汇总见 results/04_ours_memory_budget/routing_paged/summary.json')
    else:print('Use --build and/or --run; existing unaccepted runs are never overwritten.')
if __name__=='__main__':main()
