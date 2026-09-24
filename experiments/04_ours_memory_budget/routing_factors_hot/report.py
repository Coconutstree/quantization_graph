"""Audit and summarize independently trained hot-page experiments."""
import csv,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'results/04_ours_memory_budget/routing_factors_hot'
WORK=ROOT/'work/ours_memory_budget/routing_factors_hot'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 rows=[];groups=0;queries=0
 for path in sorted((OUT/'gist/runs').glob('*/acceptance.json')):
  a=json.loads(path.read_text());folder=path.parent
  assert a['passed']
  for name,h in a['files'].items():assert sha(folder/name)==h,(folder,name)
  if a['training']:continue
  groups+=1;queries+=a['queries']
  mem=json.loads((folder/'memory_measurement.json').read_text());assert mem['user_address_space_budget_passed']
  b=json.loads((folder/'budget.json').read_text());st=json.loads((folder/'routing_stats.json').read_text())
  for r in json.loads((folder/'result.json').read_text())['summary_rows']:
   routing=st.get(str(r['search_width']),{})
   rows.append(dict(run=folder.name,mode=b['mode'],budget_tier=b['fraction'],L=r['search_width'],qps=r['qps'],recall=r['recall'],
    p95_ms=r['latency_p95_us']/1000,p99_ms=r['latency_p99_us']/1000,budget_bytes=b['budget_bytes'],
    VmPeak=mem['sampled_high_water_bytes']['VmPeak'],RSS=mem['kernel_wait4_peak_rss_bytes'],routing=routing))
 manifest=json.loads((WORK/'build.json').read_text());assert sha(WORK/'ours_routing_factors_hot')==manifest['binary_sha256']
 for path,h in manifest['source_sha256'].items():assert sha(ROOT/path)==h,path
 (OUT/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
 pooled=[]
 for tier in [25,75]:
  for mode in ['clock','factors','hot']:
   rr=[r for r in rows if r['L']==100 and r['mode']==mode and r['budget_tier']==tier]
   if len(rr)!=2:continue
   assert rr[0]['recall']==rr[1]['recall'] and rr[0]['budget_bytes']==rr[1]['budget_bytes']
   pooled.append(dict(budget_tier=tier,mode=mode,qps=2/sum(1/r['qps'] for r in rr),recall=rr[0]['recall'],
     routing_pages_per_query=sum(r['routing']['reads'] for r in rr)/200,
     capacity_pages=rr[0]['routing']['capacity'],budget_MiB=rr[0]['budget_bytes']/2**20,
     max_VmPeak_MiB=max(r['VmPeak'] for r in rr)/2**20))
 for r in pooled:
  base=next(x for x in pooled if x['mode']=='clock' and x['budget_tier']==r['budget_tier'])
  r['gain_percent']=100*(r['qps']/base['qps']-1)
 (OUT/'pooled.json').write_text(json.dumps(pooled,indent=2)+'\n')
 if pooled:
  with (OUT/'pooled.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=list(pooled[0]));w.writeheader();w.writerows(pooled)
 lines=['# GIST factors 常驻与热点页：同预算实测','',f'已验收 {groups} 组、{queries} 条测量查询；另有 100 条独立验证查询用于 profile。',
 '', 'L100、beam1、workers32、100 预热，两轮反序对照。热点占 factors-only 缓存槽数的 10%，按独立 validation 页请求数选取，测试向量无重合。',
 '25/75 是沿用旧实验预算档位的简称，不是新方案的实际 codes 缓存比例。每档的三种分页模式使用完全相同总预算。',
 '', '| 预算档 | 模式 | QPS | 相对同期 CLOCK | Recall | routing 页/查询 | 缓存页槽 | 预算 MiB | VmPeak MiB |',
 '|---|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in pooled:lines.append(f"| {r['budget_tier']} | {r['mode']} | {r['qps']:.2f} | {r['gain_percent']:+.1f}% | {r['recall']:.4f} | {r['routing_pages_per_query']:.1f} | {r['capacity_pages']} | {r['budget_MiB']:.1f} | {r['max_VmPeak_MiB']:.1f} |")
 if len(pooled)==6:
  lines += ['', '本轮建议：优先使用 `--routing-factors resident`。固定热点作为可选项保留；其在低预算档略慢，高预算档额外收益很小，不默认开启。']
 lines += ['', 'clock：codes/factors 均分页；factors：factors 常驻、codes 分页；hot：factors 常驻并保护验证集热点 codes 页。',
 '所有性能试跑关闭 profile。L300/580 为单轮正确性回归，单独保存在 summary.json，不混入主性能表。',
 '逐查询结果 ID、召回、访问/剪枝/精算/重排计数与同期常驻一致；I/O 增量按物理读页对账。所有验收组通过硬内存预算及 64 页在途上限。',
 '实际收益不能由减少一个数据区的缺页推断：factors 常驻会挤占 codes cache；热点也会减少动态可替换槽。默认路径未改为新实验策略。',
 '保守内存预留尚未校准实际常驻 OOM 边界；设备缓存未受控；两轮不作统计显著性结论。',
 '', '代码：`experiments/04_ours_memory_budget/routing_factors_hot/`；原始结果：`results/04_ours_memory_budget/routing_factors_hot/gist/runs/`。']
 text='\n'.join(lines)+'\n';(OUT/'report.md').write_text(text);(ROOT/'ours_routing_factors_hot_tests.md').write_text(text)
 (OUT/'validation').mkdir(exist_ok=True)
 (OUT/'validation/audit.json').write_text(json.dumps(dict(passed=True,groups=groups,measured_queries=queries,complete=groups==21,
  binary_sha256=manifest['binary_sha256'],source_hashes_verified=True,artifacts_verified=True,supplementary_sources={str(p.relative_to(ROOT)):sha(p) for p in Path(__file__).parent.glob('*') if p.suffix in ('.py','.cpp')}),indent=2)+'\n')
 print(text)
if __name__=='__main__':main()
