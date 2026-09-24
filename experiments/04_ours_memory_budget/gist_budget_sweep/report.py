"""Audit accepted runs and render separate pilot/full figures from measured data."""
import csv,json,statistics,os
from pathlib import Path
import run as sweep
OUT=sweep.OUT

def overlap_exclusion(measurement):
 path=OUT/'validation/cache_reuse_overlap.json'
 if not path.exists():return None
 window=json.loads(path.read_text())
 start=window['conservative_start_unix'];end=window.get('end_unix',float('inf'))
 if measurement.get('started_unix',0)<end and measurement.get('finished_unix',float('inf'))>start:
  return 'cache_reuse_validation_overlap'
 return None

def collect(split):
 rows=[]
 for path in sorted((OUT/split).glob('*/acceptance.json')):
  folder=path.parent;a=json.loads(path.read_text())
  assert a['passed'] and a['binary_sha256']==sweep.sha(sweep.BIN)
  for name,h in a['files'].items():assert sweep.sha(folder/name)==h,(folder,name)
  p=json.loads((folder/'memory_plan.json').read_text());m=json.loads((folder/'memory_measurement.json').read_text());st=json.loads((folder/'routing_stats.json').read_text())
  for r in json.loads((folder/'result.json').read_text())['summary_rows']:
   w=r['search_width'];cache=json.loads((folder/'memory_stats'/f'L{w}.json').read_text());rs=st.get(str(w),{})
   rows.append(dict(performance_exclusion=overlap_exclusion(m),split=split,run=folder.name,reference='reference' in folder.name,repeat=int(folder.name[-1]),budget_mib=p['budget_bytes']/sweep.MIB,mode=p['mode'],L=w,queries=r['query_count'],recall=r['recall'],qps=r['qps'],p95_ms=r['latency_p95_us']/1000,p99_ms=r['latency_p99_us']/1000,pages_per_query=r['sectors_4k_per_query'],routing_pages=rs.get('reads',0),routing_capacity_pages=p['routing_capacity_pages'],routing_peak_inflight=rs.get('peak_active',0),optional_cache_bytes=cache['cache_reserved_bytes'],static_records=cache['payload_nodes'],dynamic_capacity=(cache['dynamic_records'] or {}).get('capacity_nodes',0),VmPeak_mib=m['sampled_high_water_bytes']['VmPeak']/sweep.MIB,RSS_mib=m['kernel_wait4_peak_rss_bytes']/sweep.MIB))
 return rows

def plot(rows,split):
 os.environ.setdefault('MPLCONFIGDIR','/tmp/gist-budget-matplotlib')
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 import numpy as np
 plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],'font.size':8,'axes.labelsize':8,'axes.titlesize':9,'legend.fontsize':7,'xtick.labelsize':7,'ytick.labelsize':7,'pdf.fonttype':42,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
 groups={}
 for r in rows:
  if r.get('performance_exclusion'):continue
  groups.setdefault((r['reference'],r['budget_mib'],r['L']),[]).append(r)
 pairs={k:v for k,v in groups.items() if len(v)==2}
 if not pairs:return
 budgets=sorted({k[1] for k in pairs if not k[0]})
 fig=plt.figure(figsize=(7.086614,4.330709),layout='constrained')
 grid=fig.add_gridspec(2,2,height_ratios=[5,.7])
 axes=[fig.add_subplot(grid[0,0]),fig.add_subplot(grid[0,1])]
 status_ax=fig.add_subplot(grid[1,1],sharex=axes[1])
 notes=fig.add_subplot(grid[1,0]);notes.axis('off')
 colors=plt.get_cmap('viridis')(np.linspace(.12,.85,max(1,len(budgets))))
 for ref,budget,color in [(False,b,c) for b,c in zip(budgets,colors)]+[(True,2048,'#555555')]:
  keys=sorted([k for k in pairs if k[:2]==(ref,budget)],key=lambda k:k[2])
  if not keys:continue
  label='Resident reference' if ref else f'{budget:g} MiB'
  rec=[statistics.mean(r['recall'] for r in pairs[k]) for k in keys];q=[2/sum(1/r['qps'] for r in pairs[k]) for k in keys]
  low=[min(r['qps'] for r in pairs[k]) for k in keys];high=[max(r['qps'] for r in pairs[k]) for k in keys]
  axes[0].plot(rec,q,color=color,label=label,lw=1,marker='.' if split=='pilot' else None,ls='--' if ref else '-')
  axes[0].fill_between(rec,low,high,color=color,alpha=.12)
 for w,color in [(100,'#0072B2'),(300,'#D55E00'),(580,'#009E73')]:
  keys=[(False,b,w) for b in budgets if (False,b,w) in pairs]
  if not keys:continue
  q=np.array([2/sum(1/r['qps'] for r in pairs[k]) for k in keys]);lo=np.array([min(r['qps'] for r in pairs[k]) for k in keys]);hi=np.array([max(r['qps'] for r in pairs[k]) for k in keys])
  axes[1].errorbar([k[1] for k in keys],q,yerr=[q-lo,hi-q],color=color,marker='o',ms=3,lw=1,capsize=2,label=f'L{w}')
 axes[0].set(xlabel='Recall@10',ylabel='QPS',title='a  Recall–throughput');axes[1].set(ylabel='QPS',title='b  Fixed search widths')
 requested=json.loads((OUT/'calibration/lock.json').read_text())['budgets_mib']
 status_ax.set(xlim=(min(requested)-64,max(requested)+64),ylim=(0,1),yticks=[],xlabel='Process budget (MiB)')
 status_ax.set_xticks(sweep.BUDGETS)
 axes[1].tick_params(axis='x',labelbottom=False)
 status_ax.spines['left'].set_visible(False)
 status_ax.set_ylabel('Status',rotation=0,labelpad=14,fontsize=7)
 failed_budgets=[];rejected_budgets=[]
 for budget in requested:
  preflight=OUT/'preflight'/str(budget)/'status.json'
  if not preflight.exists() or json.loads(preflight.read_text())['status']!='admission_rejected':continue
  probe=OUT/'diagnostics'/f'low_budget_{budget}'/'probe.json'
  failed=probe.exists() and 'failure' in json.loads(probe.read_text())['status']
  (failed_budgets if failed else rejected_budgets).append(str(budget))
  status_ax.plot(budget,.5,marker='x' if failed else '^',color='#9E4B4B' if failed else '#777777',ms=5,linestyle='none')
 for y,marker,color,label in [(.8,'x','#9E4B4B','Probe failed: '+' / '.join(failed_budgets)+' MiB'),(.3,'^','#777777','Preflight rejected: '+' / '.join(rejected_budgets)+' MiB; no QPS data')]:
  notes.plot([0],[y],marker=marker,color=color,ms=5,linestyle='none',transform=notes.transAxes,clip_on=False)
  notes.text(.035,y,label,color=color,fontsize=7,va='center',transform=notes.transAxes)
 handles,labels=axes[0].get_legend_handles_labels()
 fig.legend(handles,labels,loc='outside lower center',ncol=4,frameon=False)
 axes[1].legend(frameon=False)
 for ax in axes:ax.grid(axis='y',alpha=.15)
 fig.suptitle(f'GIST · {split} · beam1 · 32 workers',fontsize=9)
 folder=OUT/'figures';folder.mkdir(exist_ok=True)
 fig.savefig(folder/f'{split}_budget_curves.png',dpi=300)
 fig.savefig(folder/f'{split}_budget_curves.pdf')
 fig.savefig(folder/f'{split}_budget_curves.svg')
 plt.close(fig)

def main():
 lock=json.loads((OUT/'calibration/lock.json').read_text()) if (OUT/'calibration/lock.json').exists() else None
 lines=['# GIST 分档内存实验','', '所有预算为整进程 RLIMIT_AS；VmPeak 是虚拟地址空间峰值，RSS 单独记录。失败档位不填 QPS=0，不作为曲线点。','']
 if (OUT/'validation/cache_reuse_overlap.json').exists():
  lines += ['缓存复用版本的编译/校准与部分 full 运行重叠。原始 QPS 保留并标记 performance_exclusion；重叠组不进入曲线或两轮性能汇总。时间范围见 validation/cache_reuse_overlap.json。','']
 if lock:
  lines += [f"统一估算基础开销（含全量查询额外空间、安全余量）：{lock['fixed_bytes']/sweep.MIB:.1f} MiB；完整 routing 准入阈值 T={lock['resident_threshold_bytes']/sweep.MIB:.3f} MiB。这是估算准入阈值，不是实测最低运行预算。",'', '| 预算 MiB | 预检模式 | routing 页槽 | 记录缓存上限 / 实配 MiB | 状态 |','|---:|---|---:|---:|---|']
  admission=[]
  scratch=json.loads((OUT/'calibration/paged/memory_plan.json').read_text())['paging_scratch_bytes']
  minimum=lock['fixed_bytes']+scratch+10*sweep.MIB+4352
  for b in lock['budgets_mib']:
   f=OUT/'preflight'/str(b);p=json.loads((f/'memory_plan.json').read_text()) if (f/'memory_plan.json').exists() else {};status=json.loads((f/'status.json').read_text())['status'] if (f/'status.json').exists() else 'pending'
   admission.append(dict(budget_mib=b,status=status,mode=p.get('mode'),minimum_paged_bytes=minimum,minimum_factors_bytes=minimum+20000000,resident_threshold_bytes=lock['resident_threshold_bytes'],minimum_paged_deficit_bytes=max(0,minimum-b*sweep.MIB),routing_capacity_pages=p.get('routing_capacity_pages'),optional_cache_bytes=p.get('optional_cache_bytes')))
   actual=[]
   for stage in ['pilot','full']:
    for run in (OUT/stage).glob(f'budget_{b}_r*'):
     if (run/'acceptance.json').exists():actual.append(json.loads((run/'memory_stats/L100.json').read_text())['cache_reserved_bytes'])
   assert len(set(actual))<=1,'cache allocations differ across repeats'
   actual_text=f'{actual[0]/sweep.MIB:.2f}' if actual else '—'
   lines.append(f"| {b} | {p.get('mode','—')} | {p.get('routing_capacity_pages','—')} | {p.get('optional_cache_bytes',0)/sweep.MIB:.2f} / {actual_text} | {status} |")
  sweep.dump(OUT/'admission_table.json',admission)
  lines += ['',f'估算最小全分页准入：{minimum/sweep.MIB:.3f} MiB；factors 常驻最低准入：{(minimum+20000000)/sweep.MIB:.3f} MiB。完整缺口见 admission_table.json；原生 fixed-memory 错误中的缺口仅对应基础项。']
 for split in ['pilot','full']:
  rows=collect(split);sweep.dump(OUT/f'{split}_summary.json',rows)
  if rows:
   with (OUT/f'{split}_summary.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
   plot(rows,split)
  complete=(OUT/split/'completed.json').exists()
  lines += ['',f"{split}：{'全部验收完成' if complete else '未完成'}，已有 {len(rows)} 个验收后的 run×L 记录。"]
  if (OUT/'figures'/f'{split}_budget_curves.png').exists():lines += ['',f'![{split} curves](figures/{split}_budget_curves.png)']
  paired=[]
  for budget in sorted({r['budget_mib'] for r in rows if not r['reference']}):
   rr=[r for r in rows if not r.get('performance_exclusion') and not r['reference'] and r['budget_mib']==budget and r['L']==100]
   if len(rr)!=2:continue
   paired.append(dict(budget_mib=budget,mode=rr[0]['mode'],qps=2/sum(1/r['qps'] for r in rr),recall=rr[0]['recall'],p95_ms=[r['p95_ms'] for r in rr],p99_ms=[r['p99_ms'] for r in rr],pages_per_query=statistics.mean(r['pages_per_query'] for r in rr),VmPeak_mib=max(r['VmPeak_mib'] for r in rr),RSS_mib=max(r['RSS_mib'] for r in rr)))
  sweep.dump(OUT/f'{split}_L100_pooled.json',paired)
  if paired:
   lines += ['', 'L100 两轮汇总（P95/P99 为两轮范围；VmPeak/RSS 为整个运行的峰值）：','', '| MiB | 模式 | Recall | QPS | P95 ms | P99 ms | 页/查询 | VmPeak MiB | RSS MiB |','|---:|---|---:|---:|---:|---:|---:|---:|---:|']
   for r in paired:
    lines.append(f"| {r['budget_mib']:g} | {r['mode']} | {r['recall']:.4f} | {r['qps']:.2f} | {min(r['p95_ms']):.1f}–{max(r['p95_ms']):.1f} | {min(r['p99_ms']):.1f}–{max(r['p99_ms']):.1f} | {r['pages_per_query']:.1f} | {r['VmPeak_mib']:.1f} | {r['RSS_mib']:.1f} |")
 observed=[r['budget_mib'] for stage in ['pilot','full'] for r in collect(stage) if not r['reference']]
 if observed:lines += ['',f'已完成单轮运行中的最低预算为 {min(observed):g} MiB；是否已通过全部两轮/完整 L 范围，以上方完成标记为准。未对连续预算寻找真正最小值。']
 lines += ['', 'QPS 曲线取两轮总查询数/总耗时（等样本数时为调和均值）；阴影/误差线为两轮最小–最大值，不表示置信区间。性能曲线只绘制已通过逐查询验收的完整两轮组；b图独立状态行保留失败及预检拒绝档位，不为其赋予QPS。P95/P99 按轮保留在 CSV，不混合平均百分位数。',
 '', '100 条预热和 O_DIRECT 顺序预读协议保持一致，不宣称冷设备缓存。预热取当前顺序前100条，pilot测量集因此全部预热，full只预热800条中的前100条；两类曲线分别报告。热点排名复用独立 validation profile；测试集不参与热点训练。',
 '', '低预算诊断位于 diagnostics/low_budget_*：L10、100 条验证查询、无预热、64 页 cache，仅用于实际可行性探测，不能代替完整 L 范围验收。',
 '', '状态见 state.json；原始日志、内存硬限制结果、逐查询 trace、物理 routing 读页及校验散列均保存在各运行目录。']
 (OUT/'report.md').write_text('\n'.join(lines)+'\n')
 print(OUT/'report.md')
if __name__=='__main__':main()
