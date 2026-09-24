"""Original method schematics. Illustrative IDs and codes are not measurements."""
from pathlib import Path
import os
os.environ.setdefault('MPLCONFIGDIR', '/tmp/ours_nature_mpl')
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, FancyBboxPatch, FancyArrowPatch, Polygon, Circle

OUT = Path(__file__).resolve().parent
FONT = Path(os.environ.get('OURS_FIGURE_FONT', '/tmp/ours_figure_NotoSansCJKsc-Regular.otf'))
font_manager.fontManager.addfont(FONT)
mpl.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Noto Sans CJK SC','DejaVu Sans'],
 'font.size':8,'svg.fonttype':'none','pdf.fonttype':42,'axes.linewidth':0.7,
 'axes.spines.top':False,'axes.spines.right':False,'legend.frameon':False})
C={'ink':'#263544','muted':'#62707A','line':'#AEB8C0','blue':'#426D9A',
   'bluep':'#E9F0F8','teal':'#287B77','tealp':'#E5F1EF','orange':'#B97843',
   'orangep':'#FAEFE3','grey':'#F1F3F5','white':'#FFFFFF'}
def canvas(h):
 f=plt.figure(figsize=(183/25.4,h/25.4),facecolor='white')
 a=f.add_axes([0,0,1,1]);a.set_xlim(0,183);a.set_ylim(h,0);a.axis('off')
 return f,a
def text(a,x,y,s,size=8,color='ink',ha='left',weight='normal'):
 return a.text(x,y,s,fontsize=size,color=C.get(color,color),ha=ha,va='center',weight=weight,linespacing=1.5)
def line(a,pts,color='line',lw=.8,dash=False):
 a.plot(*zip(*pts),color=C[color],lw=lw,ls='--' if dash else '-',solid_capstyle='round')
def arrow(a,pts,color='muted',lw=.9,dash=False):
 if len(pts)>2:line(a,pts[:-1],color,lw,dash)
 a.add_patch(FancyArrowPatch(pts[-2],pts[-1],arrowstyle='-|>',mutation_scale=8,
  color=C[color],lw=lw,linestyle='--' if dash else '-',shrinkA=0,shrinkB=0))
def box(a,x,y,w,h,s,fill='grey',edge='line',size=8):
 a.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0,rounding_size=1.4',
  facecolor=C[fill],edgecolor=C[edge],lw=.7))
 text(a,x+w/2,y+h/2,s,size,ha='center')
def label(a,x,y,letter,s):
 text(a,x,y,letter,9,weight='bold'); text(a,x+5,y,s,9,weight='bold')
def cells(a,x,y,values,w=6,h=7,fill='bluep',highlight=()):
 for i,v in enumerate(values):
  fc='blue' if i in highlight else fill
  a.add_patch(Rectangle((x+i*w,y),w,h,fc=C[fc],ec='white',lw=.8))
  text(a,x+(i+.5)*w,y+h/2,str(v),7.5,'white' if i in highlight else 'ink',ha='center')
def save(f,name):
 f.canvas.draw()
 renderer=f.canvas.get_renderer()
 for a in f.axes:
  for t in a.texts:
   b=t.get_window_extent(renderer)
   assert b.x0>=-1 and b.y0>=-1 and b.x1<=f.bbox.x1+1 and b.y1<=f.bbox.y1+1,t.get_text()
 f.savefig(OUT/(name+'.svg'))
 f.savefig(OUT/(name+'.pdf'))
 f.savefig(OUT/(name+'.png'),dpi=600)
 f.savefig(OUT/(name+'.tiff'),dpi=600,pil_kwargs={'compression':'tiff_lzw'})
 plt.close(f)

# Contract: explain one encoding and its residency, not a measured compression ratio.
f,a=canvas(120)
label(a,6,7,'a','一份主码，逐级补充信息')
box(a,7,17,31,15,'原向量 x\n减质心、保留模长',size=7.5)
arrow(a,[(39,24.5),(47,24.5)])
box(a,48,17,38,15,'补零 + Hadamard\n归一化方向 u',size=7.5)
arrow(a,[(87,24.5),(96,24.5)])
text(a,99,19,'4-bit 主码 k',8.5,weight='bold')
cells(a,99,24,[1,0,1,1],w=9,h=8,highlight=(0,))
text(a,143,28,'单坐标示例',7.5,'muted')
line(a,[(103.5,33),(103.5,38),(98,38)],'blue')
text(a,97,39,'最高位 b',7.5,'blue',ha='right')
line(a,[(121.5,33),(121.5,38),(128,38)],'muted')
text(a,129,39,'低 3 位 ℓ',7.5,'muted')
text(a,13,50,r'主码近似 $\widetilde{z}$ = λh',8,'blue')
arrow(a,[(49,50),(59,50)])
text(a,63,50,r'剩余误差 e = z − $\widetilde{z}$',8)
arrow(a,[(104,50),(114,50)])
box(a,117,44,59,12,'每 16 维 → residual4 + 尺度',fill='orangep',edge='orange',size=7.5)
line(a,[(6,61),(177,61)])
label(a,6,68,'b','同一编码的内存与磁盘分工')
# Open swim lanes make residency the primary visual structure.
text(a,8,83,'内存',8.5,'blue',weight='bold')
text(a,8,102,'磁盘',8.5,'orange',weight='bold')
line(a,[(28,75),(28,110)])
box(a,34,76,55,13,'DB1 位面 + 筛选因子','bluep','blue')
text(a,97,82.5,'读 payload 前筛选',8,'blue')
arrow(a,[(61.5,90),(61.5,95)],'blue',dash=True)
box(a,34,97,84,13,'完整 4-bit 主码  |  residual4 + 尺度','orangep','orange',7.5)
box(a,130,97,46,13,'Vamana 邻接表','grey','line')
text(a,151,82.5,'对称 4-bit 建图',7.5,'muted',ha='center')
arrow(a,[(153,87),(153,95)],'muted')
text(a,93,92.5,'最高位在两处保存',7,'muted')
save(f,'fig01_encoding_storage')

# Contract: gate governs payload requests; preserve invalid-estimate fallback and loop.
f,a=canvas(145)
label(a,6,7,'a','查询主循环：先筛选，再为幸存候选读盘')
text(a,33,17,'内存计算',8,'blue',ha='center',weight='bold')
text(a,107,17,'磁盘访问 / 完整打分',8,'orange',ha='center',weight='bold')
line(a,[(69,20),(69,113)],dash=True)
box(a,8,24,50,14,'query 准备\n保留浮点 y，生成 INT8',fill='bluep',edge='blue',size=7.5)
arrow(a,[(58,31),(81,31)])
box(a,81,24,59,14,'从节点 0 初始化候选池\n起点用浮点 query 打分',size=7.5)
arrow(a,[(110.5,38),(110.5,46)])
box(a,81,46,59,14,'扩展未展开节点\n读图页，发现新邻居',fill='orangep',edge='orange',size=7.5)
arrow(a,[(81,53),(58,53)])
box(a,8,46,50,14,'标记 visited\nDB1 × INT8 → LB、s',fill='bluep',edge='blue',size=7.5)
arrow(a,[(33,60),(33,68)])
a.add_patch(Polygon([(33,68),(58,79),(33,90),(8,79)],fc=C['bluep'],ec=C['blue'],lw=.8))
text(a,33,79,'池满、valid\n且 LB > τ？',7.5,ha='center')
arrow(a,[(33,90),(33,99)],'orange');text(a,36,94,'是',7,'orange')
text(a,33,104,'丢弃候选',8,'orange',ha='center',weight='bold')
text(a,33,111,'不请求该候选 payload',7,'muted',ha='center')
arrow(a,[(58,79),(81,79)],'blue');text(a,70,74,'否',7,'blue',ha='center')
box(a,81,72,59,14,'幸存 ID → 页合并 / 缓存\n读取缺失 payload 页',fill='orangep',edge='orange',size=7.5)
arrow(a,[(110.5,86),(110.5,94)])
box(a,81,94,59,16,'4-bit 距离 → 更新池\nvalid：复用 s；否则浮点重算',fill='bluep',edge='blue',size=7.1)
arrow(a,[(140,102),(165,102),(165,53),(140,53)],'muted')
text(a,169,77,'继续\n扩展',7.5,'muted',ha='center')
arrow(a,[(110.5,110),(110.5,122)],'teal');text(a,115,116,'无未展开节点',7,'teal')
box(a,46,123,120,14,'池内候选：浮点 query 重算 + residual4 修正 → top-k',fill='tealp',edge='teal',size=7.5)
save(f,'fig02_query_flow')

# Contract: fixed teaching example, three records/page; no empirical speedup claim.
f,a=canvas(118)
label(a,6,7,'a','候选筛选发生在记录层，I/O 发生在页层')
text(a,7,17,'教学示例：每个 4 KiB 页容纳 3 条记录；幸存 ID 为 0、1、4。',7.5,'muted')
text(a,8,31,'候选',8,weight='bold')
for x,v in [(36,0),(57,1),(78,2),(135.5,4)]:
 a.add_patch(Circle((x,31),5,fc=C['bluep'] if v!=2 else C['grey'],ec=C['blue'] if v!=2 else C['line'],lw=.8))
 text(a,x,31,str(v),8,ha='center')
line(a,[(74,27),(82,35)],'orange',1.1)
text(a,88,31,'筛掉',7,'orange')
arrow(a,[(36,36),(36,46)],'blue');arrow(a,[(57,36),(57,46)],'blue')
arrow(a,[(135.5,36),(135.5,46)],'blue')
text(a,8,52,'磁盘',8,weight='bold')
cells(a,27,47,[0,1,2],w=21,h=12,highlight=(0,1))
cells(a,104,47,[3,4,5],w=21,h=12,highlight=(1,))
text(a,58.5,65,'页 0 · 4 KiB',7.5,ha='center');text(a,135.5,65,'页 1 · 4 KiB',7.5,ha='center')
text(a,91.5,76,'ID 2 被筛掉，页 0 仍需读取；3 个记录请求只需 2 个唯一页。',7.5,'muted',ha='center')
line(a,[(6,83),(177,83)])
label(a,6,90,'b','同一 query 内，重排复用已经读过的页')
box(a,8,99,44,12,'重排 ID：1、4','bluep','blue')
arrow(a,[(53,105),(66,105)],'teal')
box(a,67,99,52,12,'页 0、1 仍在 LRU 中','tealp','teal',7.5)
arrow(a,[(120,105),(132,105)],'teal')
text(a,135,105,'新增读页 = 0',8,'teal',weight='bold')
save(f,'fig03_page_reuse')
print('Rendered 3 schematics: SVG, PDF, 600-dpi PNG and TIFF.')
