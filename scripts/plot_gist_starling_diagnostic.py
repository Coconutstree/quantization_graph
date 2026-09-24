#!/usr/bin/env python3
"""Plot the original five 05C methods on a shared, zero-based linear QPS axis.

Figure contract: one quantitative comparison panel, raw Recall@10 versus QPS,
historical diagnostic scope, Python only, 183 x 145 mm with editable vectors.
Starling is excluded at the user's request; existing filenames are retained.
"""
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT/'results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair/test_L_400_w_32'
OUT = RUN/'plots/gist_starling_diagnostic_20260917'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    specs = [
        ('Ours-Disk', 'Ours-Disk/result.json', 'Ours', '#0072B2', 'o', '-'),
        ('SymphonyQG-DiskPort', 'SymphonyQG-DiskPort/result.json',
         'SymphonyQG', '#8B5BA6', 'D', '-'),
        ('OG-LVQ-DiskPort', 'OG-LVQ-DiskPort/result.json',
         'OG-LVQ', '#009E73', 'v', '-'),
        ('Glass-NSG-DiskPort', 'Glass-NSG-DiskPort/result.json',
         'Glass-NSG', '#B38B22', 'P', '-'),
        ('DiskANN-PQ-Disk','DiskANN-PQ-Disk/result.json','DiskANN-PQ', '#666666','^','-'),
    ]
    run_manifest = json.loads((RUN/'run.json').read_text())
    assert [s[0] for s in specs] == run_manifest['methods']
    artifacts = [(spec,json.loads((RUN/'gist'/spec[1]).read_text())) for spec in specs]
    assert all(d['status']=='done' and d['workers']==32 for _,d in artifacts)
    assert len({d['query_order_sha256'] for _,d in artifacts}) == 1
    query_root = ROOT/'results/archive/legacy_layout_20260918/disk_environment/03_system_fair/gist/csv/_query_splits'
    pair = hashlib.sha256()
    for name in ['test_query.fvecs','test_gt.ivecs']:
        path = query_root/name
        pair.update(name.encode()); pair.update(bytes.fromhex(sha(path)))
    assert all(d['query_split_sha256']==pair.hexdigest() for _,d in artifacts)
    provenance = {'purpose':'Original 05C five-method observations; historical diagnostic',
                  'backend':'Python/matplotlib','archetype':'quantitative single-panel comparison',
                  'claim':'Display the original five 05C methods with all measured Recall@10–QPS points on one linear scale.',
                  'adaptation':'Remove Starling at user request; change the common QPS axis from logarithmic to linear, starting at zero.',
                  'field_mapping':{'summary_rows.recall':'x: Recall@10, fraction',
                                   'summary_rows.qps':'y: queries/second, linear scale from zero',
                                   'summary_rows.search_width':'connection order; no interpolation'},
                  'original_05c_methods':run_manifest['methods'],
                  'run_manifest_sha256':sha(RUN/'run.json'),
                  'query_pair_sha256':pair.hexdigest(),'query_order_sha256':artifacts[0][1]['query_order_sha256'],
                  'sources':[], 'excluded_methods':{
                      'Starling':'Removed at user request; its 40 observations remain in the source artifact and previous_six_methods_log archive.',
                      'AiSAQ':'Outside the requested original-five set; no completed GIST measurement'},
                  'acceptance_notes':{
                      'all':'Historical result acceptance is unchanged; warm-up/cache and timing evidence differ.',
                      'OG-LVQ':'Official algorithm parity remains unaccepted; displayed as a historical disk-port measurement.'},
                  'observations':'All summary rows for the five retained methods; no envelope, interpolation or normalization.',
                  'uncertainty':'One recorded run per configuration; no error bars or statistical tests.',
                  'export':'183 x 145 mm; editable SVG/PDF text; 600 dpi PNG preview'}
    data=[]
    for spec,artifact in artifacts:
        path=RUN/'gist'/spec[1]
        rows=artifact['summary_rows']
        provenance['sources'].append({'method':spec[0],'path':str(path.relative_to(ROOT)),
                                      'sha256':sha(path),'input_rows':len(rows),'plotted_rows':len(rows),
                                      'address_space_cap_gib':artifact['search_dram_budget_gib'],
                                      'formal_ready':artifact.get('formal_ready'),
                                      'throughput_comparable':artifact.get('throughput_comparable'),
                                      'implementation_fingerprint':artifact.get('implementation_fingerprint')})
        for row in rows:
            assert (math.isfinite(row['recall']) and 0<=row['recall']<=1
                    and math.isfinite(row['qps']) and row['qps']>0 and row['query_count']==800)
            data.append(dict(method=spec[0],search_width=row['search_width'],recall_at_10=row['recall'],
                             qps=row['qps'],workers=32,query_count=800,
                             address_space_cap_gib=artifact['search_dram_budget_gib']))
    y_max = math.ceil(max(r['qps'] for r in data) * 1.05 / 50) * 50
    provenance['axes'] = {'xlim':[0.1,1.0], 'yscale':'linear', 'ylim':[0,y_max],
                          'shared_scale_for_all_methods':True}
    (OUT/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    with (OUT/'source_data.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],
                         'font.size':9,'axes.labelsize':10,
                         'axes.spines.top':False,'axes.spines.right':False,
                         'axes.linewidth':0.8,'pdf.fonttype':42,'svg.fonttype':'none',
                         'legend.frameon':False,'savefig.facecolor':'white'})
    fig,ax=plt.subplots(figsize=(183/25.4,145/25.4))
    fig.subplots_adjust(left=0.12,right=0.97,bottom=0.20,top=0.76)
    for spec,artifact in artifacts:
        rows=sorted(artifact['summary_rows'],key=lambda x:x['search_width'])
        ax.plot([r['recall'] for r in rows],[r['qps'] for r in rows],label=spec[2],color=spec[3],
                marker=spec[4],linestyle=spec[5],markersize=2.8,
                linewidth=1.7 if spec[0]=='Ours-Disk' else 1.3,markeredgewidth=0.5)
    ax.set_yscale('linear')
    # All methods share a zero baseline and the same untransformed QPS scale.
    ax.set_xlim(0.1,1.0);ax.set_ylim(0,y_max)
    assert all(0.1<=r['recall_at_10']<=1 and 0<=r['qps']<=y_max for r in data)
    ax.set_xticks([0.1,0.2,0.4,0.6,0.8,1.0]);ax.set_yticks(range(0,int(y_max)+1,100))
    ax.yaxis.set_major_formatter(ScalarFormatter())
    ax.set_xlabel('Recall@10');ax.set_ylabel('Throughput (queries/s)')
    ax.grid(axis='y',which='major',color='#DDDDDD',linewidth=0.5)
    ax.set_axisbelow(True)
    handles, labels = ax.get_legend_handles_labels()
    # Matplotlib fills columns first: reorder handles for the intended row order.
    order = [0,3,1,4,2]
    fig.legend([handles[i] for i in order],[labels[i] for i in order],
               loc='upper left',bbox_to_anchor=(0.12,0.872),ncol=3,fontsize=9,
               handlelength=2.5,columnspacing=2.6,borderaxespad=0)
    fig.text(0.12,0.955,'GIST | Original 05C methods',fontsize=12,fontweight='bold')
    fig.text(0.12,0.911,'32 query workers | 800 test queries | One run per configuration',fontsize=9)
    fig.text(0.12,0.105,'Historical diagnostic data; warm-up/cache protocols differ.',fontsize=8)
    fig.text(0.12,0.067,'Linear QPS axis from zero; all 227 points from the five methods retained.',fontsize=8)
    fig.text(0.12,0.029,'One common axis; no normalization or axis breaks.',fontsize=8)
    fig.savefig(OUT/'gist_recall_qps_starling_diagnostic.png',dpi=600)
    fig.savefig(OUT/'gist_recall_qps_starling_diagnostic.pdf')
    fig.savefig(OUT/'gist_recall_qps_starling_diagnostic.svg')
    plt.close(fig)
    counts = {spec[0]:len(artifact['summary_rows']) for spec,artifact in artifacts}
    (OUT/'README.md').write_text('''# GIST Recall–QPS：原 05C 五方法，线性纵轴

按用户最新要求，先去掉 Starling，只保留原 run.json 的五种方法：
Ours、SymphonyQG、OG-LVQ、Glass-NSG、DiskANN-PQ。
纵轴改为从 0 开始的线性 QPS 刻度，五种方法共用同一纵轴，无断轴、归一化或双轴。
现有 PNG/PDF/SVG 文件名保留以便原链接继续打开更新后的图；文件名中的 Starling 是历史命名。

Counts: ''' + json.dumps(counts) + f'''; total={len(data)} observations.
Points are connected in increasing search-width order, without smoothing, extrapolation or Pareto filtering.
All methods use 800 test queries and 32 query workers. Query/ground-truth bytes and query-order hashes were checked.
One run per configuration: no error bars or significance claims.

Starling 的 40 行仅按用户要求从本图排除，原始实验记录未删除。
历史五方法保留各自已记录设置。历史 warm-up/cache、计时和原生等价验收缺口仍保留，
本图未将旧结果认证为新协议的正式实验；OG-LVQ 的官方等价验收仍未完成。
所有保留方法的 227 个点均原样绘制，坐标范围覆盖全部点，没有裁掉低 QPS 点。

Files: PNG preview, editable PDF/SVG, source_data.csv, and provenance.json.
Run `python3 scripts/plot_gist_starling_diagnostic.py` from the repository to reproduce.
Previous three-method plot and script: previous_three_methods/.
Previous six-method logarithmic plot and script: previous_six_methods_log/.

QA: all retained-method rows included; finite values and axis bounds checked; actual observations only.
Historical query_split_sha256 hashes for all five methods match the query+ground-truth bytes.
''')
    print(OUT)


if __name__=='__main__':
    main()
