"""Isolated, validation-selected hybrid ratio experiment. No edits to accepted v3."""
import csv,fcntl,hashlib,json,os,random,shutil,struct,subprocess,sys,threading,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import protocol
import run as common
ROOT=protocol.ROOT
PRIOR=ROOT/'results/04_ours_memory_budget/v3_direct'
OUT=ROOT/'results/04_ours_memory_budget/hybrid_tuning'
WORK=ROOT/'work/ours_memory_budget/hybrid_tuning'
BINARY=WORK/'ours_hybrid_tuning'
RATIOS=[0,1250,2500,3750,5000,7500]
WIDTHS=protocol.WIDTHS
LIMIT=protocol.LIMIT
STATE={}
LOCK=threading.RLock()

def dump(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');tmp.replace(p)
def load(p):return json.loads(Path(p).read_text())
def sha(p):return protocol.sha(p)
def rows(folder):return load(folder/'result.json')['summary_rows']
def throughput(rs):return sum(r['query_count'] for r in rs)/sum(r['query_count']/r['qps'] for r in rs)
def select_ratio(scores):
 best=max(v for _,v in scores)
 return min(b for b,v in scores if v>=.99*best)
def render():
 with LOCK:
  STATE['updated_unix']=time.time()
  cur=STATE.get('current');d=OUT/'runs'/cur if cur else None
  STATE['completed_widths']=len(list((d/'memory_stats').glob('L*.json'))) if d else 0
  dump(OUT/'state.json',STATE)
  lines=['# Hybrid固定缓存额度配比实验','',f"状态：{STATE.get('status')}；当前：{cur}；当前已完成L：{STATE['completed_widths']}/40。",'',
   '总新增缓存额度固定1,247,580,160字节（1189.78515625 MiB），整个进程RLIMIT_AS仍为2 GiB。原BFS布局、beam1、workers32、INT8/DB1、100预热、40档L保持不变。无NUMA绑定。', '',
   '原200条验证查询按保存顺序前100条训练热点、后100条选择配额，二者与800条测试查询不重叠。每阶段沿用原流程：预热该阶段查询顺序前100条；调参集共100条，所以调参预热覆盖整批100条，属于重复查询预热口径，不宣称首次冷查询性能。', '',
   '邻接表最多占总额度25%；静态精细记录上限依次为0%、12.5%、25%、37.5%、50%、75%；扣除静态实际占用后的全部剩余空间给16分片FIFO page。只选正分热点，配额超过热点覆盖后可能出现相同实际配置，这些点仍照实记录，不声称不同配置。', '',
   '每个比例完整40档两次，第一轮固定种子打乱，第二轮反序；采用合并两次共8000条计时查询数/总查询秒数（40档每档100条每轮）排序，不挑单个L。以最高分的99%以内为近似并列，优先选精细记录配额更小者；这是预先固定的工程规则，不是统计显著性检验。', '',
   '统一O_DIRECT顺序预读在每个搜索进程前执行一次，失败即停；预读、热点训练、初始化及预热不计查询QPS。缓存维护与查询I/O全部计时，缓存计入进程预算。', '',
   '选定比例先写selection_lock.json，再使用已经验收的全部200条验证查询热点排名（与原v3相同）进行测试；测试结果不得反向更改比例。原25%与所选比例各两次交错测试，另在测试序列前后各跑一次无额外缓存基线。若选择25%，复用同一组作为default与selected，不重复伪造独立对照。', '',
   '所有纯缓存运行须逐查询结果/召回/访问/候选计数一致，I/O统计与trace相符，内存预算通过。基线相对同查询集参考完整40档总吞吐允许0.75–1.35用于阻止数量级漂移；该门槛不证明统计等效。失败停止，记录原因，不自动放宽额度或改变协议。','',
   '## 完成阶段','',*['- '+name for name in STATE.get('completed',[])]]
  if STATE.get('error'):lines+=['','失败原因：'+STATE['error']]
  if (OUT/'selection_lock.json').exists():
   sel=load(OUT/'selection_lock.json');lines+=['','## 验证集配额选择','',f"锁定精细记录配额：{sel['selected_bps']/100:.2f}%。",'', '| 精细记录上限 | 两轮合计QPS |','|---|---:|']
   lines +=[f"| {b/100:.2f}% | {v:.2f} |"for b,v in sel['scores']]
  test=[]
  for name in STATE.get('completed',[]):
   if name.startswith('test_'):
    r=next(r for r in rows(OUT/'runs'/name) if r['search_width']==100);test.append((name,r))
  if test:
   baselines=[r['qps'] for name,r in test if name.startswith('test_baseline')];base=len(baselines)/sum(1/q for q in baselines) if baselines else None
   lines+=['','## 测试集L100（单次行；基线分母为当前已完成基线的合并吞吐）','', '| 运行 | Recall | QPS | 相对基线 |','|---|---:|---:|---:|']
   for name,r in test:
    gain='—' if 'baseline' in name or base is None else f"{(r['qps']/base-1)*100:+.1f}%"
    lines.append(f"| {name} | {r['recall']:.6f} | {r['qps']:.2f} | {gain} |")
  if (OUT/'test_comparison.json').exists():
   comparison=load(OUT/'test_comparison.json')
   lines+=['','## 最终两轮合并结果','', '按每档两轮1600条查询/合计查询秒数计算QPS；百分比分母为本轮两次基线合并QPS。','', '| 方案 | L | Recall | QPS | 相对基线 |','|---|---:|---:|---:|---:|']
   for row in comparison:
    if row['L'] in [100,300,580]:lines.append(f"| {row['label']} | {row['L']} | {row['recall']:.6f} | {row['qps']:.2f} | {row['gain_percent']:+.1f}% |")
  text='\n'.join(lines)+'\n';(ROOT/'ours_hybrid_ratio_tests.md').write_text(text);(OUT/'report.md').write_text(text)
def phase(name):
 with LOCK:STATE['current']=name
 render();print(time.strftime('%F %T'),name,flush=True)
def monitor(stop):
 while not stop.wait(20):render()
def split_queries():
 v=load(PRIOR/'verification.json');source=ROOT/'artifacts/query_splits/gist/shared';order=struct.unpack('<200I',protocol.VALIDATION_ORDER.read_bytes())
 split={}
 for name,ids in [('train',order[:100]),('tune',order[100:])]:
  folder=OUT/'splits'/name;folder.mkdir(parents=True)
  for fname in ['validation_query.fvecs','validation_gt.ivecs']:
   path=source/fname;n,d=protocol.shape(path);raw=path.read_bytes();size=(d+1)*4
   (folder/fname).write_bytes(b''.join(raw[i*size:(i+1)*size]for i in ids))
  (folder/'order.u32').write_bytes(struct.pack('<100I',*range(100)))
  split[name]={'source_ids':list(ids),'query':str(folder/'validation_query.fvecs'),'groundtruth':str(folder/'validation_gt.ivecs'),'order':str(folder/'order.u32')}
 assert not set(split['train']['source_ids'])&set(split['tune']['source_ids'])
 dump(OUT/'splits.json',split);return split

def build():
 orig=WORK/'native';shutil.copytree(ROOT/'work/ours_memory_budget/v2/native',orig)
 manifest=load(PRIOR/'build.json')
 for name,digest in manifest['snapshot']['generated_sources'].items():
  if sha(orig/name)!=digest:raise ValueError('original generated snapshot drift '+name)
 for p,digest in manifest['snapshot']['original_sources'].items():
  if sha(p)!=digest:raise ValueError('source drift since v3 '+p)
 p=orig/'Cargo.toml';s=p.read_text().replace('ours-memory-budget-experiment-v2','ours-hybrid-ratio-experiment').replace('name="ours_memory_budget_v2"','name="ours_hybrid_tuning"');p.write_text(s)
 p=orig/'memory_experiment.rs';s=p.read_text();anchor='        "hybrid" | "nav_hybrid" => available / 4,'
 assert s.count(anchor)==2
 # Change only the record budget arm; leave graph budget exactly as before.
 idx=s.index('    let record_budget = match mode {');before=s[:idx];after=s[idx:]
 after=after.replace(anchor,'        "hybrid" | "nav_hybrid" => ratio_budget(available, profile),',1)
 helper='''fn budget_for_ratio(available: usize, bps: usize) -> usize {
    assert!(bps <= 7500, "record ratio exceeds 75%; graph cap remains 25%");
    ((available as u128 * bps as u128) / 10000) as usize
}
fn ratio_budget(available: usize, profile: &Path) -> usize {
    let path = profile.join("hybrid_ratio.json");
    let bps = if path.exists() {
        let value: serde_json::Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        value["record_bps"].as_u64().expect("record_bps must be an unsigned integer") as usize
    } else { 2500 };
    budget_for_ratio(available, bps)
}
#[cfg(test)]
mod ratio_tests {
    use super::*;
    #[test] fn exact_budget_and_default() {
        assert_eq!(budget_for_ratio(1247580160, 2500), 1247580160 / 4);
        assert_eq!(budget_for_ratio(1247580160, 0), 0);
        assert_eq!(budget_for_ratio(1247580160, 7500), 1247580160 * 3 / 4);
        for ratio in [0,1250,2500,3750,5000,7500] {
            assert!(budget_for_ratio(1247580160,ratio)+1247580160/4 <=1247580160);
        }
    }
    #[test] #[should_panic] fn rejects_overcommit() { budget_for_ratio(1000,7501); }
}
'''
 p.write_text(before+after+'\n'+helper)
 target=ROOT/'src/graph_core/target';cmd=['cargo','test','--release','--offline','--manifest-path',str(orig/'Cargo.toml'),'--target-dir',str(target)]
 with (OUT/'build.log').open('w')as f:
  listing=subprocess.run(cmd+['--','--list'],stdout=subprocess.PIPE,stderr=f,check=True,cwd=ROOT,text=True)
  tests=[line.split(': test')[0] for line in listing.stdout.splitlines() if line.endswith(': test')]
  assert len(tests)>=16
  # OnceLock cache and global counters persist for a process; isolate every test.
  for test in tests:
   f.write('ISOLATED TEST '+test+'\n');f.flush()
   subprocess.run(cmd+[test,'--','--exact'],stdout=f,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
  dump(OUT/'native_tests.json',{'status':'passed','isolated_processes':True,'tests':tests})
  subprocess.run([*cmd[:1],'build',*cmd[2:]],stdout=f,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
 shutil.copy2(target/'release/ours_hybrid_tuning',BINARY)
 dump(OUT/'build.json',{'sha256':sha(BINARY),'source_v3_sha256':manifest['sha256'],'generated_sources':{str(p):sha(p)for p in orig.glob('*.rs')},'original_sources':manifest['snapshot']['original_sources']})

def make_cmd(name,mode,split,profile,budget):
 cmd=load(PRIOR/'baseline/command.json');f=protocol.flags(cmd)
 f.update({'--memory-policy':mode,'--memory-cache-bytes':str(budget),'--memory-profile-dir':str(profile),'--memory-stats-dir':str(OUT/'runs'/name/'memory_stats'),'--result-json':str(OUT/'runs'/name/'result.json'),'--query-trace':str(OUT/'runs'/name/'queries.jsonl'),'--run-id':'native_integration_hybrid_ratio','--native-binary-sha256':sha(BINARY),'--implementation-fingerprint':'ours-hybrid-ratio-only'})
 if split:
  q,g,o=map(Path,[split['query'],split['groundtruth'],split['order']]);f.update({'--query':str(q),'--groundtruth':str(g),'--query-order':str(o),'--query-split-sha256':protocol.pair_hash(q,g),'--query-order-sha256':sha(o)})
 return [str(BINARY)]+[x for k,v in f.items()for x in (k,v)]
def guard(folder,reference):
 a=throughput(rows(folder));b=throughput(rows(reference));e={'ratio':a/b,'allowed':[.75,1.35],'passed':.75<=a/b<=1.35};dump(folder/'performance_gate.json',e)
 if not e['passed']:raise ValueError('baseline performance drift '+str(e))
def verify_run(folder,count,mode,target):
 rs=rows(folder)
 assert [(r['search_width'],r['beam_width'],r['query_count'])for r in rs]==[(w,1,count)for w in WIDTHS]
 result=load(folder/'result.json');assert result['direct_io'] and result['native_aio'] and result['workers']==32 and result['warmup_queries']==100
 trace=common.read_trace(folder/'queries.jsonl');assert set(trace)=={(w,q)for w in WIDTHS for q in range(count)}
 for w in WIDTHS:
  stats=load(folder/f'memory_stats/L{w}.json');assert stats['cache_reserved_bytes']<=BUDGET
  if mode in ['baseline','profile']:assert stats['cache_reserved_bytes']==0
  for key,tkey in [('read_pages','sectors_4k'),('read_bytes','bytes_read'),('io_requests','io_requests')]:
   assert sum(stats['operations'][k][key]for k in ['graph','compact','residual'])==sum(row[tkey]for (width,q),row in trace.items()if width==w)
 if target:
  evidence=common.parity(folder/'queries.jsonl',target/'queries.jsonl');dump(folder/'parity.json',evidence);assert evidence['passed']
 if mode=='profile':common.validate_profile(folder)
def execute(name,mode,split,profile,ratio=None,target=None,performance_ref=None):
 phase(name);folder=OUT/'runs'/name;folder.mkdir(parents=True);(folder/'memory_stats').mkdir()
 command=make_cmd(name,mode,split,profile,0 if mode in ['baseline','profile']else BUDGET)
 if sha(BINARY)!=load(OUT/'build.json')['sha256']:raise ValueError('binary drift')
 if sha(Path(__file__))!=load(OUT/'protocol.json')['driver_sha256']:raise ValueError('driver drift')
 v=load(PRIOR/'verification.json');common.assert_inputs_unchanged(v)
 config={'mode':mode,'ratio_bps':ratio,'cache_budget':BUDGET,'split':'test' if split is None else split,'profile':str(profile),'profile_hashes':{p.name:sha(p)for p in profile.glob('*')if p.is_file()} if mode=='hybrid' else {}}
 dump(folder/'identity.json',config)
 prep=common.prepare_search(command,folder/'storage_precondition.json')
 for item in prep['files']:assert item['sha256']==v['input_hashes'][item['path']]
 mem=common.load_measure().measure(command,folder,budget=LIMIT);assert mem['user_address_space_budget_passed']
 verify_run(folder,800 if split is None else 100,mode,target)
 if performance_ref:guard(folder,performance_ref)
 artifacts=[p for p in folder.rglob('*')if p.is_file()]
 dump(folder/'acceptance.json',{'status':'complete','artifact_sha256':{str(p.relative_to(folder)):sha(p)for p in artifacts}})
 with LOCK:STATE['completed'].append(name)
 render();return folder

def profile_view(name,source,ratio):
 folder=OUT/'profiles'/name;folder.mkdir(parents=True)
 for k in ['graph_scores.f64','payload_scores.f64']:
  shutil.copy2(source/k,folder/k)
 dump(folder/'hybrid_ratio.json',{'record_bps':ratio});return folder

def main():
 global BUDGET
 OUT.mkdir(parents=True,exist_ok=True)
 with (OUT/'queue.lock').open('a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if (OUT/'state.json').exists():raise FileExistsError('existing experiment; refuse overwrite')
  WORK.mkdir(parents=True,exist_ok=False)
  BUDGET=load(PRIOR/'hybrid/identity.json')['optional_allowance_bytes']
  assert BUDGET==1247580160
  STATE.update(status='running',pid=os.getpid(),started_unix=time.time(),current='preflight',completed=[])
  order=RATIOS.copy();random.Random(20260919).shuffle(order)
  dump(OUT/'protocol.json',{'ratios_bps':RATIOS,'repeat_orders':[order,list(reversed(order))],'budget_bytes':BUDGET,'graph_cap_bps':2500,'selector':'combined throughput across two full 40-width runs; within 1% of best choose smaller record quota','driver_sha256':sha(Path(__file__)),'storage_module_sha256':sha(ROOT/'src/disk_bench/storage_precondition.py'),'train_count':100,'tune_count':100,'test_count':800,'selection_uses_test':False})
  stop=threading.Event();thread=threading.Thread(target=monitor,args=(stop,),daemon=True);thread.start()
  try:
   for mode in ['baseline','profile','hybrid']:common.verify_acceptance(PRIOR/mode)
   split=split_queries();phase('build_and_unit_tests');build()
   train=execute('train_profile','profile',split['train'],OUT/'runs/train_profile')
   # Profile mode writes the ranking beside its stats (profile dir points to run dir).
   tune_ref=execute('validation_baseline','baseline',split['tune'],train)
   for rep,sequence in enumerate([order,list(reversed(order))],1):
    for ratio in sequence:
     view=profile_view(f'validation_r{rep}_p{ratio}',train,ratio)
     execute(f'validation_r{rep}_p{ratio}','hybrid',split['tune'],view,ratio,target=tune_ref)
   execute('validation_baseline_end','baseline',split['tune'],train,target=tune_ref,performance_ref=tune_ref)
   scores=[]
   for ratio in RATIOS:
    both=rows(OUT/f'runs/validation_r1_p{ratio}')+rows(OUT/f'runs/validation_r2_p{ratio}')
    scores.append((ratio,throughput(both)))
   selected=select_ratio(scores)
   dump(OUT/'selection_lock.json',{'selected_bps':selected,'scores':scores,'locked_unix':time.time(),'test_runs_started':False,'selection_evidence':{name:sha(OUT/'runs'/name/'acceptance.json')for name in STATE['completed']if name.startswith('validation_')}})
   # Use all 200 validation queries for final ranking; its accepted profile is unchanged.
   for ratio in set([2500,selected]):profile_view(f'final_p{ratio}',PRIOR/'profile',ratio)
   baseline=execute('test_baseline_start','baseline',None,train,target=PRIOR/'baseline',performance_ref=PRIOR/'baseline')
   sequence=[2500,selected,selected,2500] if selected!=2500 else [2500,2500]
   seen={}
   for ratio in sequence:
    seen[ratio]=seen.get(ratio,0)+1
    execute(f'test_p{ratio}_r{seen[ratio]}','hybrid',None,OUT/f'profiles/final_p{ratio}',ratio,target=baseline)
   execute('test_baseline_end','baseline',None,train,target=baseline,performance_ref=baseline)
   export();STATE.update(status='completed',current=None,finished_unix=time.time())
  except BaseException as e:
   STATE.update(status='failed',error=str(e));traceback.print_exc();raise
  finally:stop.set();thread.join();render()
def export():
 records=[]
 for name in STATE['completed']:
  folder=OUT/'runs'/name
  for r in rows(folder):
   st=load(folder/f"memory_stats/L{r['search_width']}.json");records.append({'run':name,**{k:r[k]for k in ['search_width','recall','qps','latency_p99_us','sectors_4k_per_query']},'cache_reserved_bytes':st['cache_reserved_bytes'],'payload_nodes':st['payload_nodes'],'graph_nodes':st['graph_nodes'],'page_capacity':st['page_capacity']})
 with (OUT/'results.csv').open('w',newline='')as f:
  writer=csv.DictWriter(f,fieldnames=records[0]);writer.writeheader();writer.writerows(records)
 dump(OUT/'summary.json',{'selection':load(OUT/'selection_lock.json'),'results':records})
 selected=load(OUT/'selection_lock.json')['selected_bps'];comparison=[]
 for w in WIDTHS:
  bases=[r for name in ['test_baseline_start','test_baseline_end'] for r in rows(OUT/'runs'/name) if r['search_width']==w]
  base=throughput(bases)
  for ratio in sorted(set([2500,selected])):
   rr=[r for rep in [1,2]for r in rows(OUT/f'runs/test_p{ratio}_r{rep}') if r['search_width']==w]
   assert rr[0]['recall']==rr[1]['recall']
   qps=throughput(rr);label=('原25%/选中方案' if selected==2500 else '原25%' if ratio==2500 else f'选中{ratio/100:g}%')
   comparison.append({'label':label,'L':w,'recall':rr[0]['recall'],'qps':qps,'baseline_qps':base,'gain_percent':(qps/base-1)*100})
 dump(OUT/'test_comparison.json',comparison)
if __name__=='__main__':main()
