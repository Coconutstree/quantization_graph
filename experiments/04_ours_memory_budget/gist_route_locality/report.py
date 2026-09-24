"""Report measured physical I/O reductions, preserving negative or unstable outcomes."""
import csv,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'results/04_ours_memory_budget/gist_route_locality'
def read(p):return json.loads(p.read_text())
def main():
 rows=[]
 for split in ['tune','test']:
  for p in sorted((OUT/split).glob('*/acceptance.json')):
   d=p.parent;result=read(d/'result.json')['summary_rows'][0];st=read(d/'routing_stats.json')['100'];mem=read(d/'memory_measurement.json');q=result['query_count']
   rows.append(dict(split=split,run=d.name,queries=q,recall=result['recall'],qps=result['qps'],route_pages_q=st['reads']/q,total_pages_q=result['sectors_4k_per_query'],nonroute_pages_q=result['sectors_4k_per_query']-st['reads']/q,route_hit_rate=st['hits']/st['requests'],route_merge_rate=st['merges']/st['requests'],route_wait_ms_q=st['wait_ns']/q/1e6,lock_ms_q=st['lock_wait_ns']/q/1e6,peak_rss_mib=mem['kernel_wait4_peak_rss_bytes']/2**20,vmpeak_mib=mem['sampled_high_water_bytes']['VmPeak']/2**20,source=str((d/'result.json').relative_to(OUT))))
 if not rows:return
 with (OUT/'measured_results.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 complete=len(rows)==8 and (OUT/'test_completed.json').exists()
 lines=['# GIST 1-bit分页：route codes按BFS聚簇','',f'状态：{"测量完成，见审计证据" if complete else "运行中，仅列已验收数据"}。','',
 '目的：在完整960维1-bit codes无法按既有预算策略常驻时，减少实际route读页。只改变codes的物理排列，图、逻辑ID、量化bits、factors、精算表示和搜索参数不变。', '',
 '固定538 MiB整进程RLIMIT_AS、16 MiB route页槽计账额度、256共享在途页、32 workers、L100、beam1，无额外记录缓存。两种布局用同一新二进制，所有搜索文件同一文件系统设备；各运行统一O_DIRECT预读，设备缓存不受控。两轮反序；每组预热100查询，独立test800保留原顺序。训练/调参tune100与test分开，不从test挑选布局参数。','',
 '## 实现','',
 '- 原布局：codes按原节点ID存储；BFS布局：codes按现有id_to_slot映射排列。读取时仅对codes换算物理slot，factors继续按逻辑ID索引。',
 '- 复用原记录布局的同一个Arc<LocalityLayout>，没有新增4 MiB映射数组，也没有挤占route页槽额度。',
 '- 全部1,000,000条codes逐条校验字节一致；factors尾段SHA-256一致。新sidecar保存在独立目录，旧索引未改。',
 '- 侧文件与映射哈希在启动时校验；BFS sidecar必须显式带布局标志，且只接受分页模式，防止用旧的resident读取方式错读重排数据。','',
 '## 逐轮实测','',
 '|split / run|Recall|QPS|route页/query|全部页/query|route hit %|RSS MiB|VmPeak MiB|',
 '|---|---:|---:|---:|---:|---:|---:|---:|']
 for x in rows:lines.append(f"|[{x['split']}/{x['run']}]({x['source']})|{x['recall']:.6f}|{x['qps']:.2f}|{x['route_pages_q']:.2f}|{x['total_pages_q']:.2f}|{x['route_hit_rate']*100:.2f}|{x['peak_rss_mib']:.2f}|{x['vmpeak_mib']:.2f}|")
 if complete:
  def groups(name):return sorted([x for x in rows if x['split']=='test' and x['run'].startswith(name)],key=lambda r:r['run'])
  a,b=groups('original'),groups('bfs');assert len(a)==len(b)==2
  avg=lambda xs,k:statistics.mean(x[k] for x in xs)
  harmonic=lambda xs:len(xs)/sum(1/x['qps'] for x in xs)
  assert len({x['recall'] for x in a+b})==1
  effects=dict(route_io_change=avg(b,'route_pages_q')/avg(a,'route_pages_q')-1,total_io_change=avg(b,'total_pages_q')/avg(a,'total_pages_q')-1,qps_change=harmonic(b)/harmonic(a)-1,paired_qps_changes=[y['qps']/x['qps']-1 for x,y in zip(a,b)],baseline_qps=harmonic(a),bfs_qps=harmonic(b),recall=a[0]['recall'])
  (OUT/'effects.json').write_text(json.dumps(effects,indent=2)+'\n')
  lines += ['', '## 独立test结论','',f"两轮等查询数QPS调和均值：**{harmonic(a):.2f} → {harmonic(b):.2f}（{effects['qps_change']:+.1%}）**。route物理读页变化 **{effects['route_io_change']:+.1%}**，全部物理读页变化 **{effects['total_io_change']:+.1%}**；Recall@10均为 **{a[0]['recall']:.6f}**。",'',
   '这里测的是在256页并发基础上进一步改变物理布局的效果，不能与此前64→256的百分比直接相加。所有已验收组逐查询结果ID、访问、剪枝和精算计数与原常驻参考一致；非route读页维持同一搜索路径下的计账。', '',
   ('本配置建议保留256页共享并发，并使用BFS聚簇route sidecar。收益来自实际读页减少，同时保留完整codes和原搜索语义。' if effects['route_io_change']<0 and min(effects['paired_qps_changes'])>0 else '未同时确认两轮同向吞吐收益与读页减少，暂不替换原布局。')]
 lines += ['', '## 范围与复现','',
 'BFS分支在启动时另做一次完整sidecar哈希校验；该加载读取不计入查询I/O/QPS，之后两组都预热100查询。因此读页结论针对测量阶段实际提交的物理页；设备缓存状态不能完全隔离，QPS属于本协议下的观测收益。', '',
 '仅验证GIST、L100、32 workers、538 MiB和本次BFS排列。两轮范围不是置信区间，不能据此断言其他数据集/width同样受益。538 MiB低于继承的常驻准入阈值，不等于已证明完整codes的物理运行下限。降维、压缩、预取不包含在本次效果中。','',
 '入口：`experiments/04_ours_memory_budget/gist_route_locality/prepare.py` 构建sidecar与独立二进制；`run.py` 串行验证；`report.py` 汇总。原二进制不能读取重排sidecar，新二进制需 `--routing-code-layout bfs` 和对应的index目录。','',
 '[完整CSV](measured_results.csv) · [布局证明](layout.json) · [factors与设备审计](factor_and_storage_audit.json) · [构建来源](build.json) · [运行矩阵](experiment.json) · [最终审计](audit.json) · [分页回归](routing_regression.log)']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n');print(len(rows),'accepted rows')
if __name__=='__main__':main()
