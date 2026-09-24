"""Pool paired pilot repetitions; retain broad-L parity runs separately."""
import csv,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'results/04_ours_memory_budget/routing_paged_optimized'
def main():
 raw=json.loads((OUT/'summary.json').read_text());rows=[]
 for dataset in ['gist','agnews']:
  for fraction in [25,75]:
   reference=[r for r in raw if r['dataset']==dataset and r['mode']=='v1' and r['fraction']==fraction and r['L']==100 and 'regression' not in r['run']]
   if len(reference)!=2:continue
   base=2/sum(1/r['qps'] for r in reference)
   for mode in ['v1','lru','clock']:
    rr=[r for r in raw if r['dataset']==dataset and r['mode']==mode and r['fraction']==fraction and r['L']==100 and 'regression' not in r['run']]
    if len(rr)!=2:continue
    qps=2/sum(1/r['qps'] for r in rr);assert rr[0]['recall']==rr[1]['recall'] and rr[0]['budget']==rr[1]['budget']
    rows.append(dict(dataset=dataset,cache_percent=fraction,mode=mode,recall=rr[0]['recall'],qps=qps,gain_percent=100*(qps/base-1),routing_pages_per_query=sum(r['routing']['reads'] for r in rr)/200,cv_wait_ms_per_query=sum(r['routing']['wait_ns'] for r in rr)/200/1e6,lock_wait_ms_per_query=None if mode=='v1' else sum(r['routing']['lock_wait_ns'] for r in rr)/200/1e6,lock_calls_per_query=None if mode=='v1' else sum(r['routing']['lock_calls'] for r in rr)/200,notifications_per_query=None if mode=='v1' else sum(r['routing']['notifications'] for r in rr)/200,submission_batch_pages=None if mode=='v1' else sum(r['routing']['submitted_pages'] for r in rr)/sum(r['routing']['submit_calls'] for r in rr),VmPeak=max(r['VmPeak'] for r in rr),RSS=max(r['RSS'] for r in rr),budget=rr[0]['budget']))
 (OUT/'pooled.json').write_text(json.dumps(rows,indent=2)+'\n')
 if rows:
  with (OUT/'pooled.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
 accepted=list(OUT.glob('*/runs/*/acceptance.json'));reg=sum('regression' in p.parent.name for p in accepted)
 lines=['# Routing 分页批量化优化结果','',f'状态：{"completed" if len(rows)==12 and reg==10 else "incomplete"}；L100 对照 {len(accepted)-reg}/28 组；扩展回归 {reg}/10 组。','',
  '同预算、同页槽数量、全局在途上限 64；GIST/AG News 既有测试顺序前 100 条；beam1、workers32、100 预热。L100 两轮反向交错比较 v1、批量 LRU、批量 CLOCK。',
  'QPS 按两轮总查询数／总计时时间合并；百分比以同期相同缓存容量的 v1 为分母。小规模两轮结果不作为统计显著性检验。',
  '', '| 数据集 | 缓存比例 | 实现 | Recall | 合并 QPS | 相对 v1 | routing 页/查询 | VmPeak MiB |', '|---|---:|---|---:|---:|---:|---:|---:|']
 for r in rows:lines.append(f"| {r['dataset']} | {r['cache_percent']}% | {r['mode']} | {r['recall']:.6f} | {r['qps']:.2f} | {r['gain_percent']:+.1f}% | {r['routing_pages_per_query']:.2f} | {r['VmPeak']/2**20:.2f} |")
 lines+=['','所有已验收组逐查询核对结果、召回、访问/距离/DB1 检查及幸存/精算/重排计数、精算和重排读页；总 I/O 增量与 routing 服务物理读一致，RLIMIT_AS/VmPeak/RSS 及在途容量验收通过。',
 '扩展回归覆盖 L300/580、两种优化策略和两种缓存容量，另有同期常驻参考，各一次；不与 L100 两轮性能表混合。',
 '', '实现包括批量 FFI 获取/释放、固定页锁外按记录复制、针对等待者的通知、双 eventfd 滚动补交、可复用连续工作区以及 CLOCK/LRU 对照。分页默认 CLOCK，可用 `--routing-cache-policy lru` 切回优化 LRU。',
 '条件变量等待口径随等待机制改变；v1 的低 wait_ns 不能解释为低 I/O 延迟，也不能直接用等待时间下降衡量新版。新指标含锁等待、加锁次数、通知次数、提交批次大小；v1 未采集这些字段，以 null 表示而非 0。',
 '准入预算采用保守预留，并未校准同预算常驻必然 OOM 的边界。设备缓存未受控；沿用统一 O_DIRECT 顺序预读协议。',
 '', '代码：`experiments/04_ours_memory_budget/routing_paged_optimized/`。',
 '结果：`results/04_ours_memory_budget/routing_paged_optimized/{gist,agnews}/runs/`。',
 '每组保存命令、预算、输入预读哈希、逐查询 trace、内存测量、routing 统计和 acceptance；汇总数据见本目录 summary.json、pooled.json/csv。',
 '历史 v1 文件和结果保持不变。']
 body='\n'.join(lines)+'\n';(ROOT/'ours_routing_paged_optimized_tests.md').write_text(body);(OUT/'report.md').write_text(body)
if __name__=='__main__':main()
