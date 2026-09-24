import csv,json,statistics
from prepare import OUT,dump
def read(p):return json.loads(p.read_text())
def main():
 rows=[]
 for a in sorted(OUT.glob('*/*/acceptance.json')):
  f=a.parent;plan=read(f/'memory_plan.json');mem=read(f/'memory_measurement.json');rs=read(f/'routing_stats.json')
  parts=f.name.split('_');dim=int(parts[0][1:]);keep=int(parts[1][1:])
  for x in read(f/'result.json')['summary_rows']:
   w=x['search_width'];n=x['query_count'];routing=rs.get(str(w),{});ms=read(f/'memory_stats'/f'L{w}.json');rp=routing.get('reads',0)/n
   rows.append(dict(split=f.parent.name,run=f.name,dim=dim,keep=keep,width=w,mode=plan['mode'],recall=x['recall'],qps=x['qps'],route_pages_q=rp,total_pages_q=x['sectors_4k_per_query'],nonroute_pages_q=x['sectors_4k_per_query']-rp,route_io_fraction=rp/max(1.,x['sectors_4k_per_query']),full4_pages_q=x['full4_page_reads'],rerank_pages_q=x['rerank_page_reads'],full4_candidates_q=x['full4_candidates'],route_checks_q=x['db1_checks'],query_prep_us=x['query_prep_us'],route_hit_rate=routing.get('hits',0)/max(1,routing.get('requests',0)),record_cache_hits_q=ms['record_hits']/n,codes_mib=plan['codes_bytes']/2**20,factors_mib=plan['factors_bytes']/2**20,pca_extra_mib=plan.get('pca_extra_reserved_bytes',0)/2**20,route_slots_mib=(plan['routing_page_data_bytes']+plan['routing_page_metadata_bytes'])/2**20,record_cache_mib=plan['optional_cache_bytes']/2**20,admission_mib=plan['admission_bytes']/2**20,vmpeak_mib=mem['sampled_high_water_bytes']['VmPeak']/2**20,source=str((f/'result.json').relative_to(OUT))))
 if rows:
  with (OUT/'measured_results.csv').open('w')as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 state=read(OUT/'state.json')if(OUT/'state.json').exists()else{}
 completed=state.get('status')=='completed'and state.get('matched_confirmation',False)
 lines=['# GIST：低维1-bit routing → 完整4-bit verification','',f'状态：{"两组测量完成"if completed else"测量尚未全部完成，仅列已验收结果"}。','',
 '目的：先验证低维1-bit能否通过候选排序帮助图搜索。每次扩展，PCA投影后的1-bit估计距离只用于邻居排序，前M个进入原始960维4-bit验证；本轮不把低维分数与tau比较，不执行低维下界hard prune。没有入选的节点以后仍可从其他边重新考虑。', '',
 'M=0表示全部邻居进入验证，是排序/表示正确性对照，不是“低维筛选有效”的证据。原始960基线保留此前完整1-bit统计gate和BFS分页优化；其M列记为原方法，不代表全部验证。完整4-bit及residual重排数据、图和BFS记录布局不变。PCA投影时间计入QPS。', '',
 'PCA拟合仅用32768条base样本；128/256/512维保留方差86.47%/93.69%/98.40%。低维native量化器重新生成对应codes/factors；两个量化器的每线程查询状态独立，跨维度short_ip复用关闭。这里不加载残差范数或尾部方差，在线矩阵只保留960×d；量化器旋转和worker缓冲仍显式计入预算。', '',
 '统一538 MiB整进程RLIMIT_AS、32 workers、beam1、256共享在途页。全960代码在当前保守准入账本下不能常驻。各档按实际code/factor/投影开销选择常驻或分页，常驻余量分配record cache，分页页槽额度最多16 MiB并受剩余预算约束。继承426 MiB固定预留和64 MiB reserve，每组检查实际VmPeak。', '',
 'validation tune100扫描M=0/16/32/48及width=60/100/180，目标Recall@10≥0.95；若无达标点，再按预定规则扩展width。每维选择达标且QPS最高的实测点，锁定后独立test800两轮反序确认。选参只一轮，可能有吞吐噪声；离散Recall不完全相同，不做插值伪造“精确相等”。','',
 '## validation结果','', '|维度|M|width|模式|Recall|QPS|route页/query|非route页/query|总页/query|','|---|---:|---:|---|---:|---:|---:|---:|---:|']
 for x in rows:
  if x['split']=='tune'and any(t in x['run']for t in('_grid_','_extended_')):
   lines.append(f"|{x['dim']}|{x['keep']if x['dim']!=960 else'原方法'}|{x['width']}|{x['mode']}|{x['recall']:.6f}|{x['qps']:.2f}|{x['route_pages_q']:.2f}|{x['nonroute_pages_q']:.2f}|{x['total_pages_q']:.2f}|")
 lines+=['','同一维度、同一缓存配置和width=100下，M=32与M=0全部验证的validation对照如下。这一对照用于判断低维排序是否能减少验证工作，不能代替独立test与原始960方法的最终比较。','', '|维度|全部验证Recall|M=32 Recall|全部验证总页/query|M=32总页/query|总I/O变化|','|---|---:|---:|---:|---:|---:|']
 for k in (128,256,512):
  control=next((x for x in rows if x['split']=='tune'and x['dim']==k and x['keep']==0 and x['width']==100 and'_grid_'in x['run']),None)
  shortlist=next((x for x in rows if x['split']=='tune'and x['dim']==k and x['keep']==32 and x['width']==100 and'_grid_'in x['run']),None)
  if control and shortlist:lines.append(f"|{k}|{control['recall']:.6f}|{shortlist['recall']:.6f}|{control['total_pages_q']:.2f}|{shortlist['total_pages_q']:.2f}|{shortlist['total_pages_q']/control['total_pages_q']-1:+.1%}|")
 effects=[]
 for cohort,tag,title,target in (
  ('common095','_locked_','共同validation目标0.95',.95),
  ('matched_baseline','_matched_','匹配基线validation Recall 0.961',.961)):
  lines+=['',f'## 独立test确认：{title}','']
  if cohort=='matched_baseline':
   lines+=['本组规则在查看任何test结果前另行锁定：从同一validation网格选择Recall≥原方法width=100的0.961且QPS最高的点。下表test Recall独立测量，不保证精确相等；只有实测Recall不低于基线才称为“Recall不降”的收益。','']
  lines+=['|维度|M|width|test Recall|QPS调和均值|route页/query|非route页/query|总页/query|轮数|', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
  for k in (128,256,512,960):
   xs=[x for x in rows if x['split']=='test'and x['dim']==k and tag in x['run']]
   if not xs:continue
   assert len({(x['keep'],x['width'])for x in xs})==1,(cohort,k)
   avg=lambda key:statistics.mean(x[key]for x in xs)
   e=dict(cohort=cohort,dim=k,keep=xs[0]['keep'],width=xs[0]['width'],recall=avg('recall'),qps=len(xs)/sum(1/x['qps']for x in xs),route_pages_q=avg('route_pages_q'),nonroute_pages_q=avg('nonroute_pages_q'),total_pages_q=avg('total_pages_q'),repeats=len(xs),validation_target=target,meets_target=min(x['recall']for x in xs)>=target)
   e['mode']=xs[0]['mode']
   for key in ('route_hit_rate','record_cache_hits_q','full4_candidates_q','full4_pages_q','rerank_pages_q'):e[key]=avg(key)
   effects.append(e);lines.append(f"|{k}|{e['keep']if k!=960 else'原方法'}|{e['width']}|{e['recall']:.6f}|{e['qps']:.2f}|{e['route_pages_q']:.2f}|{e['nonroute_pages_q']:.2f}|{e['total_pages_q']:.2f}|{e['repeats']}|")
  es=[x for x in effects if x['cohort']==cohort]
  baseline=next((x for x in es if x['dim']==960 and x['repeats']==2),None)
  if baseline:
   for x in es:
    if x['dim']==960 or x['repeats']!=2:continue
    relation='Recall不低于本组基线'if x['recall']>=baseline['recall']else'Recall低于本组基线，不能称为等Recall收益'
    lines+=['',f"{x['dim']}维/M={x['keep']}：{relation}（{x['recall']:.6f} vs {baseline['recall']:.6f}）；QPS {x['qps']/baseline['qps']-1:+.1%}，route读页 {x['route_pages_q']/baseline['route_pages_q']-1:+.1%}，非route读页 {x['nonroute_pages_q']/baseline['nonroute_pages_q']-1:+.1%}，总读页 {x['total_pages_q']/baseline['total_pages_q']-1:+.1%}。仅对本预算和锁定配置成立。"]
    if x['keep']==0:lines+=['','该维度锁定M=0，不能将全部验证对照的收益解释为低维1-bit筛选有效。']
    if cohort=='common095'and not x['meets_target']:lines+=['','该配置test未达到0.95目标；保留失败结果，不据test重新选参。']
 dump(OUT/'effects.json',effects)
 if completed:
  es=[x for x in effects if x['cohort']=='matched_baseline'];base=next(x for x in es if x['dim']==960)
  overview=['在538 MiB预算、test800两轮中，低维1-bit只做邻居排序、随后原维度4-bit验证的结果如下。参数均由validation锁定；表中为匹配基线validation Recall的确认组。','', '|维度|M / width|test Recall|QPS|总读页/query|','|---|---|---:|---:|---:|']
  for x in sorted(es,key=lambda x:0 if x['dim']==960 else x['dim']):
   overview.append(f"|{x['dim']}|{'原方法'if x['dim']==960 else str(x['keep'])} / {x['width']}|{x['recall']:.6f}|{x['qps']:.2f}|{x['total_pages_q']:.2f}|")
  winners=[x for x in es if x['dim']!=960 and x['keep']>0 and x['recall']>=base['recall']and x['qps']>base['qps']and x['total_pages_q']<base['total_pages_q']]
  if winners:
   for x in winners:overview+=['',f"{x['dim']}维确认了本配置的导航收益：Recall {base['recall']:.6f} → {x['recall']:.6f}，QPS {x['qps']/base['qps']-1:+.1%}，总读页 {x['total_pages_q']/base['total_pages_q']-1:+.1%}。不使用低维hard prune；候选筛选依然是近似搜索。"]
  else:overview+=['','本轮没有配置同时满足test Recall不低于基线、QPS提高、总I/O降低；保留以下全部结果。']
  overview+=['','0.95 validation目标组也完整保留在下文；该组低维M=16、width=100的test Recall未达到0.95，不据其高QPS宣称达标优化。所有读页数均以4 KiB为单位。','']
  lines[4:4]=overview
 lines+=['','## 匹配确认组的验证与缓存','', '|维度|4-bit验证候选/query|4-bit读页/query|重排读页/query|route页缓存命中率|record缓存命中/query|','|---|---:|---:|---:|---:|---:|']
 for x in effects:
  if x['cohort']=='matched_baseline':
   hit='常驻，无route页请求'if x['mode']=='resident'else f"{x['route_hit_rate']:.1%}"
   lines.append(f"|{x['dim']}|{x['full4_candidates_q']:.2f}|{x['full4_pages_q']:.2f}|{x['rerank_pages_q']:.2f}|{hit}|{x['record_cache_hits_q']:.2f}|")
 lines+=['','route命中率按hits/requests计算，在途请求合并另计；record命中为次数/查询，包含compact与residual请求，不等于唯一命中节点数。非route总页还包含图页，不能只拿route页减少量判断总I/O收益。']
 lines+=['','## 实测内存账本','', '|维度|模式|codes MiB|factors MiB|PCA额外预留MiB|route页槽MiB|record cache MiB|准入MiB|VmPeak MiB|','|---|---|---:|---:|---:|---:|---:|---:|---:|']
 for k in (128,256,512,960):
  xs=[x for x in rows if x['dim']==k and x['split']=='tune'and'_grid_'in x['run']]
  if xs:
   x=xs[0];lines.append(f"|{k}|{x['mode']}|{x['codes_mib']:.2f}|{x['factors_mib']:.2f}|{x['pca_extra_mib']:.2f}|{x['route_slots_mib']:.2f}|{x['record_cache_mib']:.2f}|{x['admission_mib']:.2f}|{max(y['vmpeak_mib']for y in xs):.2f}|")
 lines+=['','codes按实际布局计费：128/256/512维分别16/32/64 bytes/向量，原960维对齐后为128 bytes/向量。每个维度的factors均为20 bytes/向量。低维PCA额外预留为 `4×(960×d+960) + 16×d² + 32×16×960` bytes，包含投影矩阵及均值、量化器旋转和worker查询缓冲；残差范数及尾部方差在线占用为0。原维度4-bit/residual仍按需从SSD读取。准入中另含固定开销和reserve；VmPeak列为validation实测最大值，各次完整账本和硬限制验收独立保存。','',
 '在当前保守准入政策下，route codes常驻门槛约为128维525.52、256维542.00、512维576.46、960维631.14 MiB。它们是本实现的准入阈值，不是测出的物理内存极限。538 MiB时只有128维常驻；其收益同时包含route I/O消失与12 MiB record cache的效果，不能全部归因于PCA排序质量。','',
 '全部候选对照已在train100检查跨维度逐查询一致；960原方法与已有冻结结果检查一致。设备缓存不受控，各配置预读并预热100查询；共享环境不能宣称完全独占。非route页包括图、compact及residual；CSV另列full4和rerank页、cache命中和候选验证次数。被拒候选允许重访，所以route_checks是计算次数而非唯一节点数。','',
 '[全部CSV](measured_results.csv) · [0.95选参锁定](selection_lock.json) · [基线Recall选参规则](matched_protocol.json) · [基线Recall选参锁定](matched_selection_lock.json) · [协议](protocol.json) · [构建](build.json) · [账本](memory_ledger.json) · [最终审计](audit.json) · [之前的下界诊断](../gist_pca_budget/lower_bound_audit.md)']
 if (OUT/'figures/recall_qps_validation.png').exists()and(OUT/'figures/recall_qps_test.png').exists():
  i=lines.index('## validation结果')
  lines[i:i]=['## Recall–QPS 图','',
   '![固定M的validation Recall–QPS曲线](figures/recall_qps_validation.png)','',
   '本轮validation只测了width=60/100/180三个档位，尚未进行独立test的完整width扫描。M表示每次扩展后保留多少邻居进入验证，width控制图搜索候选池大小，两者不同。','',
   '图1：固定538 MiB预算，分别固定M=0/16/32/48，每条曲线按width=60/100/180连接实测点。圆点=width60，方点=width100，三角点=width180，蓝色曲线另标w值。横轴Recall@10，纵轴QPS采用对数刻度；每点为validation100单轮结果，无平滑、无置信区间。960维保留原方法，在四个面板重复作为参照；M仅作用于低维方法。','',
   '[PDF](figures/recall_qps_validation.pdf) · [可编辑SVG](figures/recall_qps_validation.svg)','',
   '![以width为横轴的Recall和QPS](figures/recall_qps_by_width.png)','',
   '补充图：直接以width为横轴，在固定M=16和32下分别查看Recall、QPS的变化。全部使用已有validation实测数据；未据三个点生成额外测量结果。','',
   '[PDF](figures/recall_qps_by_width.pdf) · [可编辑SVG](figures/recall_qps_by_width.svg)','',
   '![独立test Recall–QPS确认点](figures/recall_qps_test.png)','',
   '图2：两种validation锁定规则的独立test800确认点。QPS为两轮调和均值，误差棒为两轮最小–最大值，不是置信区间；部分范围小于点标记。纵轴为线性刻度。不同M/width的点不连线，validation和test不混合。竖虚线仅标示test Recall=0.95。','',
   '[PDF](figures/recall_qps_test.pdf) · [可编辑SVG](figures/recall_qps_test.svg) · [绘图协议及来源](figures/figure_spec.json) · [绘图检查](figures/qa.md)','']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n');print(len(rows),'accepted rows')
if __name__=='__main__':main()
