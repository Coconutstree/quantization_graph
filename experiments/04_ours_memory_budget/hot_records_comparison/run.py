"""Measure hot records; reuse accepted baseline and selected hybrid test runs."""
import argparse, csv, fcntl, importlib.util, json, os, shutil, sys, threading, time, traceback
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import protocol
# Do not import this driver itself as the common module.
spec = importlib.util.spec_from_file_location('memory_common', HERE.parent/'run.py')
common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)
ROOT = protocol.ROOT
BASE = ROOT/'results/04_ours_memory_budget'
OUT = BASE/'hot_records_comparison'
BINARY = ROOT/'work/ours_memory_budget/hybrid_tuning/ours_hybrid_tuning'
ORDER = [('hot_records_r1','hot_payload'), ('hot_records_r2','hot_payload')]
STATE = {}; LOCK = threading.RLock()

def load(p): return json.loads(Path(p).read_text())
def dump(p, value):
    p=Path(p);p.parent.mkdir(parents=True, exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');t.replace(p)
def rows(p): return load(p/'result.json')['summary_rows']
def qps(rs): return sum(r['query_count'] for r in rs)/sum(r['query_count']/r['qps'] for r in rs)
def sources(dataset):
    if dataset=='gist':
        prior=BASE/'hybrid_tuning'
        return dict(prior=prior, baseline=prior/'runs/test_baseline_start',
                    profile=BASE/'v3_direct/profile', verification=BASE/'v3_direct/verification.json',
                    budget=load(prior/'protocol.json')['budget_bytes'])
    prior=BASE/'hybrid_agnews'
    return dict(prior=prior, baseline=prior/'runs/baseline_start',profile=prior/'runs/profile',
                verification=prior/'verification.json',budget=load(prior/'budget.json')['bytes'])
def make_command(template, folder, profile, mode, budget, binary_sha):
    f=protocol.flags(template)
    f.update({'--memory-policy':mode,'--memory-cache-bytes':str(0 if mode=='baseline' else budget),
              '--memory-profile-dir':str(profile),'--memory-stats-dir':str(folder/'memory_stats'),
              '--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),
              '--run-id':'native_integration_hot_records_comparison','--native-binary-sha256':binary_sha})
    assert list(map(int,f['--integration-widths'].split(',')))==protocol.WIDTHS
    assert f['--workers']=='32' and f['--warmup-queries']=='100' and f['--integration-beams']=='1'
    return [str(BINARY)]+[str(x) for k,v in f.items() for x in (k,v)]
def render():
    with LOCK:
        STATE['updated_unix']=time.time()
        folder=OUT/STATE.get('dataset','')/'runs'/STATE.get('current','')
        STATE['completed_widths']=len(list((folder/'memory_stats').glob('L*.json')))
        dump(OUT/'state.json',STATE)
        text=['# GIST / AG News：hot records 与 hybrid 对比（复用既有对照）','',
              f"状态：{STATE.get('status')}；数据集：{STATE.get('dataset','—')}；阶段：{STATE.get('current','—')}；当前 {STATE['completed_widths']}/40 档 L。",'',
              '两个数据集依次运行，各使用完整800条测试查询、原40档L、beam1、workers32、100预热、原查询顺序和BFS布局。每个进程统一O_DIRECT完整顺序预读；预读失败即停。进程RLIMIT_AS 2 GiB，无NUMA绑定。', '',
              '按用户要求不再复测基线和hybrid。复用各数据集已验收的基线两次及选定hybrid两轮，仅补跑hot records两轮、每轮完整40档。取消的GIST未完成基线保存在cancelled_retest，不参与结果。', '',
              'GIST额外缓存上限1189.78515625 MiB；AG News上限1267.57421875 MiB，均沿用各自已校准额度，不因方法调整。hot records使用全部额度选择正分热点的compact＋residual，静态名单不替换，热点不足不填非热点。hybrid使用各数据集验证集已锁定的比例（当前均37.5%），邻接表上限25%，实际剩余额度给16分片FIFO页缓存。两者复用同一份200条独立验证查询热点排名；测试集不训练、不选比例。', '',
              '相同的是可用额度，实际内存可能不同；记录缓存实际计账、节点数、页容量、RSS/VmPeak以及读页/查询。QPS排除准备、初始化和预热，包含查询期间缓存维护和I/O。', '',
              '验收：40档全量逐查询ID、召回及访问/候选计数与基线一致；I/O统计与trace一致；进程内存及缓存额度通过；输入、二进制和profile哈希固定。复用对照原实验的基线漂移验收，不再增加同期基线。hot records与对照测量时间不同，当前对比不能排除时段/负载变化影响，不作为严格同期性能结论。失败保留日志并停止，不静默跳过或放宽门槛。', '',
              '每档两轮QPS按合计查询数/合计秒数汇总；阴影表示两轮最小–最大范围，不是置信区间。不平滑、不丢弃L。每个数据集分别生成基线、hot records、hybrid的Recall–QPS图。', '',
              '## 已完成','',*['- '+n for n in STATE.get('completed',[])]]
        if STATE.get('error'):text+=['','失败原因：'+STATE['error']]
        for dataset in ['gist','agnews']:
            p=OUT/dataset/'comparison.json'
            if not p.exists():continue
            text+=['',f'## {dataset} 两轮结果','', '| 方案 | L | Recall@10 | QPS | 相对基线 | 实际缓存MiB | 读页/查询 |','|---|---:|---:|---:|---:|---:|---:|']
            for r in load(p):
                if r['L'] in [100,300,580]:
                    text.append(f"| {r['method']} | {r['L']} | {r['recall']:.6f} | {r['qps']:.2f} | {r['gain_percent']:+.1f}% | {r['cache_mib']:.2f} | {r['pages_per_query']:.2f} |")
            text+=['',f'![{dataset}](results/04_ours_memory_budget/hot_records_comparison/{dataset}/figures/recall_qps.png)']
        text+=['','原始结果：`results/04_ours_memory_budget/hot_records_comparison/{gist,agnews}/runs/`。每组保留command.json、queries.jsonl、memory_stats/L*.json、result.json、memory_measurement.json、storage_precondition.json、acceptance.json；数据集结束输出comparison.json/csv和figures。', '',
               '运行：`python experiments/04_ours_memory_budget/hot_records_comparison/run.py --run`；可恢复已验收组，未验收的残留组须人工核查后处理。']
        body='\n'.join(text)+'\n'
        (ROOT/'ours_hot_records_hybrid_tests.md').write_text(body);(OUT/'report.md').write_text(body)
def monitor(stop):
    while not stop.wait(20):render()
def phase(dataset,name):
    with LOCK:STATE.update(dataset=dataset,current=name)
    render();print(time.strftime('%F %T'),dataset,name,flush=True)
def guard(folder,ref):
    ratio=qps(rows(folder))/qps(rows(ref));passed=.75<=ratio<=1.35
    dump(folder/'performance_gate.json',dict(ratio=ratio,reference=str(ref),passed=passed))
    assert passed, 'baseline performance drift'
def verify_run(folder,mode,budget,target):
    rr=rows(folder)
    assert [(r['search_width'],r['beam_width'],r['query_count']) for r in rr]==[(w,1,800) for w in protocol.WIDTHS]
    r=load(folder/'result.json');assert r['direct_io'] and r['native_aio'] and r['workers']==32 and r['warmup_queries']==100
    trace=common.read_trace(folder/'queries.jsonl');assert set(trace)=={(w,q) for w in protocol.WIDTHS for q in range(800)}
    for w in protocol.WIDTHS:
        st=load(folder/f'memory_stats/L{w}.json');assert st['mode']==mode
        assert st['cache_reserved_bytes'] <= (0 if mode=='baseline' else budget)
        if mode=='hot_payload':assert st['graph_nodes']==0 and st['page_capacity']==0
        for key,tkey in [('read_pages','sectors_4k'),('read_bytes','bytes_read'),('io_requests','io_requests')]:
            assert sum(st['operations'][k][key] for k in ['graph','compact','residual'])==sum(r[tkey] for (width,q),r in trace.items() if width==w)
    evidence=common.parity(folder/'queries.jsonl',target/'queries.jsonl');dump(folder/'parity.json',evidence);assert evidence['passed']
def execute(dataset,name,mode,s,profile,v,template):
    phase(dataset,name);folder=OUT/dataset/'runs'/name
    if (folder/'acceptance.json').exists():
        common.verify_acceptance(folder);return folder
    folder.mkdir(parents=True);(folder/'memory_stats').mkdir()
    frozen=load(OUT/'protocol.json')
    for p,h in frozen['driver_hashes'].items():assert protocol.sha(p)==h, 'driver drift '+p
    assert protocol.sha(BINARY)==frozen['binary_sha256']
    common.assert_inputs_unchanged(v)
    assert {p.name:protocol.sha(p) for p in profile.iterdir()}==load(OUT/dataset/'protocol.json')['profile_hashes']
    cmd=make_command(template,folder,profile,mode,s['budget'],frozen['binary_sha256'])
    dump(folder/'identity.json',dict(mode=mode,budget_bytes=s['budget'],binary_sha256=frozen['binary_sha256'],profile_hashes=load(OUT/dataset/'protocol.json')['profile_hashes'],input_hashes=v['input_hashes']))
    pre=common.prepare_search(cmd,folder/'storage_precondition.json');assert pre['status']=='completed'
    for item in pre['files']:assert item['sha256']==v['input_hashes'][item['path']]
    mem=common.load_measure().measure(cmd,folder,budget=protocol.LIMIT);assert mem['user_address_space_budget_passed']
    ref=s['baseline'] if name=='baseline_start' else OUT/dataset/'runs/baseline_start'
    verify_run(folder,mode,s['budget'],ref)
    if mode=='baseline':guard(folder,ref)
    dump(folder/'acceptance.json',{'status':'complete','artifact_sha256':{str(p.relative_to(folder)):protocol.sha(p) for p in folder.rglob('*') if p.is_file()}})
    with LOCK:STATE['completed'].append(dataset+'/'+name)
    render();return folder
GROUPS={'baseline':['baseline_start','baseline_end'],'hot_records':['hot_records_r1','hot_records_r2'],'hybrid':['hybrid_r1','hybrid_r2']}
def export(dataset):
    runs=OUT/dataset/'runs';result=[]
    for w in protocol.WIDTHS:
        base=qps([r for n in GROUPS['baseline'] for r in rows(runs/n) if r['search_width']==w])
        for method,names in GROUPS.items():
            rr=[r for n in names for r in rows(runs/n) if r['search_width']==w]
            assert rr[0]['recall']==rr[1]['recall']
            stats=[load(runs/n/f'memory_stats/L{w}.json') for n in names]
            mem=[load(runs/n/'memory_measurement.json') for n in names]
            hot=qps([r for n in GROUPS['hot_records'] for r in rows(runs/n) if r['search_width']==w])
            result.append(dict(method=method,L=w,recall=rr[0]['recall'],qps=qps(rr),gain_percent=100*(qps(rr)/base-1),
                min_qps=min(r['qps'] for r in rr),max_qps=max(r['qps'] for r in rr),
                versus_hot_records_percent=100*(qps(rr)/hot-1),
                max_process_vmpeak_mib=max(m['sampled_high_water_bytes']['VmPeak'] for m in mem)/2**20,
                max_process_rss_mib=max(m['kernel_wait4_peak_rss_bytes'] for m in mem)/2**20,
                cache_mib=sum(s['cache_reserved_bytes'] for s in stats)/len(stats)/2**20,
                pages_per_query=sum(sum(s['operations'][k]['read_pages'] for k in ['graph','compact','residual']) for s in stats)/1600,
                payload_nodes=stats[0]['payload_nodes'],graph_nodes=stats[0]['graph_nodes'],page_capacity=stats[0]['page_capacity']))
    dump(OUT/dataset/'comparison.json',result)
    with (OUT/dataset/'comparison.csv').open('w') as f:
        wr=csv.DictWriter(f,fieldnames=result[0]);wr.writeheader();wr.writerows(result)
    import plot
    plot.draw(dataset)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if not args.run:parser.error('pass --run to start or resume')
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'queue.lock').open('a') as own:
        fcntl.flock(own,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (OUT/'state.json').exists():STATE.update(load(OUT/'state.json'))
        STATE.update(status='waiting',pid=os.getpid(),error=None);STATE.setdefault('completed',[])
        STATE.setdefault('started_unix',time.time())
        stop=threading.Event();t=threading.Thread(target=monitor,args=(stop,),daemon=True);t.start()
        try:
            # Retain both queue locks throughout this experiment; no overlap with predecessor jobs.
            with (BASE/'hybrid_agnews/queue.lock').open('a') as ag, (BASE/'hybrid_tuning/queue.lock').open('a') as gi:
                for lock in [ag,gi]:
                    while True:
                        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                        except BlockingIOError:phase('queue','waiting_for_prior_experiments');time.sleep(20)
                for prior in ['hybrid_agnews','hybrid_tuning']:assert load(BASE/prior/'state.json')['status']=='completed', 'prerequisite failed or incomplete: '+prior
                STATE['status']='running'
                frozen=dict(binary_sha256=protocol.sha(BINARY),driver_hashes={str(p):protocol.sha(p) for p in [Path(__file__),HERE/'plot.py',HERE.parent/'run.py',HERE.parent/'protocol.py',HERE.parent/'hybrid_tuning/plot_comparisons.py',ROOT/'src/disk_bench/storage_precondition.py',ROOT/'scripts/local_runs/measure_05_process.py']},order=ORDER,comparison_design='historical accepted controls; hot records only; no contemporaneous baseline',limit_bytes=protocol.LIMIT,query_count=800,widths=protocol.WIDTHS)
                assert frozen['binary_sha256']==load(BASE/'hybrid_tuning/build.json')['sha256']
                if (OUT/'protocol.json').exists():assert load(OUT/'protocol.json')==frozen,'resume protocol drift'
                else:dump(OUT/'protocol.json',frozen)
                for dataset in ['gist','agnews']:
                    phase(dataset,'preflight');s=sources(dataset)
                    common.verify_acceptance(s['baseline']);common.verify_acceptance(s['profile'])
                    v=load(s['verification']);common.assert_inputs_unchanged(v)
                    selected=load(s['prior']/'selection_lock.json')['selected_bps']
                    profile=OUT/dataset/'profile';profile.mkdir(parents=True,exist_ok=True)
                    for name in ['graph_scores.f64','payload_scores.f64']:
                        src=s['profile']/name;dst=profile/name
                        if dst.exists():assert protocol.sha(dst)==protocol.sha(src)
                        else:shutil.copy2(src,dst)
                    ratio={'record_bps':selected}
                    if (profile/'hybrid_ratio.json').exists():assert load(profile/'hybrid_ratio.json')==ratio
                    else:dump(profile/'hybrid_ratio.json',ratio)
                    d=dict(budget_bytes=s['budget'],selected_bps=selected,selection_lock_sha256=protocol.sha(s['prior']/'selection_lock.json'),profile_source=str(s['profile']),profile_hashes={p.name:protocol.sha(p) for p in profile.iterdir()},source_baseline=str(s['baseline']))
                    if (OUT/dataset/'protocol.json').exists():assert load(OUT/dataset/'protocol.json')==d
                    else:dump(OUT/dataset/'protocol.json',d)
                    # Link only immutable, fully accepted prior test runs; never copy partial measurements.
                    selected_name=f'test_p{selected}' if dataset=='gist' else f'hybrid_p{selected}'
                    prior_runs=s['prior']/'runs'
                    reuse={'baseline_start':s['baseline'],
                           'baseline_end':prior_runs/('test_baseline_end' if dataset=='gist' else 'baseline_end'),
                           'hybrid_r1':prior_runs/(selected_name+'_r1'),
                           'hybrid_r2':prior_runs/(selected_name+'_r2')}
                    runs=OUT/dataset/'runs';runs.mkdir(parents=True,exist_ok=True)
                    for name,source in reuse.items():
                        common.verify_acceptance(source)
                        link=runs/name
                        if link.is_symlink():assert link.resolve()==source.resolve()
                        else:link.symlink_to(source.resolve(),target_is_directory=True)
                    dump(OUT/dataset/'reused_runs.json',{name:{'source':str(source),'acceptance_sha256':protocol.sha(source/'acceptance.json')} for name,source in reuse.items()})
                    template=load(s['baseline']/'command.json')
                    for name,mode in ORDER:execute(dataset,name,mode,s,profile,v,template)
                    export(dataset);render()
                STATE.update(status='completed',current='complete',finished_unix=time.time())
        except BaseException as e:
            STATE.update(status='failed',error=str(e));traceback.print_exc();raise
        finally:stop.set();t.join();render()
if __name__=='__main__':main()
