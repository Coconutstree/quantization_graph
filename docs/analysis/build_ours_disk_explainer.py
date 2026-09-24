"""Rebuild the source-grounded method explainer; no benchmarks or source edits."""
from pathlib import Path
from html import escape as E
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/analysis/ours_disk_explainer_20260911'
OUT.mkdir(exist_ok=True)
xml, md = [], []
def title(t): xml.append(f'<title>{E(t)}</title>'); md.append('# '+t)
def h(t, n=1): xml.append(f'<h{n}>{E(t)}</h{n}>'); md.append('#'*(n+1)+' '+t)
def p(t): xml.append(f'<p>{E(t)}</p>'); md.append(t)
def eq(t): xml.append(f'<p><latex>{E(t)}</latex></p>'); md.append('$$\n'+t+'\n$$')
def table(headers, rows):
    xml.append('<table><thead><tr>'+''.join(f'<th><p>{E(c)}</p></th>' for c in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td><p>{E(str(c))}</p></td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>')
    md.append('| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows))

def diagram(name, subtitle, nodes, links, notes):
    # Simple, self-contained SVG suitable for conversion into editable whiteboard nodes.
    s=['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="540" viewBox="0 0 1000 540">', '<rect width="1000" height="540" fill="#f8fafc"/>',f'<text x="36" y="42" font-size="22" fill="#0f172a">{E(subtitle)}</text>']
    colors={'blue':('#e0f2fe','#0369a1'),'purple':('#ede9fe','#6d28d9'),'green':('#dcfce7','#15803d'),'orange':('#ffedd5','#c2410c')}
    for a,b,label in links:
        x,y,w,_,_=nodes[a]; xx,yy,ww,_,_=nodes[b]
        start=(x+w/2,y+74); end=(xx+ww/2,yy)
        mid=(start[1]+end[1])/2
        s.append(f'<polyline points="{start[0]},{start[1]} {start[0]},{mid} {end[0]},{mid} {end[0]},{end[1]}" fill="none" stroke="#64748b" stroke-width="2"/>')
        s.append(f'<polygon points="{end[0]-5},{end[1]-8} {end[0]+5},{end[1]-8} {end[0]},{end[1]}" fill="#64748b"/>')
        if label: s.append(f'<text x="{end[0]+10}" y="{mid-7}" font-size="15" fill="#475569">{E(label)}</text>')
    for x,y,w,text,color in nodes:
        fill,stroke=colors[color]
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="74" rx="14" fill="{fill}" stroke="{stroke}"/>')
        for i,line in enumerate(text.split('\n')):
            s.append(f'<text x="{x+w/2}" y="{y+29+i*24}" text-anchor="middle" font-size="18" fill="{stroke}">{E(line)}</text>')
    for i,line in enumerate(notes): s.append(f'<text x="36" y="{480+i*25}" font-size="17" fill="#334155">{E(line)}</text>')
    s.append('</svg>'); src=''.join(s)
    ET.fromstring(src)
    (OUT/(name+'.svg')).write_text(src)
    xml.append(f'<whiteboard type="svg" path="@./docs/analysis/ours_disk_explainer_20260911/{name}.svg"></whiteboard>')
    md.append(f'![{subtitle}]({name}.svg)')

title('Ours 磁盘版逐步拆解：从 ExRaBitQ4 编码到 DB1 筛选、按页读取与残差重排')
p('面向已有向量检索基础、希望读懂方法与核心代码的读者。沿用 SAQ 示例的“符号—分步机制—存储与查询—误差边界—来源”顺序。主线为 05B/05C 的 Ours-Disk、K=1、db1+coalescing+reuse、关闭 adaptive route；以 2026-09-11 当前工作区代码为准。本文只阅读代码与已有结果，没有重新训练、建图或运行性能实验。')
h('一、先看全流程与符号')
p('Ours 磁盘版的核心思路是：离线用 4-bit ExRaBitQ 编码距离构建 Vamana 图；在线把每个向量的 1-bit 最高位及筛选系数留在内存，先判断新邻居是否值得读取，再从磁盘读取完整 4-bit 与 residual4 记录，更新候选池，最后对池内候选补偿量化误差。图邻接表也按需读盘。')
p('阅读顺序：步骤 1—3 解释共享变换、数据库编码与残差；步骤 4—5 解释建图与存储；步骤 6—8 解释 query、筛选与重排。“4-bit 主码”“1-bit 最高位”“4-bit 残差码”是三个不同对象。')
table(['符号','类型 / 维度','含义'],[
('N，D，d','整数','库大小、原始维数、补齐后的编码维数；d 是不小于 round-up-64(D) 的最小 2 的幂'),
('x，q，c','D 维实向量','数据库向量、query、共享参考质心'),('rₓ，r_q；nₓ，n_q','D 维向量；标量','减质心后的残差与真实模长'),
('T；z，y，u','d×D 变换；d 维向量','带补零的随机符号 Hadamard 变换；z=T rₓ，y=T r_q，u=z/nₓ'),
('k，h；λₓ','整数向量、实向量；标量','主码标签 k∈{0,…,15}ᵈ；h=k−7.5；λₓ 为解码比例'),
('b，ℓ；sₓ','0/1 向量、0…7 向量；标量','k=8b+ℓ；sₓ 是最高位与 query 的居中内积'),
('ê；aⱼ，vᵢ','d 维实向量；尺度、整数','分块残差重建，êᵢ=aⱼvᵢ'),
('L，B，R，k_out','整数','搜索池宽度、frontier beam、图出度上限、返回数量'),
('ε₀，τ','标量','筛选容差与当前候选池尾部距离；不是同一个量')])
h('图 1：编码层次与内存 / 磁盘边界',2)
diagram('fig01_layers','一份数据库编码，支撑三种计算精度',[
 (335,80,330,'x → 减质心 / 归一化 / 旋转\n得到 u 与真实模长 nₓ','blue'),
 (335,215,330,'4-bit 主码 k 与比例 λₓ\n构建 Vamana 图 / 在线打分','purple'),
 (35,350,280,'内存：DB1 + 筛选系数\n从主码提取最高位 b','blue'),
 (365,350,280,'磁盘：完整主码 + residual4\n补偿主码的剩余误差','orange'),
 (695,350,270,'磁盘：Vamana 邻接表\n扩展节点时读取','green')],[(0,1,''),(1,2,'提取'),(1,3,'编码误差'),(1,4,'离线建图')],['DB1 是主码最高位的内存副本；磁盘主码仍保存完整 4 bit。','residual4 是另一组量化误差码，不是主码剩下的 3 bit。'])
h('二、步骤 1：共享质心、真实模长与快速旋转')
p('输入是原始数据库向量。K=1 路径从数据库训练样本计算共享均值 c；训练样本数取配置与库大小的较小值，采样 seed=100。只有样本覆盖全库时，才能称为全库均值；这里不能照搬 SAQ 示例的“全库质心”口径。query 不参与这一训练。')
eq(r'r_x=x-c,\quad r_q=q-c,\quad n_x=\|r_x\|_2,\quad n_q=\|r_q\|_2.')
eq(r'\|q-x\|_2^2=n_q^2+n_x^2-2r_q^\top r_x.')
p('将归一化后的数据库残差补零到 d 维，乘共享随机符号，再做带 1/√d 归一化的 Hadamard 变换。正式集成创建空间时使用 seed=100；seed=0 是代码保留的恒等变换分支。函数虽然名为 roundUp64，实际还会向上补到 2 的幂：例如 D=960→d=1024，D=1536→d=2048。')
eq(r'T^\top T=I_D,\quad z=Tr_x,\quad y=Tr_q,\quad u=z/n_x,\quad y^\top z=r_q^\top r_x.')
p('输出是单位方向 u、真实模长平方 nₓ²及共享变换。与示例 SAQ 的 PCA 能量分段不同，这条主线采用共享随机混合与统一 4-bit 主码；没有学习 PCA 分段或按方向动态分配 bit。零残差通过零模长分支处理，不强行定义单位向量。')
h('三、步骤 2：ExRaBitQ4 主码与比值尺度')
p('先看方向编码。fastQuantizeAbs 对 |u| 枚举缩放阈值变化，使用优先队列更新幅度标签，寻找方向内积较好的码字。正负号单独决定最终半整数格点的符号。它不是逐维固定步长舍入，也不是 SAQ 的逐坐标 ±1 CAQ 迭代，更不是 PQ 的 k-means 中心表。')
eq(r'm_i\in\{0,\ldots,7\},\quad k_i=\begin{cases}8+m_i&u_i>0\\7-m_i&u_i\le0\end{cases},\quad h_i=k_i-7.5.')
p('因此 h 的坐标取 ±0.5、±1.5、…、±7.5；k 才是每坐标 4 bit 的存储标签。搜索码字时比较的方向目标可写为 (|u|ᵀ(m+1/2))/‖m+1/2‖。当前实现是有限阈值搜索，不声称找到全部离散码字中的全局最优。')
eq(r'\lambda_x=\frac{n_x}{u^\top h}=\frac{n_x^2}{z^\top h},\quad \widetilde z=\lambda_x h,\quad \texttt{long\_scale}=2\lambda_x.')
p('这是非退化、未触发保护分支时与代码相符的代数表达：ip_norm 是 1/(uᵀh)，EncodedHeader 保存 norm_sqr 与 long_scale。λₓ 是比例，不是夹角余弦；long_scale 已包含距离交叉项的系数 2，不能再多乘一次 2。')
eq(r'z^\top(\widetilde z-z)=0,\quad \widehat d_4^2=n_q^2+n_x^2-2y^\top\widetilde z.')
p('与 SAQ 示例的比值修正类似，该等式说明主码有效替代向量的误差与 z 正交。它消除沿 z 的径向误差，仍保留切向误差；正交恒等式是理想代数，不代表每个 query 距离都准确。')
h('四、步骤 3：把主码误差再编码成 residual4')
p('主码已经给出近似向量 z̃。残差编码针对 e=z−z̃，在每 16 个编码坐标组成的块中搜索 MSE 尺度，把尺度存为 FP16，并用实际存下再读出的尺度计算残差标签。当前 Rust 构造空间明确设置 residual_bits=4、block_size=16、mse=true、fp16=true。')
eq(r'e=z-\widetilde z,\quad v_i=\operatorname{clip}\!\left(\operatorname{round}(e_i/a_j),-7,7\right),\quad \widehat e_i=a_jv_i.')
p('这里每个标签占 4 bit，当前使用的有符号数值范围是 −7…7。输出包括打包残差码、每块 FP16 尺度和 8 字节残差因子。记录中的 residual_norm_sqr 与 long_residual_inner_product 仍占空间，但当前普通 residual4 距离修正直接用 query 与残差的内积，不把这两个因子再加进距离。')
eq(r'\widehat d_{4+\mathrm{res4}}^2=\widehat d_4^2-2y^\top\widehat e=n_q^2+n_x^2-2y^\top(\widetilde z+\widehat e).')
p('真实 nₓ²保持不变。不能把估计器改写成 ‖y−(z̃+ê)‖²后直接使用重建模长，因为一般 ‖z̃+ê‖²≠nₓ²。residual4 编码的是主码误差，不是重新减一个质心，也不是直接读取原始浮点向量。')
h('五、步骤 4—5：对称 4-bit 建图与真实存储布局')
h('步骤 4：用编码距离构建 Vamana 图',2)
p('离线建图时，两端都是 ExRaBitQ4 编码：调用 rabitq_build_vamana_graph → VamanaIndex，使用对称编码距离发现候选并剪枝，限制邻接表出度。它与在线的“浮点 / INT8 query 对数据库编码”不是同一个估计端点。主码缩放作用于两端，方向内积会裁剪到 [−1,1] 后恢复残差 L2。')
p('Ours/config.json 列出 M=32/64、L_build=400、alpha=1.2、rerank_candidates=100 等默认配置；一次磁盘运行的实际图必须以其 graph_role、图哈希和导出元数据为准。05B 中 Ours 保留自己的 ExRaBitQ 对称图，不能仅凭“shared graph”目录名就说它与 PQ/SQ/SAQ 共用同一份邻接表。')
h('步骤 5：常驻的是 sidecar，按需读取的是图与 payload',2)
table(['对象','普通 residual4 布局：每向量字节','位置 / 用途'],[
('DB1 位面','d/8','内存；从完整主码最高位提取'),('PaperPruneFactors','通常 20：4 个 float32 + valid + ABI padding','内存；模长、交叉项尺度与误差尺度'),
('compact 主记录','d/2 + 17','磁盘 payload；主码、8 字节 header、8 字节 short factors、1 字节簇号'),
('residual 记录','d/2 + d/8 + 8','同一 payload；residual4、每 16 维 2 字节尺度、8 字节因子'),
('完整 payload 记录','9d/8 + 25','紧邻保存 compact + residual，随后按 4096 字节页打包'),
('邻接表 / 模型 / 工作区','另外计算','图页、共享质心与旋转、visited、候选池、页缓存、临时批次均需计入总预算')])
p('普通布局即使 K=1 仍在 compact 中保留 1 字节簇号。表中的 factor ABI 大小由桥接布局决定；实际文件还含头部与页填充。nested4x4 等环境开关分支具有不同布局，本表不套用于它们。')
eq(r'S=9d/8+25,\quad P=4096,\quad r=\lfloor P/S\rfloor,\quad \mathrm{offset}(i)=\lfloor i/r\rfloor P+(i\bmod r)S\quad(S\le P).')
p('d=1024 时，compact=529 字节、residual=648 字节，总记录=1177 字节，每页装 3 条，余 565 字节。大于一页的记录则独占 ceil(S/4096) 个连续页。DB1 内存副本与磁盘完整主码存在重复，不能把总存储写成“只剩 3 bit 下盘”。每向量真实总成本也远大于内存位面的 1 bit/维。')
h('六、步骤 6：准备 query，一次 INT8 编码服务遍历')
p('query 先减同一个 c 并旋转为 y，保留真实 n_q²与浮点旋转坐标，同时生成 INT8 query。使用每 query 一个尺度 s_q=max|yᵢ|/128，并用由原始 query 字节和簇号确定的随机种子生成可复现 dither。')
eq(r'Q_i=\operatorname{clip}\!\left(\lfloor y_i/s_q+U_i\rfloor,-128,127\right),\quad U_i\in[0,1),\quad \widehat y_i=s_qQ_i.')
p('未裁剪随机舍入具有均值保持性质；实际 INT8 正端饱和与固定伪随机实现使“所有 query 严格无偏”不能直接成立。当前 paper gate 没有显式加入 INT8 误差范数项，因此不能把代码注释当作真实数据上零误剪的证明。零 query 使用保护分支。')
p('精度端点必须按调用链区分：有效 DB1 gate 后的遍历使用 INT8；起点或无效 gate 的 mode=0 使用浮点 query 重算；最终 OursPreparedQuery::rerank 也显式使用 mode=0，随后 residual 内核读取浮点 query 坐标。它复用 PreparedQuery 对象，但没有把最终 residual4 内积继续限制为 INT8。Ours/README.md 中“INT8 reused by residual rerank”的文字与当前代码不一致，本文以实现为准。')
h('七、步骤 7：DB1 gate 怎样决定是否读盘')
h('先算便宜的交叉项与筛选值',2)
eq(r'b_i=\lfloor k_i/8\rfloor,\quad \ell_i=k_i\bmod8,\quad s_x=\sum_i\widehat y_i(b_i-1/2).')
p('b 就是驻留内存的 DB1。整数内核按位面掩码累加选中的 query 字节，再减去半个 query 总和，得到 short_ip=sₓ；不需要展开完整 4-bit 码。令 αₓ=uᵀsign(u)/√d 为方向夹角余弦，则非退化时 sidecar 的两个尺度如下。')
eq(r'A_x=\frac{4n_x}{\sum_i|u_i|},\quad E_x=\frac{2n_x}{\sqrt{d-1}}\sqrt{\frac{1-\alpha_x^2}{\alpha_x^2}}.')
eq(r'C_x=A_xs_x,\quad C_x^+=\operatorname{clip}(C_x+\varepsilon_0E_xn_q,-2n_xn_q,2n_xn_q),\quad LB_x=n_x^2+n_q^2-C_x^+.')
p('Aₓsₓ估计的是距离中的 2 倍内积交叉项。ε₀扩大误差余量，通常使筛选更保守；代码先裁剪交叉项上界，再组成 lower_bound。这个 lower_bound 属于带分布 / 误差建模前提的 paper-prune 筛选值，不是已验证对所有点对成立的确定性下界。')
h('候选池满时才剪枝',2)
p('搜索从数据库节点 0 开始，池宽 L 会被提升到至少 k_out。每轮选最靠前的 B 个未扩展候选，按顺序读其邻接表；每个新邻居先标记 visited，再进入 DB1 批次。只有池已满、估计 valid 且 LBₓ严格大于当前尾部距离 τ 时才丢弃。无效估计走完整打分；被筛掉的节点通常不会因后续从另一条边遇到而重新尝试。')
p('筛后候选每批最多 64 个，读取 payload，再计算 4-bit 距离并插入有序池。插入前还会用更新后的池尾二次比较 lower_bound；这次比较可能省去入池，但 payload 已经读过，不能算成提前避免了一次读盘。')
eq(r'h=8(b-1/2)+(\ell-3.5),\quad \widehat d_{4,\mathrm{INT8}}^2=n_x^2+n_q^2-2\lambda_x\left[8s_x+\sum_i\widehat y_i(\ell_i-3.5)\right].')
p('上式就是 short_ip 的复用点：最高位内积已经算过，只补主码剩下的 3 bit。计算复用与页缓存复用是两种机制；OursAblation 的 payload_reuse 开关控制的是后者，不能把所有“reuse”都理解成内积复用。')
h('图 2：一次邻居扩展中的筛选分支',2)
diagram('fig02_search','先用内存 DB1 判断，再为幸存候选读取 payload',[
 (330,75,340,'读当前节点的磁盘邻接表\n收集未访问邻居并标记 visited','green'),
 (330,205,340,'DB1 × INT8：得到 LB 与 sₓ\n池满且 valid 且 LB > τ？','blue'),
 (45,345,280,'是：丢弃该候选\n不为它请求 payload','orange'),
 (390,345,545,'否：读页 → 复用 sₓ 算 4-bit 距离 → 更新池\n池无未扩展节点后，浮点 query + residual4 重排','purple')],[(0,1,''),(1,2,'是'),(1,3,'否 / 未满 / 无效')],['图邻接页在筛选前已读；DB1 主要避免的是候选 payload 读取。','丢弃候选不会自动带来等量页数下降：同页可能还有其他幸存候选。'])
h('八、步骤 8：按页合并、query 内缓存与最终重排')
p('read_records 先把幸存 ID 转成页号集合。coalescing 对同一批次页号排序、去重，然后用 native O_DIRECT + libaio 读取缺失页；它不是把图重新排列成物理相邻布局。读取粒度固定为 4 KiB，短记录不会跨页。当前 search loop 虽有 frontier beam，仍按 frontier 节点依次读邻接表，不应写成自动并行读取所有 frontier 图页。')
p('reuse 使用每 query 的有界 LRU 页缓存，图与 payload 共享缓存预算，通过文件标识区分相同页号。默认每 worker 4 MiB，包含页与索引 / LRU 元数据；新 query 重置缓存。32 workers 对应约 128 MiB 缓存预算，此外还有 visited、临时返回批次等工作区。C=0 / cache_bytes=0 表示共享缓存口径为零，不表示不存在 query 内缓存。')
h('图 3：候选数与物理页数为什么不同',2)
diagram('fig03_pages','教学算例：假设每页容纳 3 条 payload 记录',[
 (30,75,270,'幸存 ID：0、1、4\n对应页号：0、0、1','blue'),
 (360,75,280,'筛掉 ID 2\n但 0、1 仍需读取页 0','orange'),
 (700,75,270,'最终重排：ID 1、4\n再次请求页 0、1','purple'),
 (30,240,270,'批内 coalescing\n3 个页请求 → 2 个唯一页','green'),
 (360,240,280,'页 0：[0 | 1 | 2]\n页 1：[3 | 4 | 5]','orange'),
 (700,240,270,'若仍命中 query 缓存\n新增物理页读取 = 0','green')],[(0,3,''),(1,4,''),(2,5,'')],['页命中省的是再次读取；缓存驱逐后仍可能重新读盘。','遍历时 compact 与 residual 已随同一记录读取；重排可以直接命中这些页。'])
p('遍历结束后，从当前池前部取 min(max(rerank_candidates,k_out),池大小) 个候选，重读 / 命中对应 payload，浮点 query 重算完整主码距离，再加 residual4 内积修正，按估计距离排序输出 top-k。默认 rerank_candidates=100 不表示每次一定重排 100 个：池只有 10 个时实际只重排 10 个。')
p('当前磁盘布局在每次主码读取时连 residual 记录一并读取并提取，精度分阶段计算不等于残差在第二阶段才首次落盘读取。因此 rerank_page_reads=0 可以来自 query 缓存命中，不能推出 residual 没有存储成本或 CPU 成本。')
h('九、从全空间看误差：压缩误差与搜索遗漏要分开')
p('先只分析一个已读候选，并假设共享变换精确保持内积。令最终数据库有效替代向量 t=z̃+ê，则浮点 query 重排的误差有直接恒等式：')
eq(r'\widehat d_{4+\mathrm{res4}}^2-d^2=-2y^\top(t-z).')
p('没有 residual 时，主码比例在理想代数下使 zᵀ(z̃−z)=0；加入按块 MSE 量化的 ê 后，不保证新误差仍严格垂直 z。MSE 减小通常有利于近似，但排序变化取决于 query 与剩余误差的对齐关系，不能仅凭残差重建更准就承诺 recall 必然提高。')
eq(r'\widehat d_{4,\mathrm{INT8}}^2-d^2=-2y^\top(\widetilde z-z)-2(\widehat y-y)^\top\widetilde z.')
p('遍历距离多了 query 量化误差项。最终浮点重排能去掉已进入重排集合的这部分误差，无法找回前面被 gate 丢弃、被有限池淘汰或图路径未访问的近邻。Recall 损失可能来自建图、近似 gate、有限宽图搜索和最终编码排序，不能全部归因于某一个量化器。')
p('此外，τ 来自当前混合精度候选距离，而不是原空间精确距离。即使单独讨论某种概率界，也不能据此直接宣称整条图搜索没有误剪。代码同时存在针对剩余位的其他 bound 函数；本篇跟踪的是实际调用的 paper sidecar gate，不能把未调用的确定性分支当成主线证明。')
h('十、用现有 AGNews 记录把流程对上')
p('下面只引用 IDE 所在 single_test_w32_20260911 目录下 Ours-Disk/result.json 的一个已有行，用来解释字段，不作为新实验或跨方法胜负结论。该文件 status=done、implementation_parity=passed，但 formal_ready=false、memory_accounting_complete=false；运行目录日期及 git_commit 也不能证明运行二进制与所有当前未提交修改完全一致。')
table(['字段 / 结果','已有值','对应机制'],[
('数据 / 执行配置','AGNews；N=769382，D=1024；32 workers；beam=1','workers 是并发数，不是池宽或图出度'),
('本例池宽 / query 数','L=10；800 queries','实际重排 10 个候选'),('Recall / QPS','0.93175 / 35.4057','原文件单次 test 行；不外推正式性能'),
('DB1 checks → survivors / query','761.455 → 301.6275','约 60.39% 的检查候选在第一道 gate 后未幸存'),
('full4 candidates / page reads','302.6275 / 302.5375','候选数含起点；页数受同页、缓存与布局共同影响'),
('全部 4 KiB sectors / query','318.79','包括图与 payload 等实际读页'),('rerank candidates / page reads','10 / 0','最终重排命中前面已读取的页'),
('常驻 / 峰值 / 内存审计','resident_bytes=113872632；文件级 peak_rss=562700288；审计未完成','sidecar 字节不能替代真实进程峰值')])
p('本例常驻数可复算为 N×(128+20)+1024×4=113872632 字节，吻合 DB1、factor 与质心口径。磁盘含主码和 residual 的未分页记录合计 N×1177=905562614 字节；结果字段名 ours_8bit_payload_bytes 对应这一口径，不能解释成单独的一份 8-bit 主量化器。')
p('注意 I/O 字段也有计时边界：rerank_us 包含重排读取及计算，io_wait_us 的记录方式覆盖读取流程；不能把所有微秒字段不加辨别地直接相加。候选减少约 60% 也不能等价为磁盘页减少约 60%，后者需要同配置无 gate 对照。')
h('十一、当前实现、示例 SAQ 与实验分支的边界')
table(['维度','示例 SAQ 主线','本文 Ours-Disk 主线'],[
('空间组织','PCA + 分段 + 段内 Haar','补零 + 共享随机符号 Hadamard'),('数据库编码','混合 bit 格点标签 + 逐段 β','统一 4-bit 主码 + 比值尺度 + residual4'),
('候选来源','全库 full-scan','ExRaBitQ 对称 Vamana 图遍历'),('query 精度','浮点','DB1/有效 gate 后遍历用 INT8，最终浮点 query 重排'),
('存储策略','紧凑 code-only ADC 索引','内存 DB1/factors；磁盘邻接表及完整 payload'),('筛选 / 最终排序','示例无分层剪枝或 rerank','paper gate → 4-bit 遍历 → residual4 近似重排')])
p('近期 adaptive route 会引入投影与候选排序 / shortlist，并可配置 ratio、keep、revisit；代码明确称其为经验 gate，不是原空间距离下界。此分支在本例 adaptive_route_dim=0 下关闭。nested4x4、Full/B1/INT4 query 消融也不混入本文默认公式。')
p('full4-resident/no-gate、db1-resident/full4-on-ssd、db1+coalescing、db1+coalescing+reuse 是四个消融状态。第一个还会改变 query codec，因此对它与主线的性能差值应按实际配置解释，不能自动视为“只切换 gate 的纯消融”。')
h('十二、快速复习与代码来源')
p('八句话复习：① 训练共享质心，保留真实残差模长。② 补维并随机 Hadamard 旋转，编码 4-bit 主码与比值尺度。③ 对主码误差做 block16 residual4 编码。④ 以对称 4-bit 距离构建 Vamana 图。⑤ DB1 和筛选因子驻留内存，图与完整 payload 分页存储。⑥ query 生成 INT8，同时保留浮点坐标。⑦ DB1 gate 先筛，再读页、复用最高位内积补算主码距离并更新池。⑧ 池内候选用浮点 query 加 residual4 重排。')
p('最需要记住：精度分层、存储分层和缓存复用不是同一件事。DB1 的价值在于读盘前筛选，residual4 的价值在于修正已保留候选的内积；二者都不自动保证完整图搜索的 recall。')
table(['文件与定位','读什么'],[
('Ours/core/hnswlib/space_rabitq.h:217 / 867 / 990','编码维数、幅度阈值搜索、旋转'),('同文件:5866','普通主码编码、比值尺度与残差编码'),('同文件:3429 / 4873 / 4701','query 准备、INT8 最高位 gate、筛选值公式'),('同文件:4342 / 4080 / 5453','INT8 内积复用、浮点 mode=0、residual4 内核'),
('Ours/core/hnswlib/vamana_index.h；experiments/02_diskann_fair/native/rabitq_bridge.cpp','对称建图与 C++/Rust 桥接；885 附近的 mode 分派'),
('experiments/02_diskann_fair/src/ours_diskann.rs:266；src/diskann_runner.rs:1703','residual4 参数与共享均值采样'),
('experiments/05_disk_system_fair/native_rust/src/ours_port.rs:119 / 237 / 501 / 613 / 843','页布局、导出、重排、页读取、主搜索循环'),
('experiments/05_disk_system_fair/native/query_page_cache.hpp','每 query 有界 LRU 与预算'),('Ours/config.json；experiments/05_disk_system_fair/README.md','配置与正式端口边界'),
('results/disk_environment/05_disk_system_fair/single_test_w32_20260911/agnews/Ours-Disk/result.json','本文唯一性能示例及完整状态字段')])
p('以上路径相对于 /home/kai3/coco/quantization_graph，行号对应本次读取快照。基础提交 7b8e792ecdf2965903bc55b4d892f76cac825306；工作区含未提交实现改动，因此该提交号不是本文全部源码的不可变快照。本次交付另保存关键源码 SHA-256，便于核查。没有修改算法实现，也没有将现有 test 行升级为正式结果。')
xml.append('<p>介绍结构参考：<a href="https://my.feishu.cn/wiki/ZtVCw6fdNiFvgAkvr6Yc80GgnJe">SAQ 逐步拆解：从 PCA 分段到 α 修正与距离估计</a>（读取 revision 10）。公式依据上述本地实现整理；不将代数推导冒称为新的理论保证。</p>')
md.append('介绍结构参考：[SAQ 逐步拆解](https://my.feishu.cn/wiki/ZtVCw6fdNiFvgAkvr6Yc80GgnJe)（revision 10）。')
source='\n'.join(xml)
ET.fromstring('<root>'+source+'</root>')
(OUT/'ours_disk_method.xml').write_text(source)
draft_path = ROOT/'draft_fff553b7_folder/draft.xml'
if draft_path.parent.exists():
    draft_path.write_text(source)
(OUT/'ours_disk_method.md').write_text('\n\n'.join(md)+'\n')
import hashlib
paths=['Ours/core/hnswlib/space_rabitq.h','Ours/core/hnswlib/vamana_index.h','experiments/02_diskann_fair/native/rabitq_bridge.cpp','experiments/02_diskann_fair/src/ours_diskann.rs','experiments/02_diskann_fair/src/diskann_runner.rs','experiments/05_disk_system_fair/native_rust/src/ours_port.rs','experiments/05_disk_system_fair/native/query_page_cache.hpp','Ours/config.json','results/disk_environment/05_disk_system_fair/single_test_w32_20260911/agnews/Ours-Disk/result.json']
(OUT/'source_manifest.json').write_text(json.dumps({p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},indent=2)+'\n')
print(OUT)
