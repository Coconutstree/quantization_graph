"""Educational schematic: subset LUT, batched candidates, shared query preparation.
No measured performance data. Python-only drawing; white, editable vector exports.
"""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.font_manager import fontManager
P=Path(__file__).resolve().parent
fontManager.addfont(str(P.parent/'sources/diagram_font.otf'))
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Noto Sans CJK SC','Arial','DejaVu Sans'],'font.size':11,'svg.fonttype':'none','pdf.fonttype':42,'axes.linewidth':.8})
fig=plt.figure(figsize=(12,11),facecolor='white');ax=fig.add_axes([0,0,1,1]);ax.set(xlim=(0,120),ylim=(0,110));ax.axis('off')
blue='#e4eef7';green='#e7f1eb';orange='#fff0df';ink='#20384b'
def text(x,y,s,size=11,**kw):return ax.text(x,y,s,fontsize=size,color=ink,va='top',**kw)
def box(x,y,w,h,s,fill=blue,size=11):
 ax.add_patch(FancyBboxPatch((x,y-h),w,h,boxstyle='round,pad=0.3,rounding_size=1',facecolor=fill,edgecolor='#718597',linewidth=.8));ax.text(x+w/2,y-h/2,s,ha='center',va='center',fontsize=size,color=ink)
def arrow(x,y,xx,yy):ax.annotate('',xy=(xx,yy),xytext=(x,y),arrowprops={'arrowstyle':'->','color':'#657d91','lw':1.4})
text(5,107,'FastScan：先列出 16 种和，再一起给 32 个邻居查表',17,weight='bold')
text(5,101,'教学算例；数字用于解释计算，不是实验测量值。',10)
text(5,96,'a   四个坐标，只有 16 种选法',13,weight='bold')
box(5,90,37,10,'query 的这一组整数坐标\nQ = [2, 5, 1, 3]')
text(5,76,'bit = 1：把对应坐标加进来\nbit = 0：跳过这个坐标',11)
box(5,63,37,10,'邻居这组 bit：1010\n查到 2 + 1 = 3',orange)
arrow(43,85,49,85)
# Full table, displayed as two groups of 8 rows.
for col in range(2):
 x=51+col*32
 box(x,90,29,6,'四个 bit    索引    表内的和',green,10)
 for row in range(8):
  k=col*8+row;b=f'{k:04b}';value=sum(v for bit,v in zip(b,[2,5,1,3]) if bit=='1')
  y=82-row*3.4
  if k==10:ax.add_patch(FancyBboxPatch((x,y-2.8),29,3.2,boxstyle='round,pad=0.1',facecolor=orange,edgecolor='none'))
  text(x+2,y,f'{b}          {k:2d}          {value:2d}',10)
text(5,49,'b   同一组坐标的表，供 32 个邻居一起使用',13,weight='bold')
box(5,43,25,12,'该坐标组的\n16 项 query 表',green)
arrow(31,37,36,37)
box(38,43,76,6,'邻居 1         邻居 2         邻居 3         …         邻居 32',blue,11)
box(38,35,76,6,'1010 → 3      0101 → 8      1111 → 11      …       各查自己的索引',orange,10)
text(5,25,'依次处理其余坐标组，给每个邻居各自累加；最后做符号还原和边系数修正。',11)
text(5,20,'c   换一个展开节点，query 表仍然不变',13,weight='bold')
box(5,14,34,9,'每个 query 一次\n旋转、量化、建 LUT',green,10)
box(43,14,34,9,'每条边离线保存\nbit 码、尺度、中心修正',blue,10)
box(81,14,33,9,'展开节点时组合\n查表结果 + 当前节点真距离',orange,10)
text(40,11,'+',15);arrow(77.5,9.5,80,9.5)
fig.savefig(P/'fastscan_explained.png',dpi=300,facecolor='white')
fig.savefig(P/'fastscan_explained.svg',facecolor='white')
fig.savefig(P/'fastscan_explained.pdf',facecolor='white')
plt.close(fig)
