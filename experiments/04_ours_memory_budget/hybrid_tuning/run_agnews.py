"""AG News six-ratio validation sweep followed by locked test confirmation."""
import csv,fcntl,json,os,random,shutil,struct,sys,threading,time,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import protocol
import run as common
ROOT=protocol.ROOT
OUT=ROOT/'results/04_ours_memory_budget/hybrid_agnews'
REF=ROOT/'results/archive/03_disk_system_unadmitted_20260921/agnews/test_L_400_w_32/raw/Ours-Disk'
GIST=ROOT/'results/04_ours_memory_budget/hybrid_tuning'
BINARY=ROOT/'work/ours_memory_budget/hybrid_tuning/ours_hybrid_tuning'
WIDTHS=protocol.WIDTHS
N=769382
RATIOS=[0,1250,2500,3750,5000,7500]
BUDGET=0
STATE={}
LOCK=threading.RLock()
def load(p):return json.loads(Path(p).read_text())
def dump(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');t.replace(p)
def sha(p):return protocol.sha(p)
def rows(p):return load(p/'result.json')['summary_rows']
def throughput(rs):return sum(r['query_count']for r in rs)/sum(r['query_count']/r['qps']for r in rs)
def refresh():
 with LOCK:
  STATE['updated_unix']=time.time();cur=STATE.get('current');folder=OUT/'runs'/cur if cur else None
  STATE['completed_widths']=len(list((folder/'memory_stats').glob('L*.json')))if folder else 0;dump(OUT/'state.json',STATE)
  text=['# AG News：hybrid六档配额选择与测试验收','',f"状态：{STATE['status']}；当前：{cur}；当前完成{STATE['completed_widths']}/40档。",'',
   '只测hybrid与无额外缓存基线。AG News独立200条验证查询按原保存顺序分成前100条训练热点、后100条选择配额；扫描0%、12.5%、25%、37.5%、50%、75%各两轮。AG News单独选比例，不假设GIST的37.5%最优。800条测试查询不参与热点排名及配额选择。', '',
   'AG News：769,382条、1024维；原R64/Lbuild400图、BFS合并页与独立residual页；原查询顺序、40档L、beam1、workers32、100预热、INT8/DB1、top10、原rerank规则不变。每阶段预热为该阶段查询顺序前100条。', '',
   '沿用GIST已测试的配额可配置二进制，不修改搜索内核。每个进程前统一O_DIRECT顺序预读实际搜索页一次；耗时单列。RLIMIT_AS 2 GiB，无NUMA绑定；内核及控制器缓存不计入进程上限。', '',
   f"AG News新增缓存统一额度：{BUDGET/2**20:.2f} MiB。" if BUDGET else '新增缓存额度待AG News首次完整基线测定：2048 MiB − 基线VmPeak − 64 MiB统一余量。',
   '邻接表上限保持新增额度25%；静态记录扫6档配额，其余实际剩余额度归16分片FIFO page。记录真实缓存节点数和page数量；热点覆盖不足时不同配额可能得到相同实际配置。100条调参查询全部出现在预热中，属于重复查询预热口径，与GIST配额扫描相同。', '',
   '执行顺序：内存校准基线 → 100条训练profile → 调参基线 → 6配额两轮（乱序＋反序） → 调参基线复测 → 锁定比例 → 200条全验证集profile → 测试基线 → 原25%与选定比例各两轮（原/选/选/原） → 测试基线复测。开始的800条校准基线仅用于内存额度、正确性和性能合理性检查，不进入配额选择分数。', '',
   '每组验收逐查询ID、召回、访问/候选计数、内存、I/O统计及输入哈希。基线前后完整吞吐比例要求0.75–1.35；校准基线对历史AG News同配置也使用此门槛，失败即停止解释和后续运行，门槛不是统计等效检验。', '',
   'QPS排除热点训练、缓存初始化、预读及预热；正式查询的缓存查找/维护和I/O全部计时。只有两轮及一个新增数据集，不宣称普遍最优。','', '## 已完成阶段','',*['- '+x for x in STATE.get('completed',[])]]
  if STATE.get('error'):text+=['','失败原因：'+STATE['error']]
  if (OUT/'selection_lock.json').exists():
   choice=load(OUT/'selection_lock.json');text+=['','## 验证集配额选择','',f"选中比例：{choice['selected_bps']/100:g}%。以两轮全部40档合计吞吐排序，不低于最高值99%的候选中优先较小配额；这是固定工程规则，不是统计等效检验。",'', '| 精细记录上限 | 两轮合计QPS |','|---|---:|']
   text += [f"| {ratio/100:.2f}% | {score:.2f} |" for ratio,score in choice['scores']]
  if (OUT/'comparison.json').exists():
   text+=['','## 两轮合并测试结果','', '| 方案 | L | Recall@10 | QPS | 相对基线 |','|---|---:|---:|---:|---:|']
   for row in load(OUT/'comparison.json'):
    if row['L'] in [100,300,580]:text.append(f"| {row['label']} | {row['L']} | {row['recall']:.6f} | {row['qps']:.2f} | {row['change']} |")
  text+='\n原始记录：results/04_ours_memory_budget/hybrid_agnews/runs/；每档保存queries.jsonl和memory_stats，整组结束保存result.json、memory_measurement.json和acceptance.json。\n'.splitlines()
  body='\n'.join(text)+'\n';(ROOT/'ours_hybrid_agnews_tests.md').write_text(body);(OUT/'report.md').write_text(body)
def phase(name):
 with LOCK:STATE['current']=name
 refresh();print(time.strftime('%F %T'),name,flush=True)
def monitor(stop):
 while not stop.wait(20):refresh()
def preflight():
 f=protocol.flags(load(REF/'command.json'));a=ROOT/'artifacts/query_splits/agnews/shared'
 resolved={'--query':a/'test_query.fvecs','--groundtruth':a/'test_gt.ivecs','--query-order':ROOT/'results/diagnostics/03_disk_system/runtime_test_L_400_w_32/single_test_w32_20260911/agnews/order.u32','--input-manifest':ROOT/'results/archive/native_runs/runs/agnews_05c_rerun_20260901_152210/manifests/inputs/05c_agnews.json','--ours-graph':ROOT/'artifacts/graphs/agnews/Ours/agnews_Ours_R64_Lbuild400.graph.bin'}
 for k in ['--query-order','--input-manifest','--ours-graph']:assert sha(resolved[k])==f[k+'-sha256'],k
 assert protocol.pair_hash(resolved['--query'],resolved['--groundtruth'])==f['--query-split-sha256']
 assert protocol.shape(resolved['--query'])==(800,1024)
 assert sorted(struct.unpack('<800I',resolved['--query-order'].read_bytes()))==list(range(800))
 valorder=ROOT/'results/archive/03_disk_system_unadmitted_20260921/agnews/legacy_snapshot_20260918/raw/legacy_snapshot/manifests/query_order_validate_16842eeb55cc_seed20260813.u32'
 assert sorted(struct.unpack('<200I',valorder.read_bytes()))==list(range(200))
 assert protocol.shape(a/'validation_query.fvecs')==(200,1024)
 def vectors(path):
  n,d=protocol.shape(path);raw=path.read_bytes();sz=4*(d+1);return {raw[i*sz:(i+1)*sz]for i in range(n)}
 assert not vectors(a/'validation_query.fvecs')&vectors(a/'test_query.fvecs')
 assert list(map(int,f['--integration-widths'].split(',')))==WIDTHS
 assert f['--workers']=='32' and f['--integration-beams']=='1' and f['--warmup-queries']=='100'
 layout=Path(f['--locality-layout-dir']);manifest=load(layout/'manifest.json');assert manifest['record_count']==N
 for name,key in [('graph_compact.pages','--locality-combined-sha256'),('residual.pages','--locality-residual-sha256'),('id_to_slot.u32','--locality-mapping-sha256')]:assert sha(layout/name)==f[key]
 for p,h in manifest['source_hashes'].items():assert sha(p)==h
 build=load(GIST/'build.json');assert sha(BINARY)==build['sha256']
 for p,h in build['generated_sources'].items():assert sha(p)==h
 base=ROOT/'data/agnews/agnews_base.fvecs';assert protocol.shape(base)==(N,1024)
 inp=load(resolved['--input-manifest']);assert sha(base)==inp['files']['base']['sha256']
 files=list(resolved.values())+[base,valorder,a/'validation_query.fvecs',a/'validation_gt.ivecs']+list(Path(f['--disk-index-dir']).glob('*'))+list(layout.glob('*'))
 hashes={str(p.resolve()):sha(p)for p in files if p.is_file()}
 info={'resolved':{k:str(v)for k,v in resolved.items()},'input_hashes':hashes,'validation_order':str(valorder),'binary_sha256':sha(BINARY),'record_count':N,'dimension':1024,'validation_test_overlap':0}
 dump(OUT/'verification.json',info);return info
def command(name,mode,v,split=None,profile_source=None):
 f=protocol.flags(load(REF/'command.json'));f.update(v['resolved']);folder=OUT/'runs'/name
 f.update({'--memory-policy':mode,'--memory-cache-bytes':str(BUDGET if mode=='hybrid' else 0),'--memory-profile-dir':str(folder if mode=='profile' else OUT/'profiles'/name if mode=='hybrid' else OUT/'runs/profile'),'--memory-stats-dir':str(folder/'memory_stats'),'--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),'--native-binary-sha256':v['binary_sha256'],'--run-id':'native_integration_hybrid_agnews_transfer','--implementation-fingerprint':'ours-hybrid-ratio-only'})
 if mode=='profile' or split:
  q=ROOT/'artifacts/query_splits/agnews/shared/validation_query.fvecs';g=q.with_name('validation_gt.ivecs');o=Path(v['validation_order'])
  if split:
   q,g,o=map(Path,[split['query'],split['groundtruth'],split['order']])
  f.update({'--query':str(q),'--groundtruth':str(g),'--query-order':str(o),'--query-split-sha256':protocol.pair_hash(q,g),'--query-order-sha256':sha(o)})
 return [str(BINARY)]+[x for k,value in f.items()for x in (k,value)]
def execute(name,mode,v,ratio=None,target=None,perfref=None,split=None,profile_source=None):
 phase(name);folder=OUT/'runs'/name;folder.mkdir(parents=True);(folder/'memory_stats').mkdir()
 assert sha(BINARY)==v['binary_sha256'];assert sha(__file__)==load(OUT/'protocol.json')['driver_sha256']
 common.assert_inputs_unchanged(v)
 if mode=='hybrid':
  source=profile_source or OUT/'runs/profile';common.verify_acceptance(source);profile=OUT/'profiles'/name;profile.mkdir(parents=True)
  for file in ['graph_scores.f64','payload_scores.f64']:shutil.copy2(source/file,profile/file)
  dump(profile/'hybrid_ratio.json',{'record_bps':ratio})
 cmd=command(name,mode,v,split,profile_source);identity={'binary_sha256':sha(BINARY),'mode':mode,'record_bps':ratio,'optional_allowance_bytes':BUDGET if mode=='hybrid'else 0,'input_hashes':v['input_hashes'],'profile_hashes':{p.name:sha(p)for p in profile.iterdir()}if mode=='hybrid'else {}}
 dump(folder/'identity.json',identity)
 pre=common.prepare_search(cmd,folder/'storage_precondition.json');assert pre['status']=='completed'
 for item in pre['files']:assert item['sha256']==v['input_hashes'][item['path']]
 mem=common.load_measure().measure(cmd,folder,budget=protocol.LIMIT);assert mem['user_address_space_budget_passed']
 count=100 if split else 200 if mode=='profile'else 800;rr=rows(folder);assert [(r['search_width'],r['beam_width'],r['query_count'])for r in rr]==[(w,1,count)for w in WIDTHS]
 result=load(folder/'result.json');assert result['direct_io'] and result['native_aio'] and result['workers']==32 and result['warmup_queries']==100
 trace=common.read_trace(folder/'queries.jsonl');assert set(trace)=={(w,q)for w in WIDTHS for q in range(count)}
 for w in WIDTHS:
  st=load(folder/f'memory_stats/L{w}.json');assert st['cache_reserved_bytes']<=identity['optional_allowance_bytes']
  for k,t in [('read_pages','sectors_4k'),('read_bytes','bytes_read'),('io_requests','io_requests')]:assert sum(st['operations'][kind][k]for kind in ['graph','compact','residual'])==sum(row[t]for (width,q),row in trace.items()if width==w)
 if mode=='profile':common.validate_profile(folder,expected_nodes=N)
 if target:
  parity=common.parity(folder/'queries.jsonl',target/'queries.jsonl');dump(folder/'parity.json',parity);assert parity['passed']
 if perfref:
  ratio=throughput(rr)/throughput(rows(perfref));dump(folder/'performance_gate.json',{'ratio':ratio,'passed':.75<=ratio<=1.35,'reference':str(perfref)})
  if not .75<=ratio<=1.35:raise ValueError('baseline performance drift; inspect evidence before continuing')
 dump(folder/'acceptance.json',{'status':'complete','artifact_sha256':{str(p.relative_to(folder)):sha(p)for p in folder.rglob('*')if p.is_file()}})
 with LOCK:STATE['completed'].append(name)
 refresh();return folder

def export():
 result=[];details=[]
 selected=load(OUT/'selection_lock.json')['selected_bps']
 for w in WIDTHS:
  base_rows=[r for name in ['baseline_start','baseline_end']for r in rows(OUT/'runs'/name)if r['search_width']==w];base=throughput(base_rows)
  result.append({'label':'基线','L':w,'recall':base_rows[0]['recall'],'qps':base,'change':'—'})
  for ratio in sorted(set([2500,selected])):
   rr=[r for rep in [1,2]for r in rows(OUT/f'runs/hybrid_p{ratio}_r{rep}')if r['search_width']==w];assert rr[0]['recall']==rr[1]['recall'];q=throughput(rr)
   result.append({'label':f'hybrid {ratio/100:g}%','L':w,'recall':rr[0]['recall'],'qps':q,'change':f'{(q/base-1)*100:+.1f}%'})
 dump(OUT/'comparison.json',result)
 for name in STATE['completed']:
  folder=OUT/'runs'/name
  for row in rows(folder):
   st=load(folder/f"memory_stats/L{row['search_width']}.json");details.append({'run':name,**{k:row[k]for k in ['search_width','recall','qps','latency_p99_us','sectors_4k_per_query']},**{k:st[k]for k in ['cache_reserved_bytes','graph_nodes','payload_nodes','page_capacity']}})
 with (OUT/'results.csv').open('w',newline='')as f:
  wr=csv.DictWriter(f,fieldnames=details[0]);wr.writeheader();wr.writerows(details)
 dump(OUT/'summary.json',{'results':details,'comparison':result})
def split_validation(v):
 order=struct.unpack('<200I',Path(v['validation_order']).read_bytes());result={}
 source=ROOT/'artifacts/query_splits/agnews/shared'
 for name,ids in [('train',order[:100]),('tune',order[100:])]:
  folder=OUT/'splits'/name;folder.mkdir(parents=True)
  for file in ['validation_query.fvecs','validation_gt.ivecs']:
   p=source/file;n,d=protocol.shape(p);raw=p.read_bytes();size=4*(d+1)
   (folder/file).write_bytes(b''.join(raw[i*size:(i+1)*size]for i in ids))
  (folder/'order.u32').write_bytes(struct.pack('<100I',*range(100)))
  result[name]={'source_ids':list(ids),'query':str(folder/'validation_query.fvecs'),'groundtruth':str(folder/'validation_gt.ivecs'),'order':str(folder/'order.u32')}
 assert not set(result['train']['source_ids'])&set(result['tune']['source_ids'])
 dump(OUT/'splits.json',result);return result

def main():
 global BUDGET
 OUT.mkdir(parents=True,exist_ok=True)
 with (OUT/'queue.lock').open('a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if (OUT/'state.json').exists():raise FileExistsError('refuse existing run')
  order=RATIOS.copy();random.Random(20260919).shuffle(order)
  dump(OUT/'protocol.json',{'dataset':'agnews','ratios_bps':RATIOS,'repeat_orders':[order,list(reversed(order))],'selection_rule':'combined throughput over two full 40L runs; within 1% choose smaller quota','train_count':100,'tune_count':100,'test_count':800,'target_dataset_retuning':True,'driver_sha256':sha(__file__),'storage_module_sha256':sha(ROOT/'src/disk_bench/storage_precondition.py'),'budget_formula':'2GiB - AGNews calibration baseline VmPeak -64MiB','calibration_results_used_for_ratio_selection':False})
  STATE.update(status='running',pid=os.getpid(),current='preflight',started_unix=time.time(),completed=[]);refresh()
  stop=threading.Event();t=threading.Thread(target=monitor,args=(stop,),daemon=True);t.start()
  try:
   v=preflight()
   calibration=execute('calibration_baseline','baseline',v,target=REF,perfref=REF)
   BUDGET=protocol.cache_allowance(load(calibration/'memory_measurement.json'));assert BUDGET>0
   dump(OUT/'budget.json',{'bytes':BUDGET,'baseline_memory_sha256':sha(calibration/'memory_measurement.json'),'safety_bytes':protocol.SAFETY})
   splits=split_validation(v)
   train=execute('train_profile','profile',v,split=splits['train'])
   reference=execute('validation_baseline','baseline',v,split=splits['tune'])
   for rep,sequence in enumerate([order,list(reversed(order))],1):
    for ratio in sequence:execute(f'validation_r{rep}_p{ratio}','hybrid',v,ratio,target=reference,split=splits['tune'],profile_source=train)
   execute('validation_baseline_end','baseline',v,target=reference,perfref=reference,split=splits['tune'])
   scores=[(ratio,throughput(rows(OUT/f'runs/validation_r1_p{ratio}')+rows(OUT/f'runs/validation_r2_p{ratio}')))for ratio in RATIOS]
   best=max(score for _,score in scores);selected=min(ratio for ratio,score in scores if score>=.99*best)
   dump(OUT/'selection_lock.json',{'selected_bps':selected,'scores':scores,'locked_unix':time.time(),'uses_test_scores':False,'validation_acceptance_sha256':{name:sha(OUT/'runs'/name/'acceptance.json')for name in STATE['completed']if name.startswith('validation_')}})
   execute('profile','profile',v)
   base=execute('baseline_start','baseline',v,target=calibration,perfref=calibration)
   sequence=[2500,selected,selected,2500] if selected!=2500 else [2500,2500];seen={}
   for ratio in sequence:
    seen[ratio]=seen.get(ratio,0)+1;execute(f'hybrid_p{ratio}_r{seen[ratio]}','hybrid',v,ratio,target=base)
   execute('baseline_end','baseline',v,target=base,perfref=base)
   export();STATE.update(status='completed',current=None,finished_unix=time.time())
  except BaseException as e:STATE.update(status='failed',error=str(e));traceback.print_exc();raise
  finally:stop.set();t.join();refresh()
if __name__=='__main__':main()
