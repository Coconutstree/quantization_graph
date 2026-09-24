"""GIST memory policies: measured Recall@10 vs throughput, all 360 observations.
Contract: quantitative comparison; full range plus high-recall detail.
Single run per configuration: no estimated uncertainty or smoothed/interpolated data.
Python backend; 183 x 123 mm; editable vector text; PNG preview.
"""
import csv
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', '/tmp/ours-memory-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter, MultipleLocator
from protocol import OUT, MODES, WIDTHS
from run import verify_acceptance

STYLE = {
 'baseline': ('Baseline', '#565656', '-', 'o'),
 'pages': ('Page Cache', '#0072B2', '-', 's'),
 'hot_graph': ('Hot Adjacency', '#9B7A42', ':', 'x'),
 'hot_payload': ('Hot Records', '#009E73', '-', 'v'),
 'hybrid': ('Hybrid Cache', '#D55E00', '-', 'D'),
 'nav': ('Navigation', '#565656', '--', '+'),
 'nav_pages': ('Navigation + Page', '#0072B2', '--', '^'),
 'nav_hybrid': ('Navigation + Hybrid', '#D55E00', '--', 'P'),
 'full_payload': ('Full Records', '#8F63A6', '-', '*'),
}

def main():
    folder=OUT/'figures';folder.mkdir(exist_ok=True)
    rows=[]
    for mode in MODES:
        verify_acceptance(OUT/mode)
        values=json.loads((OUT/mode/'result.json').read_text())['summary_rows']
        assert [r['search_width'] for r in values]==list(WIDTHS)
        for r in values:
            assert 0<=r['recall']<=1 and r['qps']>0
            rows.append({'mode':mode,'L':r['search_width'],'recall_at_10':r['recall'],'qps':r['qps']})
    assert len(rows)==360
    with (folder/'recall_qps_source.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],
        'font.size':8,'axes.labelsize':9,'axes.titlesize':9,'legend.fontsize':7.5,
        'xtick.labelsize':8,'ytick.labelsize':8,'svg.fonttype':'none','pdf.fonttype':42,
        'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':0.7})
    fig,axes=plt.subplots(1,2,figsize=(183/25.4,123/25.4),gridspec_kw={'width_ratios':[1.15,1]})
    fig.subplots_adjust(left=.095,right=.98,bottom=.32,top=.83,wspace=.30)
    for mode in MODES:
        part=[r for r in rows if r['mode']==mode]
        label,color,ls,marker=STYLE[mode]
        for ax in axes:
            ax.plot([r['recall_at_10']for r in part],[r['qps']for r in part],
                label=label,color=color,linestyle=ls,linewidth=1.3,
                marker=marker,markersize=3.3,markerfacecolor='none',markeredgewidth=.7,
                markevery=3)
    axes[0].set_yscale('log')
    axes[0].set_xlim(.60,.991)
    axes[0].set_ylim(2,1200)
    axes[0].set_yticks([3,10,30,100,300,1000])
    axes[0].yaxis.set_major_formatter(ScalarFormatter())
    axes[0].set_title('a  Full Range (Log QPS)',loc='left',fontweight='bold',pad=10)
    axes[1].set_xlim(.95,.988)
    axes[1].set_ylim(0,90)
    axes[1].set_xticks([.95,.96,.97,.98])
    axes[1].yaxis.set_major_locator(MultipleLocator(20))
    axes[1].set_title('b  High Recall (Linear QPS)',loc='left',fontweight='bold',pad=10)
    for ax in axes:
        ax.set_xlabel('Recall@10')
        ax.set_ylabel('QPS (queries/s)')
        ax.grid(axis='both',which='major',color='#e5e5e5',linewidth=.5)
        ax.set_axisbelow(True)
    if (OUT/'performance_review.json').exists():
        fig.text(.5,.875,'Baseline slowdown unresolved: performance ranking is provisional.',
                 ha='center',fontsize=8,color='#a12727')
    fig.suptitle('GIST · 1M × 960 · 2 GiB · 32 Workers',y=.95,fontsize=11,fontweight='bold')
    handles,labels=axes[0].get_legend_handles_labels()
    # Group navigation pairs together in the column-major legend.
    order=[0,2,5,1,6,3,4,7,8]
    fig.legend([handles[i]for i in order],[labels[i]for i in order],loc='lower center',
        bbox_to_anchor=(.53,.095),ncol=3,frameon=False,handlelength=2.8,columnspacing=1.6,labelspacing=.8)
    fig.text(.5,.045,'40 measured L settings per curve; one run per setting; no smoothing or error bars.',
        ha='center',fontsize=7,color='#555555')
    fig.savefig(folder/'gist_recall_qps.png',dpi=300,facecolor='white')
    fig.savefig(folder/'gist_recall_qps.pdf',facecolor='white')
    fig.savefig(folder/'gist_recall_qps.svg',facecolor='white')
    plt.close(fig)
    (folder/'figure_notes.md').write_text('''# Recall–QPS 曲线说明

- 左图：全部9个方案、360个实测点，纵轴为对数坐标。右图：Recall≥0.95区域，纵轴为线性坐标，方便比较高召回下的吞吐。
- 横轴Recall@10，纵轴QPS，越靠右上越好。按L递增连接所有原始点；未平滑、未插值、未取最优包络。标记间隔仅为减少重叠，曲线仍包含全部40个点。
- 同色实线/虚线用于对应缓存方案及加入导航后的变体。Baseline=基线；Page Cache=page缓存；Hot Adjacency=热点邻接表；Hot Records=热点精细记录；Hybrid Cache=混合缓存；Navigation=导航小图；Full Records=全库精细记录。
- 每档800条测试查询、预热100条；热点使用独立验证查询。每配置只运行一次，无法估计运行间波动，因此不画误差条。导航方案可能改变召回。
- 源数据为同目录recall_qps_source.csv，读取原始验收结果，9×40=360行，无数据剔除。右图仅限坐标窗口，左图保留完整范围。
- PNG为300 dpi预览，PDF/SVG保留矢量和文本。画布183×123 mm。
''')
    print(folder/'gist_recall_qps.png')

if __name__=='__main__': main()
