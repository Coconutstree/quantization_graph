"""After both datasets pass, merge four-method tables/figures into the requested report."""
import argparse,csv,fcntl,hashlib,importlib.util,json,os,re,shutil,sys,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'results/04_ours_memory_budget/dynamic_records'
OLD=ROOT/'results/04_ours_memory_budget/hot_records_comparison'
REPORT=ROOT/'ours_hot_records_hybrid_tests.md'
spec=importlib.util.spec_from_file_location('memory_common_merge',HERE.parent/'run.py')
sys.path.insert(0,str(HERE.parent));common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):
 t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');t.replace(p)
METHODS=['baseline','hot_records','hybrid','hot_dynamic']
NAMES={'baseline':['baseline_start','baseline_end'],'hot_records':['hot_records_r1','hot_records_r2'],'hybrid':['hybrid_r1','hybrid_r2'],'hot_dynamic':['hot_dynamic_r1','hot_dynamic_r2']}
def dataset_section(dataset):
 rows=load(OUT/dataset/'comparison.json')
 assert len(rows)==160
 assert {(r['method'],r['L']) for r in rows}=={(m,w) for m in METHODS for w in common.WIDTHS}
 for method in METHODS:
  for run in NAMES[method]:common.verify_acceptance(OUT/dataset/'runs'/run)
 # Ensure every existing three-method row remains numerically unchanged.
 old=load(OLD/dataset/'comparison.json')
 idx={(r['method'],r['L']):r for r in rows}
 for r in old:
  new=idx[r['method'],r['L']]
  for k in ['recall','qps','cache_mib','pages_per_query']:assert new[k]==r[k],(dataset,k)
 for w in common.WIDTHS:assert len({idx[m,w]['recall'] for m in METHODS})==1
 text=[f'## {dataset} 两轮结果','', '| 方案 | L | Recall@10 | QPS | 相对基线 | 缓存计账MiB | 读页/查询 |','|---|---:|---:|---:|---:|---:|---:|']
 for w in [100,300,580]:
  for method in METHODS:
   r=idx[method,w];gain='—' if method=='baseline' else f"{r['gain_percent']:+.1f}%"
   text.append(f"| {method} | {w} | {r['recall']:.6f} | {r['qps']:.2f} | {gain} | {r['cache_mib']:.2f} | {r['pages_per_query']:.2f} |")
 rel=f'results/04_ours_memory_budget/hot_records_comparison/{dataset}/figures'
 text+=['',f'![{dataset} 四方案Recall–QPS]({rel}/recall_qps.png)','',f'[PDF]({rel}/recall_qps.pdf) · [SVG]({rel}/recall_qps.svg) · [完整40档结果](results/04_ours_memory_budget/dynamic_records/{dataset}/comparison.csv)','']
 return '\n'.join(text)
def merge():
 assert load(OUT/'state.json')['status']=='completed'
 sections={d:dataset_section(d) for d in ['gist','agnews']}
 text=REPORT.read_text()
 for dataset,section in sections.items():
  pattern=rf'## {dataset} 两轮结果\n.*?(?=\n## |\n原始结果：|\Z)'
  text,count=re.subn(pattern,lambda _:section,text,flags=re.S);assert count==1
 text=re.sub(r'^状态：.*$', '状态：两个数据集全部完成；静态hot records、hybrid及新方案均为两轮完整40档结果。',text, count=1,flags=re.M)
 text=text.replace('# GIST / AG News：hot records 与 hybrid 对比（复用既有对照）','# GIST / AG News：hot records、hybrid 与动态记录缓存对比')
 text=text.replace('每个数据集分别生成基线、hot records、hybrid的Recall–QPS图。','每个数据集分别生成基线、hot records、hybrid、hot_dynamic四条Recall–QPS曲线。')
 note='''## 新方案：hot_dynamic（静态热点＋动态记录缓存）

保留原hot records静态热点；剩余额度建立16分片FIFO动态节点记录缓存。compact和residual分别在实际读到后填入，分别标记有效，不额外读盘补齐；静态热点不替换。动态节点首次插入进入FIFO，命中不更新顺序，满时淘汰所在分片最早插入节点。每档L清空动态内容后执行原100条预热，正式计时前只清计数。查询中的查找、复制、填充和淘汰均计入时间。

缓存计账包含静态记录、动态预留缓冲、映射和元数据；动态预留空间不等于有效数据量。两个数据集的额度可覆盖全库精细记录，本轮未必频繁触发淘汰；淘汰行为另有小容量测试。这里只新增新方案测量，既有对照没有重跑。测量时段及新方案二进制不同，不能完全排除环境与执行路径差异；不据此宣称普遍最优。

[新方案方法与进度](ours_dynamic_records_tests.md)。动态命中、有效字节、驻留节点、插入和淘汰见 `results/04_ours_memory_budget/dynamic_records/{gist,agnews}/dynamic_stats.csv`。新增曲线为两轮合并QPS，阴影是两轮最小—最大范围，不是置信区间；全部40档均保留。

'''
 if '## 新方案：hot_dynamic' not in text:text=text.replace('## 已完成',note+'## 已完成',1)
 for dataset in ['gist','agnews']:
  for i in [1,2]:
   line=f'- {dataset}/hot_dynamic_r{i}'
   if line not in text:text=text.replace('## 已完成\n','## 已完成\n\n'+line,1)
 before=sha(REPORT)
 for dataset in ['gist','agnews']:
  src=OUT/dataset/'figures';dst=OLD/dataset/'figures'
  for name in ['recall_qps.png','recall_qps.pdf','recall_qps.svg','source_runs.csv','qa.json','figure_contract.json','static_preflight.txt','pdf_text_audit.txt']:
   assert (src/name).is_file()
  with (src/'source_runs.csv').open() as f:
   rr=list(csv.DictReader(f));assert len(rr)==320 and len({r['series'] for r in rr})==4
  for file in src.iterdir():
   if file.is_file():
    tmp=dst/(file.name+'.tmp');shutil.copyfile(file,tmp);tmp.replace(dst/file.name)
 tmp=REPORT.with_suffix('.md.tmp');tmp.write_text(text);tmp.replace(REPORT)
 write(OUT/'merge_status.json',dict(status='completed',finished_unix=time.time(),report=str(REPORT),before_sha256=before,after_sha256=sha(REPORT),figures=[str(OLD/d/'figures/recall_qps.png') for d in ['gist','agnews']],visual_review='See per-figure qa.json; automatic export is not visual approval'))
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--wait',action='store_true');args=parser.parse_args()
 OUT.mkdir(exist_ok=True)
 with (OUT/'merge.lock').open('a') as own:
  fcntl.flock(own,fcntl.LOCK_EX|fcntl.LOCK_NB)
  write(OUT/'merge_status.json',dict(status='waiting',pid=os.getpid(),target=str(REPORT)))
  try:
   while True:
    state=load(OUT/'state.json')
    if state['status']=='failed':raise RuntimeError('experiment failed; report merge withheld: '+str(state.get('error')))
    if state['status']=='completed':break
    if not args.wait:raise RuntimeError('experiment not complete; use --wait')
    time.sleep(20)
   with (OUT/'queue.lock').open('a') as runlock:
    fcntl.flock(runlock,fcntl.LOCK_EX)
    merge()
   print('Merged report and both four-method figures',flush=True)
  except BaseException as e:write(OUT/'merge_status.json',dict(status='failed',error=str(e)));traceback.print_exc();raise
if __name__=='__main__':main()
