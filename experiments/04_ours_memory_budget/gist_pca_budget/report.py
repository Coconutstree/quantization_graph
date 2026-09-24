"""Report measured data and explicit selection/failure boundaries."""
import csv,json,statistics
from pathlib import Path
from prepare import OUT,WORK,dump
def read(p):return json.loads(p.read_text())
def main():
 rows=[]
 for a in sorted(OUT.glob('*/*/acceptance.json')):
  folder=a.parent;cfg=read(folder/'config.json');plan=read(folder/'memory_plan.json');mem=read(folder/'memory_measurement.json');route=read(folder/'routing_stats.json')
  dim=int(folder.name.split('_')[0][1:])
  for x in read(folder/'result.json')['summary_rows']:
   w=x['search_width'];rs=route.get(str(w),{});n=x['query_count']
   rows.append(dict(split=folder.parent.name,run=folder.name,dim=dim,width=w,mode=plan['mode'],recall=x['recall'],qps=x['qps'],route_pages_q=rs.get('reads',0)/n,total_pages_q=x['sectors_4k_per_query'],nonroute_pages_q=x['sectors_4k_per_query']-rs.get('reads',0)/n,full4_pages_q=x['full4_page_reads'],rerank_pages_q=x['rerank_page_reads'],visited=x['visited_nodes'],query_prep_us=x['query_prep_us'],hit_rate=rs.get('hits',0)/max(1,rs.get('requests',0)),codes_mib=plan['codes_bytes']/2**20,factors_norms_mib=plan['factors_bytes']/2**20,pca_extra_mib=plan.get('pca_extra_reserved_bytes',0)/2**20,route_cache_mib=(plan['routing_page_data_bytes']+plan['routing_page_metadata_bytes'])/2**20,record_cache_mib=plan['optional_cache_bytes']/2**20,admission_mib=plan['admission_bytes']/2**20,vmpeak_mib=mem['sampled_high_water_bytes']['VmPeak']/2**20,source=str((folder/'result.json').relative_to(OUT))))
 if rows:
  with (OUT/'measured_results.csv').open('w') as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 spectrum=read(OUT/'spectrum.json')
 completed=(OUT/'state.json').exists() and read(OUT/'state.json').get('status')=='completed'
 superseded=(OUT/'state.json').exists() and read(OUT/'state.json').get('status')=='superseded'
 lines=['# GIST PCA route：按预算和 Recall 选择维度','',f'状态：{"实验完成" if completed else "已停止：按用户要求转向低维排序→4-bit验证（gist_pca_routing）" if superseded else "进行中，仅列已验收数据"}。','',
 'PCA 用 base-only 固定随机样本 32,768 行拟合，seed=20260921；没有使用 query 或 ground truth。128/256/512 维保留方差分别为 '+ '/'.join(f"{spectrum['retained'][str(k)]:.2%}" for k in (128,256,512))+'。累计方差不作为 Recall 替代。', '',
 '## 方法与内存口径','',
 '保留原始图、BFS record 布局、完整960维 compact/residual 精算和重排。低维路线重新生成 native RaBitQ codes/factors；PCA query 变换计入 query_prep 和端到端 QPS。低维 short_ip 不复用于完整维度精算。原始960基线保留其原有复用优化。', '',
 'PCA 后只量化主子空间；每个节点存 FP32 残差平方范数。正式候选使用残差范数下界 (||xr||−||qr||)²。投影部分使用完整浮点查询和Cauchy最坏情形误差，等价于现有公式epsilon=sqrt(code_dim−1)，并扣除浮点余量；不使用旧960维factors。stat3只作失效诊断。原始960仍用INT8查询、epsilon=1.9统计gate，因此性能差异还包含保守下界变松、失去short_ip复用，不能解释成纯PCA效果。下界针对原始平方L2，完整图搜索和压缩精排仍属ANN，并不保证Recall=1。', '',
 '当前审计实现保存完整960×960 PCA基及尾部方差，用于对比残差处理，所以各低维档矩阵均占3.52 MiB，而非仅保存960×d的理论最小量。这不是norm方案必需的最小开销，后续可以独立优化。另有均值/特征值、低维旋转及32个worker准备缓冲预留。factors按native实际stride计账，并另加4,000,000字节残差范数。', '',
 '538 MiB 指整进程 RLIMIT_AS。继承原校准 fixed=426 MiB 和64 MiB reserve，再显式增加PCA开销；每次实测 VmPeak 必须不超过计账峰值。阈值是保守准入规则，不等于物理可运行下限。固定缓存诊断使用各维度都能容纳的4 MiB页槽额度。自动分配时分页额度上限16 MiB，实际容量受剩余预算限制，并发上限256页。常驻后的可分配余量只在够record cache最小开销时分配。', '',
 '## 固定 L100、4 MiB route 页槽诊断','',
 '|维度|Recall|QPS|route页/query|全部页/query|非route页/query|命中率|','|---|---:|---:|---:|---:|---:|---:|']
 for x in rows:
  if x['split']=='tune' and '_fixed_' in x['run']:lines.append(f"|{x['dim']}|{x['recall']:.6f}|{x['qps']:.2f}|{x['route_pages_q']:.2f}|{x['total_pages_q']:.2f}|{x['nonroute_pages_q']:.2f}|{x['hit_rate']:.1%}|")
 lines+=['','## validation 宽度选择','',
 '预先锁定 Recall@10≥0.95，宽度40/60/80/100/140/180；不达标才扩展260/380/580。用tune100选择满足目标且QPS最高的实测宽度；独立test800只确认，不能重新选择。候选筛选只有单轮，吞吐选择有噪声；test两轮反序。实际Recall不完全相等，比较口径是共同目标下的离散实测点。','',
 '|维度|模式|width|Recall|QPS|总页/query|','|---|---|---:|---:|---:|---:|']
 for x in rows:
  if x['split']=='tune' and any(s in x['run'] for s in ('_grid_','_extend_')):lines.append(f"|{x['dim']}|{x['mode']}|{x['width']}|{x['recall']:.6f}|{x['qps']:.2f}|{x['total_pages_q']:.2f}|")
 lines+=['','## 独立 test','', '|维度|width|Recall|QPS调和均值|route页/query|总页/query|达到0.95|','|---|---:|---:|---:|---:|---:|---|']
 effects=[]
 for k in (128,256,512,960):
  xs=[x for x in rows if x['split']=='test' and x['dim']==k]
  if not xs:continue
  avg=lambda key:statistics.mean(x[key] for x in xs)
  qps=len(xs)/sum(1/x['qps'] for x in xs);eligible=min(x['recall'] for x in xs)>=.95
  item=dict(dim=k,width=xs[0]['width'],recall=avg('recall'),qps=qps,total_pages_q=avg('total_pages_q'),route_pages_q=avg('route_pages_q'),meets_target=eligible,repeats=len(xs));effects.append(item)
  lines.append(f"|{k}|{item['width']}|{item['recall']:.6f}|{qps:.2f}|{item['route_pages_q']:.2f}|{item['total_pages_q']:.2f}|{'是' if eligible else '否'}|")
 if completed:
  candidates=[x for x in effects if x['meets_target']]
  if candidates:
   best=max(candidates,key=lambda x:x['qps']);lines+=['',f"在本次538 MiB和0.95 Recall目标下，独立test达标配置中最高观测QPS为{best['dim']}维、width={best['width']}。这是当前候选集的结果，不是跨预算/数据集的普适最优维度。"]
  else:lines+=['','独立test没有配置达到预设目标，不能宣称相同Recall下获得优化收益；不据test重新调参。']
 dump(OUT/'effects.json',effects)
 lines+=['','## 实测预算账本','', '|维度/模式|codes MiB|factors+norms MiB|PCA额外预留MiB|route页槽MiB|record cache MiB|准入MiB|VmPeak MiB|','|---|---:|---:|---:|---:|---:|---:|---:|']
 for x in rows:
  if x['split']=='tune' and '_grid_' in x['run'] and x['width']==40:lines.append(f"|{x['dim']}/{x['mode']}|{x['codes_mib']:.2f}|{x['factors_norms_mib']:.2f}|{x['pca_extra_mib']:.2f}|{x['route_cache_mib']:.2f}|{x['record_cache_mib']:.2f}|{x['admission_mib']:.2f}|{x['vmpeak_mib']:.2f}|")
 lines+=['','原始960的native实际code stride为128 B，不能用960/8=120 B的理想值代替实际账本。总页包含route及图/compact/residual读取；非route页不单独等同于record页，CSV另列full4与rerank页计数。设备缓存不受控；所有组预读并预热100查询。历史结果保持不变。','',
 '[完整CSV](measured_results.csv) · [PCA谱](spectrum.json) · [协议](protocol.json) · [validation锁定](selection_lock.json) · [构建](build.json)']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n');print(len(rows),'accepted rows')
if __name__=='__main__':main()
