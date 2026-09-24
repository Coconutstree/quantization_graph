"""Build readable, paper-first explainers; preserve the existing Feishu images.

Network publication is separate. Running this file only changes the two local
explainers and emits three text patches per document, divided by retained images.
"""
from html import escape
from pathlib import Path
import json
import re
import shutil
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def inline(s):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(s))


class Doc:
    def __init__(self, key):
        self.key = key
        self.short = key[:2]
        raw = json.loads((HERE / f"{self.short}_before.json").read_text())
        self.before = raw["data"]["document"]
        tree = ET.fromstring("<doc>" + self.before["content"] + "</doc>")
        self.title = "".join(tree.find("title").itertext())
        self.images = list(tree.iter("img"))
        assert len(self.images) == 2
        self.parts = [[]]
        self.md = ["# " + self.title]
        self.all_xml = ["<title>" + escape(self.title) + "</title>"]

    def add(self, xml, md):
        self.parts[-1].append(xml)
        self.all_xml.append(xml)
        self.md.append(md)

    def p(self, text):
        self.add("<p>" + inline(text) + "</p>", text)

    def h(self, text, level=2):
        self.add(f"<h{level}>{escape(text)}</h{level}>", "#" * (level + 1) + " " + text)

    def table(self, headers, rows):
        def cells(tag, row):
            return "<tr>" + "".join(f"<{tag}><p>{inline(str(v))}</p></{tag}>" for v in row) + "</tr>"
        xml = "<table><thead>" + cells("th", headers) + "</thead><tbody>"
        xml += "".join(cells("td", row) for row in rows) + "</tbody></table>"
        md = "| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n"
        md += "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows)
        self.add(xml, md)

    def steps(self, steps):
        self.add("<ol>" + "".join("<li>" + inline(s) + "</li>" for s in steps) + "</ol>",
                 "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))

    def source(self, text, url):
        self.add(f'<p><a href="{escape(url, quote=True)}">{escape(text)}</a></p>', f"[{text}]({url})")

    def figure(self, index):
        img = self.images[index]
        # Reuse the remote token in the complete XML source; publishing leaves
        # the existing resource block untouched, including its block ID.
        label = "论文方法总览" if index == 0 else "我们的磁盘存储与读取顺序"
        self.all_xml.append(f'<img src="{img.attrib["src"]}" caption="{label}"/>')
        if index == 0:
            path = f"figures/{self.key}_flow.png"
        else:
            path = f"readable_revision_20260917/{self.short}_storage_remote.jpg"
        self.md.append(f"![{label}]({path})")
        self.parts.append([])

    def save(self):
        assert len(self.parts) == 3
        for suffix in ("md", "xml"):
            old = ROOT / f"{self.key}.{suffix}"
            backup = HERE / f"{self.short}_local_before.{suffix}"
            if not backup.exists():
                shutil.copyfile(old, backup)
        for i, part in enumerate(self.parts):
            (HERE / f"{self.short}_part{i}.xml").write_text("\n".join(part) + "\n")
        (ROOT / f"{self.key}.md").write_text("\n\n".join(self.md) + "\n")
        (ROOT / f"{self.key}.xml").write_text("\n".join(self.all_xml) + "\n")


d = Doc("04_og_lvq")
d.p("**先记住一句话：OG-LVQ 把向量压缩后放在内存里，沿着图找邻居，用压缩后的向量计算距离。我们的磁盘版又把图和压缩向量搬到了 SSD，读取方式因此发生了变化。**")
d.p("本文仍分两部分：先按“全流程 → 编码 → 建图与查询 → 误差”的顺序解释原论文，再简述我们的磁盘改动。例子中的小向量只用于说明计算，不是实验数据。更新于 2026-09-17。")
d.h("第一部分：论文中的 OG-LVQ 怎么做", 1)
d.h("一、先看全流程：谁负责找路，谁负责算距离")
d.p("一条数据用一串数字表示，这串数字就是“向量”；用户这次要找的内容也表示成一个向量，叫“查询向量”。目标是找到离查询向量最近的若干条数据。")
d.table(["名称", "在这里负责什么"], [
    ("图", "每条数据记住一小份邻居名单。搜索顺着这些连接走，避免把全库都算一遍。"),
    ("LVQ", "把向量中的小数变成短整数编号，减少存储和读取的数据量。"),
    ("OG-LVQ", "论文把优化后的图搜索与 LVQ 放在一起得到的完整方法。"),
    ("候选池", "这次查询暂时保留的一份近邻名单。里面的点未必都已经查看过邻居。"),
])
d.p("一次查询的大致过程是：**从入口出发 → 计算邻居离查询有多远 → 保留较近的候选 → 继续查看候选的邻居 → 返回结果。**LVQ 决定这些距离怎样计算。")
d.p("读下图时按编号 1 到 6 看：前四步是查询前的准备，后两步是查询时的工作。“残差”是压缩后丢掉的那一点数值差，第三节会用数字解释。")
d.figure(0)
d.h("二、步骤 1：先把所有向量减去同一个均值")
d.p("先计算整个数据库的平均向量，再让每条数据都减去它。比如平均向量是 [10, 100]，一条数据是 [11, 102]，减完就得到 [1, 2]。查询向量也用同一个均值处理。")
d.p("这样做相当于一起移动坐标原点。两点都减去同一个数，它们的差不变，所以这一步不会改变真实距离。它的作用是减少各维本身的固定偏移，方便后面压缩。")
d.h("三、步骤 2：每个向量用自己的刻度，把小数变成编号")
d.p("以下用 **LVQ4** 举例：每个坐标用 4 bit，能保存 0～15 共 16 个编号。对一条已经减过均值的向量，先找出它自己最小、最大的坐标，再在这个范围内放 16 个等距刻度。")
d.p("例如向量是 [−1, 0.34, 1.12, 2]。最小值是 −1，最大值是 2，所以刻度间隔为 (2 − (−1)) ÷ 15 = 0.2。每个坐标保存最近刻度的编号：")
d.table(["原来的坐标值", "保存的编号", "根据编号恢复的近似值"], [
    ("−1", "0", "−1 + 0 × 0.2 = −1"),
    ("0.34", "7", "−1 + 7 × 0.2 = 0.4"),
    ("1.12", "11", "−1 + 11 × 0.2 = 1.2"),
    ("2", "15", "−1 + 15 × 0.2 = 2"),
])
d.p("因此，这条向量保存的是 [0, 7, 11, 15]，再加上恢复数值所需的起点和间隔。四个坐标的编号只占 2 字节；范围参数也要占空间，不能只算编号。实际高维向量更容易摊薄这部分额外开销。")
d.p("**“局部自适应”指每个向量都有自己的刻度。**另一条向量如果只落在 [−0.1, 0.2]，它的间隔就是 0.02。同样编号 7，在两条向量中表示的数值可以不同。因此 LVQ 不能把不同向量的编号直接相减来算距离。")
d.h("四、步骤 3：可选地再存一点误差，让最后排序更准")
d.p("刚才 0.34 被恢复成 0.4，二者差值是 −0.06。这个“原值 − 恢复值”就叫残差。论文可以用第二层短码再压缩这点残差。")
d.p("搜索大部分节点时，只使用第一层码；最后只对留下来的候选读取第二层码，把恢复值修正得更接近原值，再排一次序。例如把第一层恢复值 0.4，加上第二层给出的近似残差，就会更接近 0.34。")
d.p("名称 **LVQ-4×8** 表示每坐标第一层 4 bit、第二层 8 bit。第二层需要额外存储，也需要额外计算。论文包含不同位宽的单层和双层配置；我们的磁盘实验只用了单层 LVQ4。")
d.h("五、步骤 4：用压缩向量建立图")
d.p("图可以理解为“每条数据的一份邻居名单”。论文采用 Vamana 式建图：为一个点寻找一批邻居候选，删掉部分作用重复的连接，并限制每个点保留的边数。对全库重复这个过程，得到可以搜索的图。")
d.p("这些建图步骤也需要大量距离计算。论文分析了量化误差对构图的影响，并通过实验研究使用 LVQ 压缩向量构图的可行性，从而减少建图期间向量占用的内存。这里的图仍然负责导航，LVQ 负责提供计算距离的紧凑表示。")
d.h("六、步骤 5：查询时保存什么、怎样算一个距离")
d.p("原论文针对内存检索。主要保存：邻居名单、入口、每条向量的第一层码及其刻度、全库均值；双层版本还保存残差码。查询时另外开一份候选池。")
d.p("**查询向量保持浮点数；数据库向量按需要从短码恢复数值。**距离计算可以一边恢复一边累加，不用提前解压整个数据库。")
d.p("沿用前面的例子，假设减过均值的查询向量是 [−1, 0.5, 1, 2]，数据库向量恢复后是 [−1, 0.4, 1.2, 2]。逐坐标相减、平方后相加：0² + 0.1² + (−0.2)² + 0² = 0.05。这就是用来比较远近的近似分数，越小越近。")
d.h("七、步骤 6：顺着图搜索，最后返回结果")
d.steps([
    "先给入口算一个距离，把它放入候选池。",
    "从候选池中挑距离较近、还没查看过邻居的点。查看它的邻居名单，这个动作叫“展开节点”。",
    "给这些邻居算距离，更新候选池；池子满了，就淘汰距离较大的候选。",
    "重复上两步，直到候选池里没有需要继续展开的点。",
    "单层版本按现有分数返回；双层版本先用残差码重新评分，再返回前 k 个。k 就是用户要求的结果个数。",
])
d.p("**候选池容量决定同时保留多少条搜索线索，不等于总共只访问这么多个点。**例如容量是 40、最终返回 10 个，搜索中可以不断淘汰旧候选、加入新候选，所以累计评分数可以远大于 40。")
d.h("八、为什么能更快，又为什么会漏掉近邻")
d.p("压缩后，每次从内存搬来的数据更少；论文还利用一次处理多个坐标的 CPU 指令和提前读取数据来加速。查询向量没有被压缩，并不意味着距离就是精确的：数据库向量已经变成近似值，候选的先后顺序可能改变；有限候选池也可能过早丢掉有用的路径。")
d.p("第二层残差能改善已找到候选之间的排序，不能补回搜索时从未找到的点。")
d.source("原论文：Similarity search in the blink of an eye with compressed indices，§3–5", "https://www.vldb.org/pvldb/vol16/p3433-aguerrebere.pdf")
d.h("第二部分：我们的磁盘实验改了什么", 1)
d.p("我们先用 SVS 构建并保存图与单层 LVQ4 编码，再把邻居名单和压缩向量分别放进两个磁盘文件。全库均值、索引说明和查询工作区留在内存。查询需要哪条记录，就读取它所在的 4 KiB 页；本次查询读过的页可以缓存复用。")
d.p("下图只需先看三列：左边是内存，中央是磁盘，右边是读取顺序。graph.pages 保存邻居名单，lvq4.pages 保存压缩向量；“码行”就是一个向量的编号和恢复参数。")
d.figure(1)
d.h("一、搬到磁盘后，一次查询这样走")
d.steps([
    "取入口的 LVQ4 记录，算距离，放入候选池。",
    "选择要展开的点 u，读取它的邻居名单。",
    "对需要评分的邻居 v，先取得 v 的 LVQ4 记录，再算距离、更新候选池。所在页没有缓存时，这一步需要读 SSD。",
    "继续搜索，最后按 LVQ4 分数返回前 10 个；当前配置没有第二层残差重排，也没有原始浮点向量重排。",
])
d.p("**主要变化发生在第 3 步：为了判断邻居值不值得继续搜索，就可能要先读盘。**例如展开 u 后发现了 64 个邻居，需要访问相应的编码记录；但读取次数不一定是 64，因为多条记录可以在同一页，已经缓存的页也不用再读。DiskANN 的全库 PQ 码在内存，这类邻居评分通常不需要为编码另读 SSD。")
d.h("二、这版实现能代表什么")
d.p("当前磁盘查询的距离计算仍是本地解码实现，没有完成与官方优化距离内核的一致性核验，因此 OG-LVQ 的正式实验准入仍被阻塞。它的历史曲线反映的是这版移植实现，不能据此断言原论文 OG-LVQ 本身比 DiskANN 慢。")
d.p("原存储图中的 seen 是旧版标注：当前源码使用官方 SearchBuffer 候选队列，默认不启用独立的 visited 去重过滤。本文保留原图资源，以此处说明为准。")
d.p("核验位置：experiments/05_disk_system_fair/native/og_lvq_disk_port.cpp 的 lvq_distance 与 search_one；docs/analysis/baseline_algorithm_audit_20260916/README.md。本文改写没有运行新实验。")
d.save()

d = Doc("05_glass_nsg")
d.p("**先记住一句话：NSG 决定每个点应该连向谁，让搜索沿着少量有用的边前进。我们使用 Glass 库实现它，再用 SQ4U 压缩向量，最后把图和编码搬到 SSD。**")
d.p("本文先按“全流程 → 建图 → 存储与查询 → 误差”的顺序讲 NSG 原论文，再解释 Glass、SQ4U 和磁盘读取。图中的小例子只用于说明原理，不是实验结果。更新于 2026-09-17。")
d.h("第一部分：论文中的 NSG 怎么做", 1)
d.h("一、先分清 NSG、Glass、SQ4U")
d.table(["名称", "它是什么", "负责什么"], [
    ("NSG", "论文提出的图方法", "决定邻居连接，让查询少走弯路。"),
    ("Glass", "实现近邻搜索的程序库", "提供建图、编码和搜索代码。"),
    ("SQ4U", "我们选择的 4 bit 编码方式", "把向量压缩成短整数，用于计算近似距离。"),
])
d.p("所以，Glass-NSG-SQ4U 是一个实现组合。**NSG 原论文的核心是怎样建图，SQ4U 的压缩步骤属于我们所选的库配置。**第一部分先用原始向量的欧氏距离解释 NSG。")
d.p("这里“图”就是一份份邻居名单：每条数据是一个点，名单上的邻居对应它连出的边。查询从固定入口开始，计算邻居到查询的距离，优先继续搜索较近的候选。")
d.p("读下图时按 1 到 6 看：前五步准备好图，最后一步才是用户发来查询后的搜索。下面逐步解释这些连接怎样选出来。")
d.figure(0)
d.h("二、步骤 1：先给每个点找一批附近的点")
d.add('<p>先建立一张近似 kNN 图：对每个点，找 k 个大致较近的点作为邻居。kNN 的意思就是“k 个最近邻”。<span background-color="light-yellow" text-color="red"><b>这张初始图只提供候选连接，后面还要重新筛选，才得到最终的 NSG。</b></span></p>',
      '先建立一张近似 kNN 图：对每个点，找 k 个大致较近的点作为邻居。kNN 的意思就是“k 个最近邻”。**这张初始图只提供候选连接，后面还要重新筛选，才得到最终的 NSG。**')
d.p("原因是：一个点最近的许多邻居可能集中在同一小片区域。把连接机会都用在这些点上，搜索仍然可能绕远路。NSG 希望留下数量较少、能通向不同位置的有用连接。")
d.h("三、步骤 2：选一个所有查询共用的入口")
d.p("计算数据库的平均向量，再在初始图中搜索一个靠近它的真实数据点，把这个点作为入口。平均向量用来帮助找入口，入口本身仍然是数据库里的一条数据。")
d.h("四、步骤 3：给每个点收集更有用的邻居候选")
d.p("假设现在要给点 u 选邻居。暂时把 u 当成查询目标，从入口沿初始图搜索它。搜索途中经过的点，加上 u 原来的近邻，组成待选名单。")
d.p("这样待选名单里既有 u 附近的点，也有从入口接近 u 时经过的点。后者可能提供有用的导航连接。这个过程要对每个数据点执行一次。")
d.h("五、步骤 4：删掉作用重复的边，保留有用的方向")
d.p("按离 u 从近到远处理待选点，先保留最近的一个。如果已有邻居能更靠近另一个待选点，就不再给 u 加这条直接连接；否则保留它，直到达到边数预算或候选用完。")
d.p("用一个二维例子看这条规则：u=(0,0)，待选点 A=(1,0)、B=(2,0)、C=(0,2)。先保留最近的 A，然后检查 B 和 C：")
d.table(["待选点", "与已经保留的 A 比较", "对 u 的选边结果"], [
    ("A", "离 u 最近，距离为 1。", "保留 u→A。"),
    ("B", "A 到 B 的距离是 1，小于 u 到 B 的距离 2。", "不保留 u→B；A 已提供一个更接近 B 的中继位置。"),
    ("C", "A 到 C 的距离约为 2.24，大于 u 到 C 的距离 2。", "保留 u→C；仅靠 A 不能替代这条连接。"),
])
d.p("最终 u 在这个例子中连向 A 和 C。这个规则倾向于避免把连接都花在相似方向上；它不会自动添加 A→B，整张图能否从入口走通还需要下一步检查。")
d.h("六、步骤 5：检查有没有从入口到不了的点")
d.p("选完边后，从入口顺着有向边遍历整张图。如果还有一片区域到不了，就添加连接，再继续检查，直到所有点都能从入口到达。这个步骤叫可达性修复。")
d.p("“有路可达”表示连接关系允许走过去。实际查询只查看有限数量的候选，所以仍然可能没走到真正的近邻。")
d.h("七、步骤 6：图建好后，一次查询怎么走")
d.p("索引保存原始向量、每个点的邻居名单和入口。查询时维护一个有容量限制的候选池：它是一份“目前找到的较近点”的名单，距离小的排在前面，并记录哪些点已经查看过邻居。")
d.steps([
    "从入口开始，计算它到查询向量的距离。",
    "挑一个较近且尚未展开的候选，查看它的邻居名单。",
    "计算新邻居到查询的距离，加入候选池；容量不够时淘汰较远的点。",
    "继续展开候选，直到池中没有需要继续处理的点，再返回最前面的 k 个结果。",
])
d.p("例如继续使用前面的 u、A、C，令查询 q=(2,2)。它们到 q 的平方距离分别是 8、5、4，所以发现 A、C 后会优先展开 C。假设 C 又连着 D=(1,2)，D 的分数是 1，搜索就找到一个更近的候选。距离分数按“坐标差的平方相加”计算，越小越近。")
d.p("搜索并非永远只走一条路：候选池还保留 A 等备用候选。容量 L 越大，能同时留下的搜索线索越多，通常也需要更多计算。L 是搜索候选池容量，R 是建图时每个点的出边预算，k 是最终返回个数，三者不同。")
d.h("八、NSG 的收益和误差来自哪里")
d.p("NSG 希望用较少的边和较短的搜索路径找到近邻，从而减少距离计算。原论文这一流程使用原始向量评分，近似主要来自图连接和有限范围的搜索：没有被搜索到的点，就不会出现在结果中。")
d.source("原论文：Fast Approximate Nearest Neighbor Search with the Navigating Spreading-out Graph，Algorithm 1、2", "https://arxiv.org/abs/1707.00143v9")
d.h("第二部分：我们的磁盘实验改了什么", 1)
d.h("一、先用 Glass 建 NSG，再选择 SQ4U 算距离")
d.p("实验配置名 NSG_R64_L400_SQ4U 表示使用 NSG 图、出边预算 64、建图搜索宽度 400，以及 SQ4U 编码。这里的 400 是建图参数，查询时还会单独设置搜索宽度。")
d.p("SQ4U 用一套共享刻度，把数据库向量的每个坐标变成 0～15 的编号；查询向量也使用同一套刻度。与 LVQ 每条向量单独定刻度不同，SQ4U 的刻度由训练数据共同确定，超出范围的值会截到边界。")
d.p("教学例子：假设共享范围是 [0,3]，刻度间隔为 0.2。查询的一个坐标 0.34 编成 2，数据的一个坐标 0.72 编成 4；它们在这一维的整数距离贡献是 (2−4)²=4。所有维的贡献相加，就得到排序分数。因为所有点使用同一刻度，可以直接比较这些整数分数。")
d.p("这算的是两端压缩后的近似距离。例子中两个坐标实际被当成了 0.4 和 0.8；乘上共同的间隔平方后，得到的是它们的平方距离，而不是原来 0.34 与 0.72 的精确距离。")
d.h("二、把邻居名单和压缩向量分别放到磁盘")
d.p("图放在 graph.pages，SQ4U 编码放在 sq4u_codes.pages；共享刻度、入口、候选池和查询页缓存留在内存。每次磁盘读取以 4 KiB 页为单位，一个页可以放多条记录。下图的“码行”就是一个向量的压缩编号。")
d.figure(1)
d.h("三、实际查询顺序，以及增加的读盘开销")
d.steps([
    "先把查询向量转成 SQ4U 编号。",
    "取得入口的编码，计算整数距离，放入候选池。",
    "选择要展开的点 u，读取它的邻居名单。",
    "对还没有评分过的邻居 v，取得 v 的编码，计算整数距离并更新候选池。编码所在页没有缓存时，需要读 SSD。",
    "重复搜索，最后按整数距离返回前 10 个；当前配置没有用原始浮点向量再排一次序。",
])
d.p("**短码减少了索引大小，但不保证一次评分只从磁盘读取几个字节。**缓存未命中时仍然要读一整页；图页和编码页分开保存，也需要分别取得。因此，实际性能既取决于 NSG 找路的效率，也取决于候选编码分布在哪些页上。")
d.h("四、当前实现与历史曲线的区别")
d.p("当前源码复用 Glass 的整数距离计算和 LinearPool 候选队列。此前本地队列处理相同距离候选的顺序与官方不同，已经在源码中修正；小规模对照通过，但旧性能二进制和历史曲线没有因此自动更新。正式准入仍需要完成，不能把旧曲线当作修正后实现的结果。")
d.p("核验位置：experiments/05_disk_system_fair/native/glass_disk_port.cpp 的 search_one；baselines/pyglass/glass/quant/sq4u_quant.hpp、calibrator.hpp；docs/analysis/baseline_algorithm_audit_20260916/README.md。本文改写没有运行新实验。")
d.save()
print("Rebuilt OG-LVQ and Glass-NSG; six text patches generated; existing image blocks will be preserved.")
