"""Run only the new dynamic scheme; reuse accepted controls, full original searches."""
import csv,fcntl,importlib.util,json,os,shutil,sys,threading,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('accepted_comparison',HERE.parent/'hot_records_comparison/run.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
ROOT=c.ROOT;OUT=c.BASE/'dynamic_records';PRIOR=c.BASE/'hot_records_comparison'
c.OUT=OUT;c.BINARY=ROOT/'work/ours_memory_budget/ours_dynamic_records'
c.GROUPS['hot_dynamic']=['hot_dynamic_r1','hot_dynamic_r2']
STATE=c.STATE

def render():
    with c.LOCK:
        STATE['updated_unix']=time.time()
        folder=OUT/STATE.get('dataset','')/'runs'/STATE.get('current','')
        STATE['completed_widths']=len(list((folder/'memory_stats').glob('L*.json')))
        c.dump(OUT/'state.json',STATE)
        text=['# 静态热点＋动态记录缓存实验','',f"状态：{STATE.get('status')}；数据集：{STATE.get('dataset','—')}；阶段：{STATE.get('current','—')}；当前 {STATE['completed_widths']}/40 档。",'',
        '## 方法','',
        'hot_dynamic：保留原200条独立验证查询训练的静态热点compact＋residual，静态部分与hot records完全相同；剩余额度建立16分片、固定容量的节点记录FIFO缓存。动态节点命中不改变FIFO顺序，新节点入队，满时淘汰所在分片最早插入的节点及其两部分记录。静态热点不淘汰。分片之间不借用额度。', '',
        '动态槽位预留compact＋residual空间，但仅在查询实际读到相应部分后标记有效；compact命中不代表residual命中。不额外读盘补齐，不提前扫描全库。缓存命中在锁内复制一次，避免并发淘汰导致批次结果错位。动态容量不超过该分片非静态节点数。', '',
        '动态缓存的节点映射、槽位ID、有效标记、数据缓冲及4096字节容器/分配余量均计账。固定缓冲一次分配；整个进程仍受2 GiB RLIMIT_AS硬限制。静态热点放满后若额度不足动态元数据和最小槽位，则不建动态缓存。', '',
        '每个L开始清空动态内容，再执行原100条预热；正式计时前只清统计计数，不清预热缓存。正式查询的缓存查找、复制、填充和淘汰均计时。初始化、预读和预热不计QPS。驻留状态是查询访问形成的，预留空间不等于已装入有效记录。', '',
        'GIST缓存额度1189.78515625 MiB，AG News 1267.57421875 MiB；原800条测试查询、40档L、workers32、beam1、INT8/DB1、BFS布局、查询顺序、rerank规则不变。每个新进程前统一完整O_DIRECT预读，失败即停。', '',
        '## 测试顺序与验收','',
        '仅跑GIST新方案两轮，再跑AG News新方案两轮。按用户要求不再复测基线、hot records和hybrid；引用其已验收两轮结果。跨时段、且新方案使用新二进制，性能差异不能完全归因于缓存。新二进制仅增加此缓存路径，旧策略路径保留。', '',
        '每轮验收全部40档、逐查询ID/召回/访问及候选计数一致、I/O计数与trace一致、缓存计账不超额度、进程2 GiB限制通过。静态节点数须与hot records一致。保存动态命中/未命中（compact、residual分别统计）、节点插入/淘汰、容量、驻留节点、有效字节；不能把固定预留容量当成命中或实际有效数据量。', '',
        'FIFO、分片、部分有效性、并发复制、预算及真实O_DIRECT读取路径均有测试；所有原native测试按独立进程运行，避免OnceLock状态污染。失败保留结果并停止，不静默降低参数或跳过验收。', '',
        '## 已完成','',*['- '+x for x in STATE.get('completed',[])]]
        if STATE.get('error'):text+=['','错误：'+STATE['error']]
        for dataset in ['gist','agnews']:
            p=OUT/dataset/'comparison.json'
            if not p.exists():continue
            text+=['',f'## {dataset} 两轮结果','', '| 方案 | L | Recall | QPS | 相对基线 | 相对静态hot records | 缓存计账MiB | 读页/查询 |','|---|---:|---:|---:|---:|---:|---:|---:|']
            for r in c.load(p):
                if r['L'] in [100,300,580]:text.append(f"| {r['method']} | {r['L']} | {r['recall']:.6f} | {r['qps']:.2f} | {r['gain_percent']:+.1f}% | {r['versus_hot_records_percent']:+.1f}% | {r['cache_mib']:.2f} | {r['pages_per_query']:.2f} |")
            text+=['',f'![{dataset}](results/04_ours_memory_budget/dynamic_records/{dataset}/figures/recall_qps.png)']
        text+=['','原始结果：`results/04_ours_memory_budget/dynamic_records/{gist,agnews}/runs/`。保留逐查询JSONL、40档memory_stats、QPS/recall汇总、预读、峰值内存和acceptance。完成后输出comparison.csv/json、dynamic_stats.csv和PNG/PDF/SVG。', '',
        '运行：`python experiments/04_ours_memory_budget/dynamic_records/run.py`。已有完整验收组可恢复；未验收残留需核查，不自动覆盖。']
        body='\n'.join(text)+'\n';(ROOT/'ours_dynamic_records_tests.md').write_text(body);(OUT/'report.md').write_text(body)
c.render=render
original_verify=c.verify_run
def verify(folder,mode,budget,target):
    original_verify(folder,mode,budget,target)
    dataset=STATE['dataset']
    original=c.load(PRIOR/dataset/'runs/hot_records_r1/memory_stats/L100.json')
    for w in c.protocol.WIDTHS:
        st=c.load(folder/f'memory_stats/L{w}.json');d=st['dynamic_records']
        assert st['graph_nodes']==0 and st['page_capacity']==0
        assert st['payload_nodes']==original['payload_nodes']
        assert st['cache_reserved_bytes']==original['cache_reserved_bytes']+d['reserved_bytes']
        assert d['resident_nodes']<=d['capacity_nodes']
        assert max(d['compact_records'],d['residual_records'])<=d['resident_nodes']
        assert sum(d['hits'])+sum(d['misses'])+st['record_hits']==sum(st['operations'][k]['logical_requests'] for k in ['compact','residual'])
c.verify_run=verify
original_command=c.make_command
def command(*args):
    cmd=original_command(*args);f=c.protocol.flags(cmd)
    f['--implementation-fingerprint']='ours-static-plus-dynamic-records-fifo'
    f['--run-id']='native_integration_dynamic_records'
    return [cmd[0]]+[x for k,v in f.items() for x in (k,v)]
c.make_command=command

def export(dataset):
    # The common exporter writes all GROUPS; supply our matching plotting module.
    p=importlib.util.spec_from_file_location('plot',HERE/'plot.py');m=importlib.util.module_from_spec(p);sys.modules['plot']=m;p.loader.exec_module(m)
    c.export(dataset)
    detail=[]
    for name in ['hot_dynamic_r1','hot_dynamic_r2']:
        for w in c.protocol.WIDTHS:
            s=c.load(OUT/dataset/'runs'/name/f'memory_stats/L{w}.json')['dynamic_records']
            detail.append(dict(run=name,L=w,**s))
    with (OUT/dataset/'dynamic_stats.csv').open('w') as f:
        wr=csv.DictWriter(f,fieldnames=detail[0]);wr.writeheader();wr.writerows(detail)

def main():
    OUT.mkdir(exist_ok=True)
    with (OUT/'queue.lock').open('a') as own,(PRIOR/'queue.lock').open('a') as prior:
        fcntl.flock(own,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(prior,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert c.load(PRIOR/'state.json')['status']=='completed'
        build=c.load(OUT/'build.json');assert c.protocol.sha(c.BINARY)==build['sha256']
        for p,h in build['generated_sources'].items():assert c.protocol.sha(p)==h
        assert c.load(OUT/'native_tests.json')['status']=='passed'
        hashes={str(p):c.protocol.sha(p) for p in [Path(__file__),HERE/'plot.py',HERE/'prepare.py',HERE/'dynamic_records.rs',HERE.parent/'hot_records_comparison/run.py',HERE.parent/'hybrid_tuning/plot_comparisons.py',HERE.parent/'run.py',HERE.parent/'protocol.py',ROOT/'src/disk_bench/storage_precondition.py',ROOT/'scripts/local_runs/measure_05_process.py']}
        frozen=dict(binary_sha256=build['sha256'],driver_hashes=hashes,design='new scheme only; two full runs per dataset; accepted historical controls')
        if (OUT/'protocol.json').exists():assert c.load(OUT/'protocol.json')==frozen
        else:c.dump(OUT/'protocol.json',frozen)
        if (OUT/'state.json').exists():STATE.update(c.load(OUT/'state.json'))
        STATE.update(status='running',pid=os.getpid(),error=None);STATE.setdefault('completed',[])
        stop=threading.Event();t=threading.Thread(target=c.monitor,args=(stop,),daemon=True);t.start()
        try:
            for dataset in ['gist','agnews']:
                c.phase(dataset,'preflight');s=c.sources(dataset);v=c.load(s['verification'])
                c.common.assert_inputs_unchanged(v);c.common.verify_acceptance(s['profile'])
                folder=OUT/dataset;profile=folder/'profile';profile.mkdir(parents=True,exist_ok=True)
                for name in ['graph_scores.f64','payload_scores.f64']:
                    src=s['profile']/name;dst=profile/name
                    if dst.exists():assert c.protocol.sha(dst)==c.protocol.sha(src)
                    else:shutil.copyfile(src,dst)
                c.dump(folder/'protocol.json',dict(budget_bytes=s['budget'],selected_bps=c.load(s['prior']/'selection_lock.json')['selected_bps'],profile_hashes={p.name:c.protocol.sha(p) for p in profile.iterdir()},hot_dynamic_policy='static hot records plus 16-shard node FIFO; lazy per-part valid bits'))
                runs=folder/'runs';runs.mkdir(exist_ok=True)
                reused={}
                for group,names in c.GROUPS.items():
                    if group=='hot_dynamic':continue
                    for name in names:
                        src=(PRIOR/dataset/'runs'/name).resolve();c.common.verify_acceptance(src)
                        link=runs/name
                        if link.is_symlink():assert link.resolve()==src
                        else:link.symlink_to(src,target_is_directory=True)
                        reused[name]=dict(source=str(src),acceptance_sha256=c.protocol.sha(src/'acceptance.json'))
                c.dump(folder/'reused_runs.json',reused)
                template=c.load(s['baseline']/'command.json')
                for name in ['hot_dynamic_r1','hot_dynamic_r2']:c.execute(dataset,name,'hot_dynamic',s,profile,v,template)
                export(dataset);render()
            STATE.update(status='completed',current='complete',finished_unix=time.time())
        except BaseException as e:STATE.update(status='failed',error=str(e));traceback.print_exc();raise
        finally:stop.set();t.join();render()
if __name__=='__main__':main()
