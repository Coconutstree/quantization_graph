# ExRaBitQ-Disk：SIGMOD 论文工作稿

## 交付与定位

英文正文见 [manuscript.md](manuscript.md)，ACM 双栏源文件见 [main.tex](main.tex)，参考文献见 [references.bib](references.bib)。这是方法与论证完整、性能结果明确待补的工作稿，不是可直接提交的最终版。原始实验安排和算法实现没有被改写，也没有启动新的训练或性能实验。

来源为用户指定飞书文档 revision **148**、本次读取的 `experiments.md`、核心实现及本地审计说明。原文快照保存在 `sources/feishu.json` 和 `sources/feishu.md`；源码与实验安排的哈希见 `sources/source_manifest.json`。当前工作区已有未提交修改，因此仅记录 Git commit 不足以固定本稿的方法版本。

使用 nature-writing；轴为 `manuscript / algorithmic / 全文 / zh-to-en / generic（以 SIGMOD 规则覆盖期刊格式）`。主读者为数据库系统与 ANN 审稿人。技术问题主导引言：有限内存下，分级估计如何在读盘之前决定候选是否值得访问。

**一句话论证：** ExRaBitQ-Disk 将内存中的 1-bit 筛选、4-bit 量化构图与遍历、残差重排及页级读取结合起来，以控制磁盘 payload 的访问；是否带来 recall-matched 系统收益，由三层正式实验验证。

## 章节安排与写作依据

1. Introduction：磁盘场景、现有方法、具体缺口、贡献与边界。
2. Problem and Design Rationale：先定义候选与物理页的区别，再解释分级精度的作用。
3. Method：表示 → 对称构图 → 1-bit gate → 遍历与重排 → 物理布局。
4. Analysis：对称距离误差、局部剪枝稳定条件、查询误差与搜索遗漏、成本。
5. Experimental Methodology：严格对应 experiments.md 的 01/02/03（内部协议仍为 05a/05b/05c） 和内存敏感性。
6. Results：结果位置、验收要求和 E1–E5 待补项，不伪造实验结论。
7. Related Work / Discussion：准确归属已有工作，给出方法失败条件与范围。

参考 [SymphonyQG 原文](https://arxiv.org/html/2411.12229v1) 的“具体瓶颈—表示和执行协同—构图/查询—端到端与消融”论证组织。参考 [SAQ 原文](https://arxiv.org/html/2509.12086v1) 的“动机—编码—估计器—分析—实验”技术展开。没有复用其句子、性能数字或保证，也没有把它们的已有思想写成 Ours 首创。SAQ 已有渐进、多阶段估计，因此“使用多阶段估计”不能单独作为新颖性主张。

## 飞书到论文的必要修正

| 飞书表述或隐含结论 | 本稿处理 | 原因 |
| --- | --- | --- |
| 方法名 Ours / Ours-Disk 混用 | 正文统一 ExRaBitQ-Disk；实验标签保留在说明里 | revision 148 明确命名 |
| 对称距离构图和搜索 | 离线为两端编码对称比较；在线为 FP/INT8 query 对数据库码 | 两个估计端点不同 |
| INT8 在最终 residual rerank 复用 | 更正为最终 FP query 主码重算 + FP residual 内积 | `OursPreparedQuery::rerank` 的实际调用链 |
| 4-bit payload 即全部存储 | 区分主码、residual4、scales、factors、padding、DB1 副本 | 普通布局每条 `9d/8+25` 字节，未含页填充和图 |
| 05B 都是 shared graph | PQ/SQ/SAQ 共享 baseline 图；Ours 各消融共享自有图 | 当前 experiments.md 的明确约束 |
| no-gate resident 与主线差值就是 gate 收益 | 标记为复合对照；补同 residency、同 query codec 的 gate-only 对照 | 当前开关也改变存储与评分路径 |
| gate lower_bound 是确定性安全下界 | 正文称 screening score，解释适用边界 | INT8 误差、快速旋转与自适应阈值未提供整条搜索证明 |
| page reuse 意味着每页永远只读一次 | 改为有界缓存、可能逐出并重读 | 当前 query-local LRU |
| 1-bit AGNews 约 0.95 GiB | 不沿用；`N*(d/8+20)+4096=113872632` 字节，约 108.6 MiB | 飞书数值与实际 DB1/factor 口径不符；此计算仍不是完整 search memory |
| 剩余约 1 GiB 可以直接用于缓存 | 先核算所有 worker、visited、临时批次等，再决定剩余容量 | residency 不等于完整预算使用 |
| 202.13 QPS、0.9944 recall、16 倍 I/O 降幅 | 不放入摘要或正式结果 | 不能把不同 width 的极值和均值拼成同一个 operating point；当前 artifact 口径也未验收 |
| 99.43% 时间是纯磁盘等待 | 暂不作为机制事实 | 历史 `io_wait_us` 可能包含查页、分配、复制和解码 |
| AGNews 主图 ready | 重新按当前规范核验，不按旧标题放行 | `formal_ready` 标签与旧文件名不能替代完整验收 |
| 最新 locality / adaptive 分支属于主方法 | 排除在默认公式之外，仅在 Discussion 指明需单独验证 | 用户飞书主线与实验规范未确定其作为正式配置 |

## 术语表

| Canonical term | 定义与使用 |
| --- | --- |
| ExRaBitQ-Disk | 论文方法名；对应 Ours-Disk |
| primary code | ExRaBitQ4 的 4-bit 主码 |
| one-bit gate / DB1 sidecar | 从主码 MSB 派生的筛选层 |
| residual4 / residual correction | 每块16维、FP16尺度的4-bit残差编码 |
| symmetric construction score | 两个数据库端点均编码的构图评分 |
| screening score | 代码称 lower_bound 的实际经验/建模筛选值 |
| payload-page demand | 页需求；与候选数、I/O请求数分开 |
| query-local reuse | 同 query 的有界页缓存复用 |
| C0 | 无跨 query 缓存；不是没有任何 query 内工作区或缓存 |
| search-index DRAM / process peak RSS | 分别报告，不能互相替代 |

## Claim–evidence map

| Claim | Evidence | Status |
| --- | --- | --- |
| 候选在读 payload 前接受 DB1 筛选 | `search_ours_disk_graph`，gate 在 `read_records` 前 | supported：实现结构 |
| 主码内积可复用 MSB 部分 | `k=8b+l` 的代数分解与 mode=1 距离路径 | supported：代数与实现 |
| 普通布局残差随主码一起读取 | payload layout 与 `read_records` | supported：实现结构 |
| 比值主码误差与原向量正交 | 本稿第3.2节直接推导 | supported：理想算术、非退化前提 |
| 对称距离误差包含两端及交叉项 | 本稿第4.1节展开；数值自检 | supported：推导，不是新概率定理 |
| 单个剪枝比较在足够 margin 下不翻转 | 误差三角不等式；固定候选集前提 | supported：局部充分条件，非整图保证 |
| gate 减少实际读取且不显著损失召回 | 同图、同codec、同residency开关对照 | needs evidence：E4 |
| 自有量化图改善质量/构建时间 | 固定 query pipeline 的构图对照 | needs evidence：E4 |
| 比五系统在相同 recall 更快、I/O 更低 | 32-worker、同预算同缓存、独立test正式曲线 | needs evidence：E1 |
| 跨规模、跨数据集效果稳定 | 验收后的 core + extension 结果 | needs evidence：E1/E5 |
| “纯 I/O wait”占主导 | 非重叠计时或内核级等待证据 | needs evidence：E2 |

## 待补输入与实验

| 编号 | 需要补充的具体内容 | 对应正文 |
| --- | --- | --- |
| E1 | core正式 test 曲线与五系统；各目标recall同点QPS/p95/bytes/requests；必要时扩展数据集 | Abstract、Introduction、6.1、Conclusion |
| E2 | 运行机器与存储设备/介质/控制器、实际测试数、warm-up、时长、工具链、数据来源与模型、构图与export分时 | 5.1、5.2、6.3 |
| E3 | 05A具体评分端点是否包含residual，实际完整record和nominal4bit含义；精度/bytes结果 | 5.3、6.2 |
| E4 | 四级消融；真正gate-only对照；固定query pipeline的fp32/symmetric建图对照；所有实际计算计数 | 5.4、6.2 |
| E5 | 全部search memory计量；C0及至少三档可行B；必要时通过验收的streaming fallback | 5.6、6.3 |

飞书提到“八个选六个”，但 experiments.md 当前明确为3个core+5个extension；因此正文列出全部计划候选，绝不声称8个都跑完。最终纳入集合应按预定准入规则和验收情况确定，不按哪几个结果更好来挑选。

## 主图主表配置

| 编号 | 内容 | 状态与素材 |
| --- | --- | --- |
| Figure 1 | 离线表示/构图与在线三阶段、DRAM/磁盘边界 | `main.tex` 提供矢量 TikZ 结构草图；需编译后视觉复核 |
| Figure 2 | fixed-candidate主码误差、effective bits和bytes/query | E3；不要直接放旧单worker图 |
| Figure 3 | core数据集recall–QPS，配套recall–MiB/query | E1；曲线来自同一正式run |
| Figure 4 | 同一Ours图的gate/coalescing/reuse消融 | E4；同时报告recall与读取 |
| Figure 5 | C0与三档预算敏感性 | E5；不同预算分面展示 |
| Table 1 | 数据集与实际query数 | 当前提供计划规模；query数待填 |
| Table 2 | 构图/编码/export、disk bytes、search DRAM、RSS | E2/E5；分清from-scratch与reused |

现有 `paper/figures` 和 `docs/analysis` 图没有被自动合入：它们可能来自不同线程、分支、计时范围或旧run。结构图不属于实验结果，可独立完成；结果图必须跟随验收后的数据。

## Main-text discipline audit

- core：recall-matched完整系统收益；必要支持：05A、同图消融、构图比较；qualification：无确定性gate保证、residual真实字节和未验收结果，都留正文。
- provenance：输入和binary哈希、旧结果诊断及逐文件修正记录放本说明和sources，不挤入正文。
- 统计：主文使用单次QPS与query-level p95；不伪造median-of-5、跨run置信区间。历史I/O分解不当作纯等待。
- 结果部分从中文大纲重新成稿，无同语言before-word-count可比；英文分节词数随 `validation.json` 保存。
- 主张在Introduction承担提出作用，在Analysis解释边界，在Results列验证任务，在Conclusion综合；没有重复历史最大值填充段落。

## SIGMOD 格式

未指定目标年份，按本次查到的 [SIGMOD 2027 Research CFP](https://2027.sigmod.org/calls_papers_sigmod_research.shtml) 设置：投稿正文最多12页，参考文献不限页，ACM sigconf双栏、Letter、双匿名，PDF不超过10 MB；appendix需另交PDF且正文自足。投稿/修订不使用PACMMOD版式。

`main.tex` 使用 `acmart` 的 `sigconf,anonymous`，没有作者姓名或单位。正文内容会由 `render_latex.py` 从 `manuscript.md` 生成，避免两份正文漂移。提交前需把工作稿占位项替换成证据，而不是直接删除以掩盖缺少实验。文献当前对多篇论文引用已核验的arXiv版本，故年份是预印本年份；没有编造正式卷期、页码或把SAQ原文里的模板DOI当真。PQ发行元数据与原文获取状态分别记录在 `sources/literature_notes.md`。

## 无 sudo 的本地编译与 VS Code 预览

项目已配置本地 Tectonic 编译器：`work/latex/bin/tectonic`（0.17.0，官方 Linux x86_64 musl 发行版）。模板、字体缓存位于 `work/latex/cache`。这些运行依赖保存在被 Git 忽略的 `work/` 下，不修改系统安装；初次编译或添加新宏包可能需要联网下载公开依赖，论文在本地编译。

服务器已有 LaTeX Workshop 扩展。打开 `main.tex` 后按 `Ctrl+Alt+B` 编译，再按 `Ctrl+Alt+V` 预览PDF。若快捷键冲突，使用命令面板里的 `LaTeX Workshop: Build LaTeX project` 与 `LaTeX Workshop: View LaTeX PDF file`。编译配方为 `SIGMOD: Markdown -> Tectonic (no sudo)`，配置位于项目 `.vscode/settings.json`。

终端可执行：

```bash
python3 paper/sigmod_draft/build.py
```

该入口先将 `manuscript.md` 转换为 `main.tex/body.tex`，再生成 `paper/sigmod_draft/main.pdf`，因此修改Markdown后无须手动执行转换。若直接编辑LaTeX，转换会覆盖生成的正文；长期仍以Markdown为正文来源。自动保存编译关闭，手动编译即可。

本次已成功编译为8页PDF，参考文献已解析，并在依赖缓存就绪后完成了无联网权限的再次编译。仍有约1–2pt的轻微段落溢出提示；当前Tectonic默认包中的acmart为v1.83（2022），可用于本地预览，正式投稿前还应以目标会议要求的最新模板做一次排版核验。

另一个具有完整TeX环境的机器仍可执行 `latexmk -pdf main.tex`。编译成功只表示可预览，不代表 E1–E5 证据占位已经完成或符合最终投稿标准。

## 定向修改入口

若需调整贡献重心，优先指出“第3.3节对称构图”或“第3.4节gate”哪一部分应作为主贡献；其余已核对机制和实验边界可保持不变。

当前图号与来源对照见 `../FIGURE_PROVENANCE.md`。正文实验编号已同步为 01/02/03；冻结原始证据中的历史 ID 保持不变。本次未重新编译 main.pdf，因此已有 PDF 仍是之前的编译快照。
