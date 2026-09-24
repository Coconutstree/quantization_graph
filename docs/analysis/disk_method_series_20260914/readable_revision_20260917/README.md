# 两篇方法文档的通俗改写（2026-09-17）

用户反馈 OG-LVQ、Glass-NSG 两篇难以理解。本次直接更新原飞书文档和本地阅读稿，保留此前要求的两部分结构及论文解释顺序。

| 文档 | 改写内容 | 飞书核验 |
| --- | --- | --- |
| [OG-LVQ](../04_og_lvq.md) | 用四维向量解释独立刻度、4 bit 编码、恢复值、残差和查询距离；解释候选池与展开；指出磁盘候选评分前的取码开销 | revision 8 → 12；正文一致；2 张原图片保留 |
| [Glass-NSG](../05_glass_nsg.md) | 区分 NSG、Glass、SQ4U；用二维点解释剪边与可达性；补充查询轨迹、共享刻度与读盘顺序 | revision 13 → 17；正文一致；2 张原图片保留 |

原文首次读取后，Glass revision 12 → 13 新增了一处选边高亮，没有修改文字或图片。已保存该变更前后快照，并在改写稿相应解释处保留黄色红字强调，见 05_rebase_note.json。

## 内容依据

- LVQ 原论文：../sources/LVQ_correct.pdf 与 .txt，§3–5；正文保留论文链接。
- NSG 原论文：../sources/NSG.pdf 与 .txt，Algorithm 1、2；正文保留论文链接。
- 当前磁盘端口：experiments/05_disk_system_fair/native/og_lvq_disk_port.cpp、glass_disk_port.cpp。
- Glass 编码：baselines/pyglass/glass/quant/sq4u_quant.hpp、calibrator.hpp。
- 既有一致性核验：docs/analysis/baseline_algorithm_audit_20260916/README.md。

OG-LVQ 仍为本地距离解码实现，正式一致性准入未通过。Glass 源码中的候选队列已修正，但历史曲线和旧二进制没有因此自动更新。未把这两类历史结果写成官方方法的正式性能结论。

## 修改与核验方式

- rewrite.py 仅生成这两篇 Markdown/XML 和 6 个正文片段；原本地稿保存在 *_local_before.md/xml。
- publish.py 按三个图片区间替换文字，然后删除旧文字；每次修改带 revision 守卫并回读，不覆盖文档，不重建图片。
- *_before.json 保存改写前的完整飞书文档；*_after.json 保存最终回读内容。
- 04_verification.json、05_verification.json 记录正文与本地一致、两部分结构、表格数量、原图片 block ID 与 token 均保留。
- 两张 *_storage_remote.jpg 是飞书预览返回的原存储图（实际 MIME 为 image/png），用于本地阅读稿与飞书保持一致；没有编辑图片。

本次未修改实验代码、二进制、实验配置或性能数据，未启动任何实验。
