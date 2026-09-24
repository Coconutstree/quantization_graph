# Ours 核心方法图设计与代码核查

状态：已按本设计生成原生可编辑 draw.io 与 SVG/PDF；2026-09-21。图号 FIG-02 / Figure 2。

交付目录：`figures/ours-core-overview/`。最终图注见 `caption.md`，生成与导出方式见 `README.md`，审查见 `qa.md` / `qa.json`。下方保留设计约定；生成阶段实际排版以交付图为准。SVG/PDF 从同一 draw.io XML 经本地原生形状渲染器导出，当前环境无 draw.io Desktop CLI。

## 图的唯一主张

从数据库 primary 4-bit 表示中抽取同源 1-bit MSB 平面，在 DRAM 中对图搜索产生的候选进行低成本非对称筛选；对通过筛选的候选使用 SSD 中的完整 primary 4-bit 做导航距离估计，最后以 residual 4-bit 精化候选集排序。

目的：把有限 DRAM 用于广覆盖的粗筛选，把较昂贵的精细访问和计算集中到有希望的候选。减少 I/O、维持 Recall 是待实验支持的目标，不在示意图中承诺无损或固定倍数提升。

这里提炼的是方法贡献的表达，不是已完成相关工作检索的新颖性判定。本次仅查阅本地实现、设计说明、实验驱动及已有报告，未运行新性能实验。

## 三个应展示的核心思想

1. **同源表示复用**：`Primary 4-bit → extract MSB → 1-bit routing`。1-bit 是 primary 的视图，不是独立训练的量化器；primary 仍完整保存，不能画成 SSD 只剩 3-bit。
2. **让粗筛选控制精细访问**：DRAM 的 1-bit sidecar 和少量 factors 对新图邻居候选做筛选；幸存候选触发精细 payload 访问。判断单位是节点/向量候选，实际 I/O 单位是页面。
3. **按职责逐级使用精度**：1-bit 用于 gate；完整 primary 4-bit 用于候选池排序及后续图扩展；primary + residual 用于最终候选集 rerank。1-bit 不是最终排名距离。

不将这些画成三个独立算法框。它们共同解释同一份数据库表示如何跨内存层级服务搜索。

## Panel 布局与视觉叙事

横向双栏宽方法图，暂定宽约 178 mm、高约 80–90 mm，最终服从投稿模板。三块宽度约为 28% / 47% / 25%。中央 (b) 为视觉主角；画面主标题放在 caption 中。推荐原生 draw.io，最终 PDF/SVG，保留文字与连线可编辑。

### (a) One primary code, two views — 表示来源

读者要理解：DRAM 的 1-bit 从哪里来。

对象：
- 一个数据库向量标签 `Database vector x`，可用小型实值列表示，不展开训练流水线。
- 四个示意维度的 primary 4-bit 小方格组：`1010 | 0011 | 1110 | 0101`。每组左侧最高位用蓝色强调，余下三位淡蓝。
- 一条从四组最高位引出的汇聚线，指向 `1 | 0 | 1 | 0`，标 `Extract MSB (1 bit/dim)`。
- 1-bit 行旁加一个小 metadata 标签 `+ scale / norm factors`。
- primary 完整四位组旁标 `Primary 4-bit retained on SSD`。

箭头：
- `Primary 4-bit → Extract MSB → DRAM routing` 必须是直接可追踪的最强表示派生关系。
- primary 四位整体连到 (b) SSD 内的完整 primary 区，1-bit 行连到 (b) DRAM sidecar。
- 用细的派生/存放箭头区别于查询时读取箭头；不要误画成每条 query 都从 SSD 提取 MSB。

文字：`Derived from primary; no separate 1-bit quantizer` 可作短注。码字只表示位结构，不是实测样本或原始向量量化结果。

不出现：独立的 1-bit codebook、二次训练模块、Hamming 搜索、SSD “remaining 3-bit only”、residual 与 primary 低三位的混同。

### (b) Screen in DRAM, access selectively — 中央主图

读者要理解：这一份低比特信息如何避免不必要的精细访问。

画面分为上部浅蓝 `DRAM` 与下部浅灰 `SSD`，中间用清晰水平边界。计算操作放在 DRAM/CPU 一侧，SSD 只画记录与页。

对象：
- 上侧入口 `Real-valued query q`，用实值小列表示；不画 1-bit query。
- DRAM 内 `Derived 1-bit codes + factors` 小型矩阵。
- 6 个示意图邻居候选，例如 v1…v6，连接到一个当前扩展节点，标 `New graph neighbors`。沿用这些 ID 到下方 SSD 记录，避免无语义方框。
- 候选中 2 个保留饱和颜色，其他候选淡灰并带短斜杠；旁注 `Illustrative candidates` 或在 caption 说明数量不表示实测筛选率。
- 一个很小的 `Asymmetric screening` 评分符号，接收 query、对应 1-bit code 和 factors。
- SSD 页中展示 `Primary 4-bit + metadata` 记录；residual 区只做小型提示，细节交给 (c)。用小标签承认 `Graph adjacency` 也在 SSD，不把全图默认画在 DRAM。

箭头与数据流：
1. query 与 DRAM 1-bit sidecar 汇入非对称 gate。
2. `New graph neighbors` 提供待评分 ID；这些邻居来自已经发生的图扩展/邻接读取。
3. 幸存候选沿粗实线跨越边界，标 `Fetch primary payload for survivors`。
4. 被筛掉候选的请求线终止于 DRAM 内，标 `Pruned before payload fetch`，不要穿越边界后再打叉。
5. SSD primary 数据向上返回 CPU 侧 `4-bit distance estimate`；query 也连接到该计算。
6. 一个小回环由 4-bit 评分指向候选池/下一次图扩展，标 `Update search frontier`，说明 4-bit 参与导航，不只是输出后的精排。

绘制约束：
- 页面可包含多个记录；不画成一节点必然对应一页，也不把节点拒绝次数等同于节省页面数。
- 不画“先筛掉页再读取所有邻接”；实现是先读取当前扩展节点邻接，再筛新邻居的 payload。
- 不将 DRAM 全驻留表述为所有预算下都成立：此图展示 resident-routing 核心配置；分页预算策略放到扩展方法/实验图。
- 图中 query 保留实值入口，但 caption 注明当前磁盘实现 gate/navigation 可采用 INT8 派生查询内核；不写 `FP32 throughout`。

### (c) Refine where it matters — 精度分工

读者要理解：粗层不是最终答案，SSD 精度仍服务导航与最终排序。

对象：采用“同一候选记录的三种视图”纵向排布，不做泛化的三步流程方框。
- 顶层：蓝色 1-bit 行，旁标 `Screening`。
- 中层：同一个 ID 的完整 primary 4-bit 行，旁标 `Graph navigation`。
- 底层：primary 4-bit 与橙色 residual 4-bit 两条并列码带，旁标 `Final shortlist reranking`。
- 一个小候选集缩略图强调仅最终 shortlist 使用 residual 评分；不添加无来源分数或人为排名翻转。

箭头：
- 1-bit 与 primary 间加可选细虚线 `Reuse coarse contribution`：正常有效 gate 会将 short_ip 传给 4-bit 距离内核，这是已确认的计算复用，不应成为挤占主图的第四主题。
- primary 与 residual 各自连到 CPU 侧精化评分，query 分支也连入。
- residual 行标 `Correction to primary reconstruction`，避免与 primary 的剩余 3-bit 混淆。
- 不画“每个节点 uncertainty 判定 → 可选 residual fetch”：当前搜索实现是在结束后对选定候选集统一 residual rerank。

## 代码核对与边界

| 机制 | 结论 | 源码定位 |
|---|---|---|
| primary 为 4-bit，分解为 MSB 1-bit + 其余 3-bit | 已确认 | `Ours/core/hnswlib/space_rabitq.h:52`，kTotalBits/kRemainingBits/kMsbWeight |
| 从 primary 取 MSB | 已确认，逐维 `primaryCodeValue(code, i) >> kRemainingBits` | 同文件 `:4364`，extract_paper_prune_sidecar |
| 数据导出从 compact 生成 MSB/factors | 已确认 | `experiments/02_disk_shared_graph/native/src/ours_port.rs:259`，export_sidecar |
| DRAM 不只存裸 bit | 已确认，含 factors/centroid 等；不能宣称整进程只需 N×d bits | 同文件 `:425`、`:457` |
| 查询是实值输入 | 已确认，search 接收 &[f32]；prepared 保存 raw query | 同文件 `:896`；`space_rabitq.h:3681` |
| 当前默认磁盘 gate 完全用 FP32 query | 不成立，默认 gate ablation 选择 INT8；存在独立 Full 实现 | `ours_port.rs:98`；`native/src/main.rs:2275`；`space_rabitq.h:4396`、`:4867` |
| 非对称评分而非两边 1-bit Hamming | 当前 INT8/Full gate 已确认；另有 B1 对称实验分支，本图不纳入 | `space_rabitq.h:4867`；`Ours/tests/query_coarse_codec_test.cpp:1` |
| gate 对象为新邻居候选 | 已确认，graph.read_nodes 后产生 fresh IDs | `ours_port.rs:993`、`:1059` |
| gate 决策依据 | 候选池已满、estimate 有效且 lower_bound 大于池尾距离时拒绝；不是固定保留率 | 同文件 `:1075` |
| primary 参与图导航 | 已确认，幸存节点读取 compact 后算距离并插入候选池 | 同文件 `:1095`、`:1116`、`:1130` |
| short_ip 复用 | 已确认，有效 gate 采用 DIST_MODE_REUSE_QUANTIZED_MSB | 同文件 `:1101`；`space_rabitq.h:4341` |
| residual 4-bit | 当前构造显式传入 4；普通编码对 primary 重构误差量化 | `src/graph_core/src/ours_diskann.rs:279`；`space_rabitq.h:5946` |
| residual 调用时机 | 最终 shortlist 统一重排，不是逐节点不确定性触发 | `ours_port.rs:1145`、`:544` |
| residual 一定到重排才物理读盘 | 取决于布局；普通 payload 合放 compact+residual，locality 路径分开读取 | 同文件 `:294`、`:663`、`:1159`；`experiments/04_ours_memory_budget/README.md:39` |
| 1-bit 在所有预算下都全驻留 | 不成立，有 Resident/Paged 两条分支 | `ours_port.rs:425`；`routing_paged/README.md` |
| gate 保证 Recall 无损 | 本次未核实证明；代码有 epsilon 控制的估计界，不将变量名 lower_bound 当作零漏检证明 | `space_rabitq.h:4701` |

另有 adaptive/projected routing 路径（`ours_port.rs:1020`），其注释明确是诊断性经验筛选，不是原空间距离下界。本图只描述 primary-derived MSB 路径，不混入降维、独立码本或投影路由。

已检查实验驱动 `gist_joint_optimization/run.py` 与 `gist_fixed_factors_budget/driver_base.py`。它们主要改变预算/缓存/调度，逐查询检查 ID、Recall、visited、distance evaluations、DB1 checks/survivors 等一致性；这些检查不能自动证明“移除 1-bit gate 后 Recall 仍完全相同”。历史二进制快照不等于当前工作树，本次不声称逐个复核全部实验二进制。

## Caption 草案

**Figure 2. Primary-derived routing for selective SSD access.** (a) A 1-bit routing view is extracted from the most-significant bit of each primary 4-bit code coordinate and stored with scoring factors in DRAM. The complete primary code remains on SSD. (b) Asymmetric screening evaluates newly discovered graph neighbors before their fine payloads are requested; surviving candidates are scored with the primary 4-bit representation to update the search frontier. (c) The final candidate shortlist is reranked using the primary representation and its 4-bit residual correction. The diagram shows the resident-routing configuration. Queries enter as real-valued vectors; the current disk implementation uses an INT8-derived query representation for the gate/navigation kernels. Candidate counts are illustrative; logical refinement stages do not imply a separate physical I/O for each stage.

## 绘制与审查交接

- 保留图中文字：`Primary 4-bit`、`Extract MSB`、`Derived 1-bit`、`DRAM`、`SSD`、`Real-valued query q`、`Asymmetric screening`、`Promising candidates`、`Graph navigation`、`Residual 4-bit`、`Final shortlist reranking`。
- 蓝色表示 primary 及其同源 MSB，橙色表示 residual，灰色表示被筛候选。相同候选 ID 在 panel 间保持一致，颜色不是唯一编码。
- 省略训练细节、FHT/SIMD、并发数、缓存替换策略、BFS 重排、全部文件格式及页元数据；保留 factors 小标签与邻接来源，防止示意简化变成机制错误。
- 无实验曲线/数值，无需虚构 source CSV；若后续加入收益 inset，须单独锁定同期公平对比原始数据。
- 源头为本计划中的代码定位。已完成 visual_reference、参考图纠错、原生 draw.io 重建、PDF/SVG 导出及本地版面 QA；原生 draw.io 应用显示及投稿模板适配尚未核验。
- 本图无需 Product Design 或外部工具授权才能完成设计；图面可读性审查留待真实图稿。正式生成时可先探索视觉参考，再按代码事实重建原生 draw.io。
