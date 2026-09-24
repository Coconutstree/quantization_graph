# 磁盘检索方法图文解读

> 2026-09-15 新增[四方法公平性审计](../disk_fairness_audit_20260915/README.md)：Glass 已复现官方/磁盘搜索不一致；其他方法的测试通过范围和待验收项见该记录。本文系列的方法介绍不等于正式公平性验收。

共五篇，仅包含当前 05C 的 Ours 与四个 baseline。每篇独立阅读，含方法原理、公式、流程图、内存/磁盘布局、查询过程及实现边界。四篇 baseline 于 2026-09-15 按要求重构：第一部分沿用 SAQ 示例的逻辑顺序，只讲论文原方法；第二部分简述我们的磁盘实验改动及执行顺序。Ours 于 2026-09-17 重写，以 locality v1 为正文主线。

| 方法 | 阅读入口 | 图数 |
| --- | --- | --- |
| Ours 磁盘版：1-bit 筛选、4-bit 搜索与残差重排 | [01_ours.md](01_ours.md) | 1 |
| DiskANN-PQ 磁盘版逐步拆解：内存 PQ 导航与磁盘精确评分 | [02_diskann.md](02_diskann.md) | 2 |
| SymphonyQG 磁盘适配逐步拆解：边局部 RaBitQ、FastScan 与隐式重排 | [03_symphonyqg.md](03_symphonyqg.md) | 2 |
| OG-LVQ 磁盘适配逐步拆解：逐向量标量量化、Vamana 与按页取码 | [04_og_lvq.md](04_og_lvq.md) | 2 |
| Glass-NSG 磁盘适配逐步拆解：稀疏导航图、SQ4U 与双端整数距离 | [05_glass_nsg.md](05_glass_nsg.md) | 2 |

核对日期：2026-09-14。当前工作区含已有实现修改；关键来源哈希保存在 [sources/sha256.json](sources/sha256.json)。未修改算法、未重新训练或运行性能实验。

## 阅读时需要区分

- Ours：正文以 locality 图/主码共置、残差分离布局为主线；旧普通布局放在差异说明中。
- DiskANN：官方磁盘路径；当前 PQ 码长为 ceil(D/2) 字节，不能套用经典 32 字节示例。
- SymphonyQG：当前节点行含邻居边码，候选评分不提前读取目标整行；一行可能占多个磁盘页。
- OG-LVQ：官方 SVS 构图/保存码，本地兼容解码和搜索循环；不能误称查询直接调用官方 SIMD kernel。
- Glass-NSG：当前 SQ4U 将 query 与数据库双端量化，使用 u4×u4 整数距离。

用户给定的 LVQ.pdf 实际为 LOPQ，OQG.pdf 与 OG-LVQ 也不是同一方法；按最终要求，不为它们另建方法文档。OG-LVQ 已补查正确 LVQ 原论文。

## 飞书文档

- [Ours 磁盘版：先用 1-bit 筛选，再用 4-bit 搜索，最后用残差重排](https://my.feishu.cn/docx/LctHdcd4loJv7kx80KrcCC30nDc)
- [DiskANN-PQ 磁盘版逐步拆解：内存 PQ 导航与磁盘精确评分](https://my.feishu.cn/docx/D1a4d1sksoDMuyxFvRec30Dgnle)
- [SymphonyQG 磁盘适配逐步拆解：边局部 RaBitQ、FastScan 与隐式重排](https://my.feishu.cn/docx/GQwrdL4feo9AtExH1qzcIGZxnfN)
- [OG-LVQ 磁盘适配逐步拆解：逐向量标量量化、Vamana 与按页取码](https://my.feishu.cn/docx/BfYtdjApkoSXxgxbzKVcSHDAnvg)
- [Glass-NSG 磁盘适配逐步拆解：稀疏导航图、SQ4U 与双端整数距离](https://my.feishu.cn/docx/HXtcd7yGUoY7xTxWSI6cl2ydnHd)

五篇初次发布均已回读验证；后续改写的资源数量和同步状态以各次更新记录为准。

本地 Markdown 为阅读稿；同名 XML 为飞书排版源，figures/ 为图片。revise_baselines.py 保留 2026-09-15 四篇 baseline 的生成逻辑；OG-LVQ、Glass-NSG 的最新阅读稿由 readable_revision_20260917/rewrite.py 生成，不应使用旧脚本覆盖。build_series.py 保留初稿生成逻辑。

SymphonyQG 的 FastScan 小节已补充逐步算例和解释图：四 bit 的 16 项 LUT → 32 邻居批量查表 → 符号还原与跨节点复用。

## 2026-09-17 新增两篇（已发布并回读验证）

沿用原方法在前、本地接入在后的模板；每篇两张机制示意图，正文包含公式、参数、内存账目与来源。Starling 补入 2026-09-17 GIST 内存诊断，AiSAQ 区分原始全内联与固定官方版本扩展。

- [Starling：内存导航图、块重排与页内搜索](06_starling.md)
- [AiSAQ：邻居 PQ 内联、按需读码与 DRAM 开销](07_aisaq.md)

用户明确授权后，两篇均已发布并回读验证：正文、12 条公式、8 张表格与本地一致，4 张图片均存在。

- [Starling 飞书文档](https://my.feishu.cn/docx/WKeldEixjomxEkxYVd3cSJgTnWb)
- [AiSAQ 飞书文档](https://my.feishu.cn/docx/S7dTdCZE3okkDhxDXC6cqegZn4b)

验证记录见 [extension_20260917_status.json](extension_20260917_status.json)。

## 2026-09-17 Ours 重写

本地与原飞书文档已同步并回读验证：1 张流程图、8 条公式、5 张表格。正文以 locality v1 为主线，依次解释编码、建图、存储、查询和误差；旧版详细推导保存在 [备份](ours_revision_20260917/before.md)，发布核对见 [verification.json](ours_revision_20260917/verification.json)。本次没有修改算法或重跑实验。

## 2026-09-17 OG-LVQ 与 Glass-NSG 通俗改写

两篇均保留“论文原方法 → 我们的磁盘改动”两部分，改用逐步算例解释概念：OG-LVQ 展示 4 bit 编码、恢复数值、残差和距离计算；Glass-NSG 区分图方法、实现库和量化配置，用三个点解释选边，再说明查询顺序与实际读盘位置。同步纠正旧性能曲线与当前源码的证据边界。

本地与原飞书正文已回读比对一致，每篇原有两张图片的 block ID 和资源 token 均保留。Glass 新增的选边高亮已纳入改写。OG-LVQ 发布后为 revision 12，Glass-NSG 为 revision 17；核验与原稿备份见 [改写记录](readable_revision_20260917/README.md)。未修改实验代码或重跑实验。
