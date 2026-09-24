from pathlib import Path
from html import escape as E
import xml.etree.ElementTree as ET,json
P=Path(__file__).resolve().parent;out=P.parent
xml=[];md=[]
def p(t,bold=False):xml.append('<p>'+('<b>'+E(t)+'</b>' if bold else E(t))+'</p>');md.append('**'+t+'**' if bold else t)
def eq(s):xml.append('<p><latex>'+E(s)+'</latex></p>');md.append('$$\n'+s+'\n$$')
p('FastScan 做的事可以分成三步：query 先把可能用到的坐标和算好，邻居拿自己的 bit 码查表，再让 SIMD 同时处理一批邻居。先看一个只有四个坐标的例子。')
p('1．先把四个坐标的 16 种选法列成表',True)
p('假设 query 已旋转并量化，这一组四个整数坐标为 Q=[2,5,1,3]。一个邻居在这四个坐标上的符号用四个 bit 表示：1 对应正号，0 对应负号。为了方便查表，先只加 bit 为 1 的坐标；负号稍后统一还原。')
p('四个 bit 总共只有 16 种组合，所以提前算一张 16 项的小表，叫 LUT（查找表）。例如 0000 对应 0；1010 对应第 1、3 个坐标的和 2+1=3；1111 对应四个坐标的和 11。这里按左到右对应四个坐标，1010 作为二进制索引就是 10。')
p('有了这张表，遇到 bit 为 1010 的邻居，直接取 LUT[10]=3。不同邻居虽然 bit 不同，但查的是同一张表，因为 query 没变。每个四维坐标组都有自己的表；不是一张 16 项表覆盖整个向量。')
p('2．同一张表，同时给 32 个邻居使用',True)
p('把 32 个邻居在同一坐标组上的四 bit 索引放在适合 SIMD 的连续布局中。SIMD 可以在多条并行通道上执行查表：邻居 1 查 1010，邻居 2 查 0101，邻居 3 查 1111……得到的分别是 3、8、11……。随后换到下一个坐标组，继续给每个邻居各自累加。32 是一个批次的邻居数，不是一次算完 32 个完整距离的单条指令。')
p('为什么快：小表能放在 SIMD 寄存器中，多个邻居并行查表；码按批排列，避免逐邻居到分散位置取码。所有坐标组处理完，才得到每个邻居的总查表和 S。')
p('3．查表和还不是距离：先还原正负号，再乘边系数',True)
eq(r'\sum_j Q_j(2b_j-1)=2\underbrace{\sum_{j:b_j=1}Q_j}_{S}-\sum_jQ_j.')
p('在刚才的四维例子中，1010 表示正、负、正、负。先查得 S=3，再算 2×3−11=−5，等于直接计算 2−5+1−3。接着还要恢复 query 的量化尺度与偏移，结合该边保存的 RaBitQ 系数，才能得到邻居距离的估计值。因此 LUT 返回的 3 不是最终距离。')
xml.append('<img path="@./docs/analysis/disk_method_series_20260914/fastscan_explainer/fastscan_explained.png" width="1400" caption="FastScan 的三个层次：四 bit 查表、32 邻居批量累加，以及 query 表的复用。数字为教学算例。"/>');md.append('![FastScan：查表、批量累加与 query 表复用](fastscan_explainer/fastscan_explained.png)')
p('4．为什么换一个展开节点，不用重新建 query 表？',True)
p('查询 q 在整次搜索中不变，因此 Tq 也不变。变化的是当前展开节点 u，以及 u 到各邻居的边码和系数。把理论式拆开就能看清两者的分工：')
eq(r'(Tq-Tx_u)^\top t_{uv}=(Tq)^\top t_{uv}-(Tx_u)^\top t_{uv}.')
p('第一项需要 query，由同一套 query LUT 配合不同边码计算；第二项只包含数据库节点和边的信息，可以在建索引时算好并存进边系数。于是每个 query 只需旋转、量化和建表一次。展开 u 时，再计算 q 到 u 的精确距离，与查表结果、边系数组合，得到各邻居的距离估计。')
p('记住这四个时机即可：建索引时保存边码和系数；query 开始时建 LUT；展开节点时批量查表；累加和修正后得到邻居距离。')
(P/'section.xml').write_text('\n'.join(xml));(P/'section.md').write_text('\n\n'.join(md)+'\n')
# Replace only the two former paragraphs in the local section, retaining all other content.
pth=out/'03_symphonyqg.xml';s=pth.read_text();a=s.index('<h2>四、步骤 3：');a=s.index('</h2>',a)+5;b=s.index('<h2>五、步骤 4：',a);s=s[:a]+'\n'+'\n'.join(xml)+'\n'+s[b:];pth.write_text(s)
pth=out/'03_symphonyqg.md';s=pth.read_text();a=s.index('### 四、步骤 3：');a=s.index('\n',a);b=s.index('### 五、步骤 4：',a);pth.write_text(s[:a]+'\n\n'+'\n\n'.join(md)+'\n\n'+s[b:])
# Keep the four-baseline generator aligned with the edited section.
g=out/'revise_baselines.py';s=g.read_text();a=s.index("d.p('把四个符号 bit");b=s.index("d.sub('五、步骤 4：用量化搜索",a)
s=s[:a]+"d.xml.append((P/'fastscan_explainer/section.xml').read_text())\nd.md.append((P/'fastscan_explainer/section.md').read_text())\n"+s[b:];g.write_text(s)
(P/'figure_contract.md').write_text('结论：FastScan 把重复的坐标求和变成 query 小表查找，并对 32 个邻居批量累加；query 表在不同展开节点间复用。\n图式：schematic-led composite；a 为主图，展示完整 16 项教学表；b 展示批处理；c 展示数据准备分工。\nPython/matplotlib；白底，PNG 300 dpi、SVG 可编辑文本、PDF；面向飞书阅读，12×11 英寸画布不套用期刊栏宽。\n教学输入 Q=[2,5,1,3]，16 个表项均按 bit 选择和生成；无实验数据和统计推断。\nPDF 字号下限 10 pt；各面板已目视检查。\n')
print('Updated local section and reproducible generator.')
