from pathlib import Path
from html import escape as E
from PIL import Image, ImageDraw, ImageFont
import json, hashlib, shutil, re
ROOT=Path('/home/kai3/coco/quantization_graph')
OUT=ROOT/'docs/analysis/disk_method_series_20260914'
(OUT/'figures').mkdir(exist_ok=True)
FONT='/tmp/ours_figure_NotoSansCJKsc-Regular.otf'
# Source font is already present from the earlier project explainer.
shutil.copyfile(FONT,OUT/'sources/diagram_font.otf')
FONT=str(OUT/'sources/diagram_font.otf')
def font(n):return ImageFont.truetype(FONT,n)
def diagram(name,title,items,note,storage=False):
 im=Image.new('RGB',(1500,820),'#f8fafc');d=ImageDraw.Draw(im)
 d.text((55,35),title,font=font(37),fill='#132638')
 d.text((55,93),'磁盘方法逐步拆解 · 机制示意（非性能数据）',font=font(23),fill='#536575')
 colors=[('#e1eef9','#285d85'),('#e4f1eb','#28634f'),('#fff0de','#985b20')]
 if storage:
  for i,(head,body) in enumerate(items):
   x=55+i*480; fill,edge=colors[i]
   d.rounded_rectangle((x,160,x+440,650),radius=20,fill=fill,outline=edge,width=2)
   d.text((x+24,186),head,font=font(31),fill=edge)
   for j,line in enumerate(body.split('\n')):d.text((x+24,262+j*57),line,font=font(25),fill='#203345')
 else:
  for i,(head,body) in enumerate(items):
   row=i//3; col=i%3 if row==0 else 2-i%3;x=55+col*480;y=165+row*255;fill,edge=colors[col]
   d.rounded_rectangle((x,y,x+440,y+194),radius=20,fill=fill,outline=edge,width=2)
   d.text((x+20,y+19),str(i+1)+'  '+head,font=font(29),fill=edge)
   for j,line in enumerate(body.split('\n')):d.text((x+20,y+77+j*43),line,font=font(24),fill='#203345')
   if i in (0,1):
    d.line((x+442,y+95,x+476,y+95),fill='#637689',width=4);d.polygon([(x+476,y+95),(x+464,y+87),(x+464,y+103)],fill='#637689')
   elif i==2:
    d.line((x+220,y+196,x+220,y+250),fill='#637689',width=4);d.polygon([(x+220,y+250),(x+212,y+238),(x+228,y+238)],fill='#637689')
   elif i in (3,4):
    d.line((x-3,y+95,x-36,y+95),fill='#637689',width=4);d.polygon([(x-36,y+95),(x-24,y+87),(x-24,y+103)],fill='#637689')
 for j,line in enumerate(note.split('\n')):d.text((55,710+j*37),line,font=font(24),fill='#415466')
 im.save(OUT/'figures'/f'{name}.png');return f'figures/{name}.png'
class Doc:
 def __init__(self,key,title,scope):
  self.key=key;self.title=title;self.xml=[f'<title>{E(title)}</title>'];self.md=['# '+title];self.p(scope)
 def h(self,t):self.xml.append(f'<h1 seq="auto">{E(t)}</h1>');self.md.append('## '+t)
 def p(self,t):self.xml.append(f'<p>{E(t)}</p>');self.md.append(t)
 def eq(self,t):self.xml.append(f'<p><latex>{E(t)}</latex></p>');self.md.append('$$\n'+t+'\n$$')
 def table(self,heads,rows):
  self.xml.append('<table><thead><tr>'+''.join(f'<th><p>{E(str(c))}</p></th>' for c in heads)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td><p>{E(str(c))}</p></td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>')
  self.md.append('| '+' | '.join(heads)+' |\n| '+' | '.join(['---']*len(heads))+' |\n'+'\n'.join('| '+' | '.join(str(c).replace('|','∣') for c in row)+' |' for row in rows))
 def fig(self,label,items,note,storage=False):
  n=f'{self.key}_{"storage" if storage else "flow"}';p=diagram(n,label,items,note,storage)
  self.xml.append(f'<img path="@./docs/analysis/disk_method_series_20260914/{p}" caption="{E(label)}" width="1400"/>');self.md.append(f'![{label}]({p})')
 def source(self,label,path,url=None):
  if url:self.xml.append(f'<p>{E(label)}：<a href="{E(url)}">{E(path)}</a></p>');self.md.append(f'{label}：[{path}]({url})')
  else:self.p(label+'：'+path)
 def save(self):
  (OUT/(self.key+'.xml')).write_text('\n'.join(self.xml));(OUT/(self.key+'.md')).write_text('\n\n'.join(self.md)+'\n');docs.append({'key':self.key,'title':self.title})
docs=[]
SCOPE='面向熟悉向量检索、希望逐步理解编码与磁盘查询的研究人员。按“符号—离线构建—在线查询—存储/I/O—误差—来源”阅读。核对日期 2026-09-14；代码以当前工作区为准，本次不重跑性能实验。'
# Ours keeps the detailed, checked mathematical derivation from the previous explainer.
old=ROOT/'docs/analysis/ours_disk_explainer_20260911'
md=(old/'ours_disk_method.md').read_text();xml=(old/'ours_disk_method.xml').read_text()
md=md.replace('以 2026-09-11 当前工作区代码为准。','原稿核对日期为 2026-09-11；本次于 2026-09-14 对磁盘查询与 locality 分支补充核对。')
xml=xml.replace('以 2026-09-11 当前工作区代码为准。','原稿核对日期为 2026-09-11；本次于 2026-09-14 对磁盘查询与 locality 分支补充核对。')
# Keep old source references explicitly historical; change the obsolete example path to existing artifact path later.
for f in ['fig01_encoding_storage','fig02_query_flow','fig03_page_reuse']:
 shutil.copyfile(old/'nature_figures'/f'{f}.png',OUT/'figures'/f'{f}.png')
 md=md.replace(f'nature_figures/{f}.png',f'figures/{f}.png')
 xml=xml.replace(f'docs/analysis/ours_disk_explainer_20260911/nature_figures/{f}.png',f'docs/analysis/disk_method_series_20260914/figures/{f}.png')
d=Doc('01_ours','Ours 磁盘版：DB1 筛选、ExRaBitQ4 与两种物理布局',SCOPE)
# Omit the historical single-row result: its former path was migrated.
start=md.index('## 十、');end=md.index('## 十一、',start)
md=md[:start]+'## 十、从字节账目核对实现\n\n以 d=1024 为教学算例：每向量 DB1 为 128 字节，常见 factor ABI 为 20 字节；普通 compact 为 529 字节，residual 为 648 字节。向量级账目之外，还需另计图、共享模型、所有 worker 工作区、页填充与缓存。本篇不引用已迁移路径下的历史性能行，也不把它与当前 test snapshot 混合。\n\n'+md[end:]
start=xml.index('<h1>十、');end=xml.index('<h1>十一、',start)
xml=xml[:start]+'<h1>十、从字节账目核对实现</h1>\n<p>以 d=1024 为教学算例：每向量 DB1 为 128 字节，常见 factor ABI 为 20 字节；普通 compact 为 529 字节，residual 为 648 字节。向量级账目之外，还需另计图、共享模型、所有 worker 工作区、页填充与缓存。本篇不引用已迁移路径下的历史性能行，也不把它与当前 test snapshot 混合。</p>\n'+xml[end:]
md=re.sub(r'^\| results/disk_environment/05_disk_system_fair/single_test_w32_20260911/.*?\n','',md,flags=re.M)
xml=re.sub(r'<tr><td><p>results/disk_environment/05_disk_system_fair/single_test_w32_20260911/.*?</tr>','',xml)
xml=xml.replace('基础提交 7b8e792ecdf2965903bc55b4d892f76cac825306','原稿基础提交 7b8e792ecdf2965903bc55b4d892f76cac825306')
md=md.replace('基础提交 7b8e792ecdf2965903bc55b4d892f76cac825306','原稿基础提交 7b8e792ecdf2965903bc55b4d892f76cac825306')
d.xml=xml.splitlines();d.md=[md]
d.h('2026-09-14 补充：当前 locality 布局必须单独理解')
d.p('前文普通布局将 compact 与 residual 合在 payload 中，图单独存放。当前论文 test 快照使用 locality 布局：保持逻辑节点 ID、邻接顺序、DB1 gate 和距离公式，改变物理排列与记录共置。不能把普通布局的读盘解释直接套到这组曲线上。')
d.fig('Ours locality：先读图与主码，最后单独取残差', [('内存 DB1 gate','先评估新邻居\n拒绝者不发 payload 读取'),('逻辑 ID → BFS slot','查 id_to_slot.u32\n只变物理位置'),('读 graph + compact','主码打分时图已随页读入\n同页合并并进入缓存'),('更新候选池','仅幸存候选参与遍历\n逻辑搜索顺序保持'),('展开时复用图页','图与主码在同一记录\n缓存未命中仍正常读取'),('最终 residual4 重排','读取独立 residual.pages\n浮点 query 加残差修正')], '普通布局：主码与残差同读；locality：图与主码同读，残差在最终重排读取。\n这同时改变排列、共置与残差分离，不能把收益全归因于某一个因素。')
d.table(['对象','普通布局','locality v1'], [('物理排列','原记录顺序','BFS 物理 slot；逻辑 ID 保持'),('图 + 主码','分开文件','graph_compact.pages 共置'),('residual4','跟主码同一 payload','residual.pages；最终重排按需读取'),('新增常驻','无 ID 映射','id_to_slot：约 4N 字节，按 capacity 核算'),('query 缓存','图与 payload 共享','combined 与 residual 共享同一个 4 MiB 预算'),('记录限制','支持普通读者定义的布局','combined 和 residual 各须 ≤4096 字节')])
d.p('教学计算：R=64 时图记录为 4×(R+1)=260 字节。d=1024 时 compact=529，residual=648 字节。locality 的 combined=789 字节，每页 5 条；residual 每页 6 条。普通 payload=1177 字节，每页 3 条。比较时要同时计图文件、残差文件、填充与新增 ID 映射，而不是只比较 789 和 1177。')
d.eq(r'S_{\rm combined}=4(R+1)+d/2+17,\quad S_{\rm residual}=5d/8+8.')
d.p('current manuscript 明确说明曲线来自未完成正式验收的 test snapshot；普通布局不是该快照的实际物理布局。此前 locality 诊断记录报告过 ordered top-10 与搜索计数一致，但本次未重新执行该实验。这支持已有测例上的行为一致性，不证明任意配置均一致，也不解决硬件缓存与完整内存账目问题。本文不拼接旧布局与新布局的性能数据。')
d.source('新增实现定位','experiments/05_disk_system_fair/native_rust/src/locality.rs:17 / 68 / 127；ours_port.rs:955 / 1119；scripts/local_runs/export_ours_locality_layout.py')
d.source('结果边界','docs/analysis/ours_locality_query_v1.md；paper/sigmod_draft/manuscript.md 的 Experimental scope 与 Limitations')
d.save()

# DiskANN
d=Doc('02_diskann','DiskANN-PQ 磁盘版逐步拆解：内存 PQ 导航与磁盘精确评分',SCOPE)
d.h('先看全流程与符号')
d.p('DiskANN 原本就是面向 SSD 的图检索系统：内存保存全库 PQ 码，用近似距离决定下一批扩展节点；磁盘保存原始向量及邻接表，节点展开时读取，并将精确距离纳入结果。我们使用官方 Rust diskann-disk 路径，外围增加实验契约、查询级缓存和计数。它是 Ours 最直接的存储分层参照。')
d.table(['符号','含义'],[('x,q∈Rᴰ','原向量和浮点 query'),('M；cₘⱼ；kₘ(x)','PQ 子空间数、子空间中心、码字编号'),('R,L,W','出度上限、候选池宽、一次扩展的 beam 宽'),('P=4096','磁盘页；不是每条向量固定只读一页')])
d.fig('DiskANN：候选打分主要在内存，展开节点才读磁盘',[('离线 Vamana 图','候选发现 + robust prune\n保留方向多样性'),('训练 PQ 并编码','每子空间 256 个中心\n全库 PQ 码常驻内存'),('准备 query LUT','浮点 query 到各 PQ 中心\n距离表只做一次'),('内存更新候选池','邻居 PQ lookup-add\n选择待展开节点'),('读展开节点页','FP32 向量 + 邻接表\n官方异步磁盘 reader'),('更新精确结果','已展开节点计算真 L2\n继续搜索直至停止')], '箭头描述数据依赖；W 是 beam 宽，32 workers 是并发 query 数，两者不同。')
d.h('步骤 1：Vamana 怎样构建导航图')
d.p('原论文第 2 节先迭代搜索每个数据库点的邻居候选，再按距离逐个选边并剪除被已选邻居覆盖的候选，同时补反向边并限制度数。robust pruning 中 α 调整覆盖条件，使长程导航与局部连接达到不同取舍。它不是简单取 R 个欧氏最近邻。论文讨论先 α=1 再使用目标 α 的两遍构造。')
d.p('当前磁盘 exporter 读取项目已有 canonical shared graph，检查文件结构后转换为官方磁盘输入；不会每次导出都重新训练 Vamana 图。端口名称包含 R64/L400/A1.2，但具体来源应以图 manifest 与哈希为准。05C 不要求所有其他系统共用这个图。')
d.h('步骤 2：PQ 码与 query 查表')
d.eq(r'k_m(x)=\arg\min_j\|x^{(m)}-c_{m,j}\|^2,\quad T_m(j)=\|q^{(m)}-c_{m,j}\|^2.')
d.eq(r'\widehat d_{\rm PQ}^2(q,x)=\sum_{m=1}^{M}T_m(k_m(x)).')
d.p('这是忽略实现中共享中心平移记号后的标准 ADC 表达。数据库每个子空间用一个 8-bit 编号；query 保持浮点并生成距离表。4 bit/原维度与“每个 PQ 子码 4 bit”不同：当前代码令 M=ceil(D/2)，每子空间 256 个中心，码长约 D/2 字节，即平均约 4 bit/维。')
d.p('例如 D=1024 时 M=512，每向量 PQ 码为 512 字节（不含文件头）；这并非经典示例中的 32 字节码。增大 M 能提高表达能力，也提高全库常驻开销与每候选 lookup-add 次数。用过小的传统 PQ 内存估算解释当前实验，会算错预算。')
d.h('步骤 3—4：页读取与精确结果')
d.fig('DiskANN 的常驻内存与磁盘记录',[('常驻内存','全库 PQ codes\nPQ pivots / codebook\nquery LUT、候选池\n查询内缓存与 scratch'),('按需磁盘','官方 disk index\n节点 FP32 坐标\n邻接表与页填充\n读展开节点所需扇区'),('评分边界','发现邻居：PQ 近似\n展开节点：精确 L2\n未展开节点不保证返回\nC0 仍有 query 内缓存')], '完整预算 = PQ 码 + 模型 + 图/节点缓存 + 全部 worker 工作区；不能只报 PQ 文件。',True)
d.p('每轮优先选择未展开候选，用官方异步路径取节点记录；得到邻居 ID 后，用常驻 PQ 码打分，无须仅为邻居 PQ 评分再读该邻居的原向量。已读取节点的 FP32 向量可用于精确距离，形成论文所谓 implicit re-ranking。当前结果中的 rerank_us=0 是计数方式，不表示没有精确评分。')
d.eq(r'd^2(q,x)=\sum_{j=1}^{D}(q_j-x_j)^2,\quad B_{\rm physical}=4096\times n_{\rm sectors}.')
d.p('原论文用低维向量说明原向量与邻接表可落在一个扇区。当前 D=1024 时，单份 FP32 向量已经 4096 字节，再加邻接信息就超过一页；因此不能笼统宣称“每扩展一次只读 4 KiB”。实际读量以官方布局与实际 sectors 为准。')
d.h('我们改了什么，保留了什么')
d.table(['层面','当前处理'],[('图与 PQ','复用图；官方 PQ training/export；M=ceil(D/2)'),('搜索核心','官方 diskann-disk 搜索/provider 路径'),('I/O','Linux O_DIRECT + io_uring；当前工作区含 completion 修复'),('缓存','C0 关闭跨 query 节点缓存；QueryScope 内仍有受控页缓存'),('实验外围','固定数据划分、并发数、二进制哈希、真实读取计数')])
d.h('误差、瓶颈与阅读检查')
d.p('PQ 距离误差会改变扩展顺序，有限 L/W 又限制访问集合。精确评分只修复已取回节点的排序，无法自动找回未访问的近邻。磁盘吞吐还受节点跨页、请求批量和硬件缓存影响。与 Ours 比较时，应在相同 recall 目标下，同时看常驻内存、物理字节和 QPS；当前文档不将已有 test 曲线升级为正式结论。')
d.p('检查自己是否理解：若新发现 64 个邻居，是否必须读 64 份原向量？不必；PQ 码已在内存，真正展开哪些节点才决定后续磁盘读。')
d.h('来源与代码定位')
d.source('原论文，第 2 节、第 3.1—3.5 节','DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node','https://harsha-simhadri.org/pubs/DiskANN19.pdf')
d.source('实现','experiments/05_disk_system_fair/native_diskann/src/main.rs:455 / 535 / 716 / 850；cached_reader.rs；query_cache.rs')
d.save()

# Symphony
d=Doc('03_symphonyqg','SymphonyQG 磁盘适配逐步拆解：边局部 RaBitQ、FastScan 与隐式重排',SCOPE)
d.h('先看全流程与符号')
d.p('SymphonyQG 将“当前节点的原始向量、该节点所有邻居的量化码和邻接 ID”放在同一行。一次读入当前节点行，既能精确评估当前节点，也能用 FastScan 批量估计邻居距离。我们将官方行分页落盘，候选仅在被决定展开时读整行，保留这种边局部打分依赖。')
d.table(['符号','含义'],[('u,v,q','当前展开节点、其邻居、query'),('D,d,R','原维度、FHT 补齐维度、每节点出度'),('zᵤᵥ=T(xᵥ−xᵤ)','以当前节点为中心的边残差'),('bᵤᵥ, factorsᵤᵥ','该边的 1-bit 码与预计算系数'),('L；top-k','近似候选池容量；精确结果容量')])
d.fig('SymphonyQG：当前节点行提供邻居距离',[('官方 QG 建图','迭代搜索 + NSG 式剪枝\n补边以匹配 FastScan'),('按边量化邻居','中心是当前节点 u\n为 u 的每条出边存码'),('query 一次准备','FHT 旋转 + 6-bit query\n生成 FastScan LUT'),('选 u 后读取整行','FP32(u) + 边码 + ID\n先算 q 到 u 的真距离'),('批量评估所有邻居','仅用 u 行的码与 factors\n未展开 v 不读取整行'),('更新池与精确结果','v 的近似值进入搜索池\nu 的精确值进入结果池')], 'v 可从不同父节点得到不同估计；只在展开时标记 visited，不能在首次发现时永久去重。')
d.h('步骤 1：为什么编码依赖一条边')
d.p('用户提供论文第 3.1.1 节以当前节点向量作为 RaBitQ 中心。邻居 v 的方向编码围绕 xᵤ 构造，因而同一 v 被不同父节点发现时，使用的残差方向和估计可能不同。这不同于每个数据库向量只保存一份全局中心码。')
d.eq(r'z_{uv}=T(x_v-x_u),\quad y_u=T(q-x_u),\quad \|q-x_v\|^2=\|q-x_u\|^2+\|x_v-x_u\|^2-2y_u^\top z_{uv}.')
d.p('上式是用于理解的精确 L2 恒等式，RaBitQ 近似的是最后的内积。其方向估计使用码方向的比值修正；实际 scanner 不为每个 u 重新生成一套完整 query LUT，而把中心相关项预先折入每条边的 factors，复用 query 的共享旋转和 LUT。')
d.eq(r'\widehat d_{uv}^2=d^2(q,x_u)+t_{uv}+a_{uv}\,w_q\,s_{uv}+g_{uv}\,l_q.')
d.p('对应代码：t=triple_x，a=factor_dq，g=factor_vq；w_q=width，l_q=lower_val；sᵤᵥ=2×FastScanSum−sumq。这里 gᵤᵥ 是标量 factor，与 1-bit 向量 bᵤᵥ 不同。该表达直接对应 appro_dist_impl，不把它误写成对目标节点原向量的精确 L2。')
d.h('步骤 2：FastScan 与建图怎样配合')
d.p('每组四个 bit 可以作为一个 16 项小查表的索引；数据按批转置打包，SIMD 同时处理多名邻居。当前 QG_BQUERY=6，query 的量化值存于 uint8，而不是 Ours 的有符号 INT8 方案；累加结果用 uint16 后再转 float。当前 AVX512 分支用无符号扩展，避免大于 32767 的累加和被当成负值。')
d.p('原论文第 3.2 节用量化搜索加速建图中的候选发现，再用 NSG 式规则剪枝，最后对度数不足节点放宽角度条件并补边，使出度匹配 32 候选一批的 FastScan。当前端口使用 R=64，即两批。补边降低 SIMD 空槽浪费，但额外边码和 factors 会真实占用磁盘。')
d.h('步骤 3：整行到底有多大')
d.eq(r'S_{\rm row}=4D+Rd/8+12R+4R,\quad P_u=\lceil S_{\rm row}/4096\rceil.')
d.fig('SymphonyQG：每个节点行包含邻居的复制码',[('常驻内存','FHT rotator\nquery LUT 与工作区\n近似池 + 精确结果池\n每 query 有界页缓存'),('磁盘 node_rows','当前节点 FP32：4D\n邻居边码：Rd/8\n边 factors：12R\n邻接 ID：4R'),('读盘决策','展开 u → 读 u 的整行\n评分邻居不另读 v 行\n多页行需读多个扇区\n结果不足 k 有补全读取')], '例：D=d=1024、R=64 → 行长 13312 字节，独占 4 个页，共 16384 字节。',True)
d.p('端口 row 从页边界开始，每行占 ceil(S/4096) 个页。D=d=1024、R=64 时，4096+8192+768+256=13312 字节，物理占 16384 字节。原论文的空间式省略常数因子；做磁盘账目时 factors 和页尾空洞都必须算。这里的 1 bit 是每条边每维一位，不是全库每向量只有 d/8 字节。')
d.h('步骤 4：隐式重排与多次估计')
d.p('从近似池弹出 u 后才置 visited，并把 d²(q,xᵤ) 放入独立 top-k 精确结果池。邻居只要尚未展开，就可能再次带着另一父节点给出的估计入池。这保留论文第 3.1.2 节的多估计机会；若把 seen 提前到首次发现时设置，会改变算法。')
d.p('结束时直接从精确池返回。当前端口还复刻官方 update_results：如果精确池不足 k，遍历已入结果节点的邻居并读取尚未访问者补全。因此“没有统一的末尾重排扫描”成立，但“最后绝对不再读盘”不成立。CPU prefetch 也不能被翻译为把未决候选整行从磁盘提前读取。')
d.h('论文、当前端口与收益边界')
d.table(['方面','保留','磁盘新增影响'],[('量化','官方边中心编码、LUT/FastScan','边码复制使整行很大'),('搜索','展开时去重，多估计，精确结果池','节点读取受页与设备延迟约束'),('布局','保留官方 row 字节结构','按 4 KiB 对齐；独占若干页'),('实现','当前仓库官方核心并含 unsigned 修复','O_DIRECT/libaio 与 query cache 外围'),('证据','SymphonyQG.pdf §3；当前源码','论文内存 QPS 不迁移为磁盘结论')])
d.p('读盘前省去了“逐候选整行读取”，但读当前节点行本身可能搬运大量边复制码。与 Ours 比较时要看访问了多少行、每行多少页；不能只比访问节点数。RaBitQ 的概率误差分析也不等于整条图遍历完全无漏检。')
d.h('来源与代码定位')
d.source('用户原文','SymphonyQG.pdf：第 5—8 页，§3.1—3.3；本地 PDF 为含占位出版信息的版本，不照抄占位 DOI')
d.source('论文入口','SymphonyQG','https://arxiv.org/abs/2411.12229')
d.source('实际端口','experiments/05_disk_system_fair/native/symphonyqg_disk_port.cpp:216 / 410 / 455')
d.source('官方核心','baselines/symphonyqg/symqglib/qg/qg_query.hpp；qg_scanner.hpp；qg.hpp:46；common.hpp:11')
d.save()

# LVQ
d=Doc('04_og_lvq','OG-LVQ 磁盘适配逐步拆解：逐向量标量量化、Vamana 与按页取码',SCOPE)
d.h('先明确 LVQ 是哪篇论文')
d.p('这里的 LVQ 是 Locally-adaptive Vector Quantization，原论文是 Similarity search in the blink of an eye with compressed indices。用户提供的 LVQ.pdf 实际是 LOPQ，未列入当前对比，不另作方法文档。OG-LVQ 是项目方法标识，不能因拼写相近就当作 OQG。当前配置为 SVS Vamana + 单层 LVQ4。')
d.table(['符号','含义'],[('x,q,c∈Rᴰ','原向量、浮点 query、参考中心'),('r=x−c；aₓ,bₓ','中心化残差；其逐向量最小值、最大值'),('Δₓ；kₓⱼ∈{0,…,15}','该向量的步长；每维 4-bit 编码'),('R,L','Vamana 出度上限与搜索候选池宽')])
d.fig('OG-LVQ：先按向量归一化，再以 LVQ 距离搜索',[('中心化数据库','减共享参考中心 c\n缩小跨维度偏移'),('逐向量取范围','a=min(x−c), b=max(x−c)\n每个向量自己的区间'),('编码并建 Vamana','4-bit labels + scale/bias\n官方 SVS 导出图和码'),('扩展当前节点 u','按需读取 graph.pages\n取得邻居 ID'),('读取新邻居 LVQ4','解 Turbo nibble 排列\n浮点 query 对重建值算 L2'),('更新有序候选池','保留最多 max(L,k)\n按 LVQ4 距离返回 top-k')], '当前单层 LVQ4 端口没有论文可选的二级残差重排，也没有 Ours 的 DB1 读盘前 gate。')
d.h('步骤 1—2：局部自适应的是每个向量的范围')
d.eq(r'r_x=x-c,\quad a_x=\min_j r_{x,j},\quad b_x=\max_j r_{x,j},\quad \Delta_x=(b_x-a_x)/15.')
d.eq(r'k_{x,j}=\operatorname{clip}(\operatorname{round}((r_{x,j}-a_x)/\Delta_x),0,15),\quad \widetilde x_j=c_j+a_x+\Delta_x k_{x,j}.')
d.p('这是非退化范围下的数学模型；常量向量需要编码器的零范围保护。LVQ 的 local 指每向量独立范围，不是为每个倒排桶学习旋转或 PQ 中心。数据库只保留整数码与尺度/偏移，不必在查询前永久解压整个向量库。')
d.p('教学例子：某个中心化向量的范围是 [−1,2]，则 Δ=0.2，坐标 0.34 量化为 round(6.7)=7，重建为 0.4。若换成范围 [−0.1,0.2] 的向量，步长就变成 0.02。这说明相同 4 bit 会按每个向量的动态范围调节精度。')
d.h('步骤 3：建图与论文的二级量化')
d.p('原论文第 2—4 节将 LVQ 与 Vamana 式构图和搜索结合，并分析低精度构图对剪枝的影响。当前 exporter 通过官方 SVS binding 构建或复用 LVQ4_R64_W400 索引，再将保存的 graph/data 行转换为页，不重新用另一个量化器替代它。')
d.p('论文还提出 LVQ-B1×B2：第一级引导搜索，第二级压缩第一级重建残差，末尾用两级和重排。当前注册的是单层 LVQ4；文件里没有可据此宣称启用了 LVQ4×4 的第二级 payload。也不能把 Ours residual4 套到本端口。')
d.h('步骤 4：实际记录与距离内核')
d.eq(r'\widehat d_{\rm LVQ}^2(q,x)=\sum_j\left[q_j-(c_{s(x),j}+\mathrm{bias}_x+\mathrm{scale}_x k_{x,j})\right]^2.')
d.p('当前 lvq_distance 从保存行尾读取两个 float32（scale、bias）和 uint8 selector，再按 SVS Turbo 布局取 4-bit 标签。selector 选择保存的中心；公式不能擅自把任意文件都简化为没有 selector 的全库单中心格式。量化距离是重建向量 L2，和 Ours 保留真实 norm² 的交叉项估计不是同一种表达。')
d.fig('OG-LVQ：图页与逐向量 LVQ 行分开保存',[('常驻内存','centroids\n索引元信息\nseen 与有序候选池\n图/码共用 query cache'),('按需磁盘','graph.pages\nlvq4.pages\n码行：code + scale/bias\n末尾 1-byte selector'),('访问时序','展开 u：先取邻接表\n新 v：取 LVQ 行再评分\n同页/重复页可缓存\n单行须 ≤4096 字节')], '码长按保存文件实际 row stride 计算；SVS 内存对齐和论文中的 FP16 常数不能直接套入本端口。',True)
d.p('graph 行保存 degree 与固定宽邻居槽，码行按各自 record_bytes 打包；每页装 floor(4096/S) 条，不允许行跨页。当前转换直接复制保存行，code_bytes=lvq_record_bytes−9。若未来 SVS 序列化布局改变，应重新核对，而不是将 Turbo 排列当作通用相邻 nibble 顺序。')
d.h('步骤 5：搜索与端口忠实度的具体边界')
d.p('起点先读取 LVQ 行并打分。每次选最近未扩展节点，读图行，再对新邻居逐个取 LVQ 行并计算距离；入池时标记 seen，按距离和 ID 排序，池限制为 max(L,10)。结束后直接返回当前近似距离最小的十个节点。没有额外 FP32 或二级码重排。')
d.p('需要修正一个容易误读的标签：source_kernel 写着 official SVS LVQ4 distance kernel，但当前源码 lvq_distance 实际是本地逐维标量 C++ 循环，负责兼容官方编码布局；搜索循环也由端口实现。因而可说“使用官方 SVS 建图和保存码，采用兼容解码距离”，不能写成“查询直接调用未修改的官方 SIMD 内核”。这一区别可能影响 CPU 开销解释。')
d.p('现有 run_og_lvq_disk_port_parity.py 把同一保存索引的磁盘 top-10 与官方 SVS 内存搜索对照，是正确的行为核验方向；本次只阅读脚本，没有重新运行。有限测例的一致性也不能证明所有宽度、维度与并列距离情形都一致。')
d.h('误差与磁盘适配的得失')
d.eq(r'e_x=\widetilde x-x,\quad \widehat d^2-d^2=-2(q-x)^\top e_x+\|e_x\|^2.')
d.p('逐向量范围提高动态区间利用率，但异常极值仍会拉大整向量步长。磁盘适配降低了全库常驻量，却把每个新邻居的打分变成按需取码：若候选分散，一条短 LVQ 行也可能触发整页读取。少搬多少字节取决于每页装几条、访问局部性与缓存命中，不等于 32/4=8 倍吞吐。')
d.h('来源与代码定位')
d.source('正确 LVQ 原论文，§2—5','Similarity search in the blink of an eye with compressed indices','https://www.vldb.org/pvldb/vol16/p3433-aguerrebere.pdf')
d.source('当前实现','experiments/05_disk_system_fair/native/og_lvq_disk_port.cpp:321 / 415 / 463 / 478 / 510')
d.source('行为核验脚本','experiments/05_disk_system_fair/tests/run_og_lvq_disk_port_parity.py；run_og_lvq_disk_port_integration.py')
d.save()

# Glass
d=Doc('05_glass_nsg','Glass-NSG 磁盘适配逐步拆解：稀疏导航图、SQ4U 与双端整数距离',SCOPE)
d.h('先区分图算法与量化实现')
d.p('NSG 是导航图方法，Glass 是实现库，SQ4U 是当前选择的量化/距离后端。NSG 原论文并不定义这里的 4-bit query 编码，更不原生描述这个磁盘端口。我们的 05C 组合为 Glass NSG_R64_L400_SQ4U：保留库的建图和 SQ4U 核心，将图行和码行分别放入磁盘页。')
d.table(['符号','含义'],[('x,q∈Rᴰ','数据库向量与输入 query'),('a,b；Δ=(b−a)/15','训练得到的共享标量范围与步长'),('k(x),k(q)','两端 4-bit 码'),('R,L,P','图度数配置、搜索宽度、4096 字节页')])
d.fig('Glass-NSG：query 和 database 都进入 SQ4U',[('NSG 构建','候选搜索、邻居剪枝\n导航连通性处理'),('全局标量校准','学习共享 min / dif\n当前有尾部截断'),('编码数据库与 query','4-bit 无符号 nibble\n两端用同一 calibrator'),('展开节点读图行','graph.pages 给出邻居\n只处理尚未 seen 的节点'),('读邻居 SQ4 行','sq4u_codes.pages 按页加载\n官方 u4×u4 L2 内核'),('整数距离维护池','按距离与 ID 排序\n直接返回，无浮点重排')], 'SQ4U 的 U 是 uniform；当前不是浮点 query 对 SQ4 重建值的 ADC 路径。')
d.h('步骤 1：NSG 负责让图既稀疏又可导航')
d.p('NSG 原论文从 MRNG 的单调导航性质出发，实践中先建立近似 kNN 图，选接近数据中心的 navigating node，对每点搜索出邻居候选并施加几何剪枝，最后用可达性修复补上缺失连接。目标同时覆盖连通性、较低度数、较短路径和索引大小。理论对象 MRNG 与最终启发式 NSG 应区分。')
d.p('当前磁盘 exporter 调用 glass::create_nsg("SQ4U",R,L) 构建，或复用保存的对应图；它并非把论文伪代码重新写一遍。端口以保存的 entry points 和 graph_k 为查询依据，不能假定所有图都只有恰好一个入口或所有节点恰好满度。')
d.h('步骤 2：共享校准与 4-bit 标签')
d.eq(r'k_j(x)=\operatorname{round}\!\left(15\operatorname{clip}\left(\frac{x_j-a}{b-a},0,1\right)\right),\quad \widetilde x_j=a+\Delta k_j(x).')
d.p('非退化范围下，当前 calibrator 是 AffineCalibrator<15>，min/dif 为共享标量，不是每向量一个区间，也不是每维一个区间。train_ratio=0.1，drop_ratio=0.01；train 调用从传入数据指针起取指定数量的标量参与校准，不应无依据写成随机采样全库 10%。尾部截断允许少量值被 clip。')
d.p('每两个坐标打成一个字节。code_size 由 Quantizer 的补齐/存储规则决定，不能直接将 ceil(D/2) 当成所有维度下的真实行长。磁盘 meta 持久化 code_size、cal_min 与 cal_dif，查询时恢复同一校准器。')
d.h('步骤 3：query 同样被压成 4 bit')
d.eq(r's(q,x)=\sum_j\big[k_j(q)-k_j(x)\big]^2,\quad \|\widetilde q-\widetilde x\|^2=\Delta^2 s(q,x).')
d.p('get_computer(query) 先调用同一 encode，将 query 转为 u4；内核 helpa::l2a_u4_u4 返回 int32 分数。由于 Δ² 对所有候选相同，整数分数可保持重建空间 L2 排序，但不是原始空间精确距离。论文中 NSG 用原空间距离的理论性质不能直接当作当前 SQ4U 搜索的保证。')
d.p('教学例子：共享区间 [0,15] 时，query 坐标 7.4 编码为 7，数据库坐标 7.1 也为 7，该维整数距离为 0；原空间却有 0.09 的平方距离。双端量化可能形成并列距离，端口用 ID 作为第二排序键。')
d.h('步骤 4：磁盘记录与读码时机')
d.fig('Glass：小码行省容量，页粒度决定实际读量',[('常驻内存','共享 min / dif\n入口与元数据\nSQ4 query code\n候选池、seen、页缓存'),('磁盘分文件','graph.pages：邻接行\nsq4u_codes.pages：SQ4U 行\n每页打包若干完整行\n行宽来自 index.meta'),('查询依赖','展开 u 先读图\n新邻居 v 读码再评分\n同页可以 query 内复用\n不读取原始 FP32 重排')], '短码不代表短 I/O：缓存未命中时仍按 4 KiB 读取，图页与码页须一起计费。',True)
d.p('端口对每个入口先读码评分。循环选最近的未扩展候选，读取其图行，遇到 −1 邻居哨兵停止；对未 seen 邻居取码评分并入池。图和码共享每 query 的受控页缓存，但使用不同文件命名空间。当前执行顺序是逐邻居调用 reader，不凭异步 reader 的名称宣称已经把所有 frontier 候选批量并发读取。')
d.h('误差、保留部分与端口边界')
d.eq(r'\delta_q=\widetilde q-q,\quad\delta_x=\widetilde x-x,\quad \widehat d^2-d^2=2(q-x)^\top(\delta_q-\delta_x)+\|\delta_q-\delta_x\|^2.')
d.p('误差来自双端量化、截断及有限图遍历。该配置没有原向量最终重排，增大 L 只能扩大候选搜索，不能完全消掉量化并列和排序误差。与 LVQ 比较的关键是“共享区间、双端整数核”对“逐向量区间、浮点 query 重建距离”，而不仅是两者都叫 4-bit。')
d.table(['部分','当前事实'],[('图构建','官方 Glass NSG builder'),('编码和距离','官方 SQ4U calibrator、encode、u4×u4 kernel'),('查询控制流','本地磁盘搜索循环，应结合 parity 验证'),('物理 I/O','O_DIRECT + libaio，4 KiB 对齐，query 内缓存'),('论文结论','NSG 结构分析不直接证明此量化磁盘组合的性能')])
d.h('来源与代码定位')
d.source('NSG 原论文，构图章节与 Algorithm 2','Fast Approximate Nearest Neighbor Search with the Navigating Spreading-out Graph','https://arxiv.org/abs/1707.00143v9')
d.source('官方库','Glass / pyglass','https://github.com/zilliztech/pyglass')
d.source('实际实现','experiments/05_disk_system_fair/native/glass_disk_port.cpp:344 / 443 / 704；baselines/pyglass/glass/quant/sq4u_quant.hpp；calibrator.hpp')
d.save()

(OUT/'manifest.json').write_text(json.dumps(docs,ensure_ascii=False,indent=2))
# Snapshot only sources actually discussed; generated PDFs stay local as reading materials.
paths=['LVQ.pdf','OQG.pdf','SymphonyQG.pdf','experiments/05_disk_system_fair/native/og_lvq_disk_port.cpp','experiments/05_disk_system_fair/native/symphonyqg_disk_port.cpp','experiments/05_disk_system_fair/native/glass_disk_port.cpp','experiments/05_disk_system_fair/native_diskann/src/main.rs','experiments/05_disk_system_fair/native_rust/src/ours_port.rs','experiments/05_disk_system_fair/native_rust/src/locality.rs','baselines/pyglass/glass/quant/sq4u_quant.hpp','baselines/symphonyqg/symqglib/qg/qg_scanner.hpp','Ours/core/hnswlib/space_rabitq.h','paper/sigmod_draft/manuscript.md']
(OUT/'sources/sha256.json').write_text(json.dumps({p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},indent=2))
print(json.dumps(docs,ensure_ascii=False,indent=2))
