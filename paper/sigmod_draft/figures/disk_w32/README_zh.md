# 论文实验图：修订版

已按 nature-figure 流程重画，并通过 main.tex 的 diskfigure 宏在论文第 6 节插入四组图。main.pdf 已编译；图 2–5 位于第 8–9 页。manuscript.md 保留插图标记，render_latex.py 支持转换，后续 build.py 编译不会丢图。

## 图表

- fig01_throughput：高召回吞吐量，正文图 2。
- fig02_read_volume：高召回读取量，正文图 3。
- fig03_tail_latency：p95 查询延迟，正文图 4。
- fig04_screening_io：筛选与读页计数，正文图 5。
- fig05_full_sweep：完整吞吐量扫描，独立补充图。
- fig06_requests：I/O 请求数，独立补充图。
- all_figures.pdf：以上六组图的合并预览。

前三组正文图为 178 × 62 mm，筛选图为 178 × 100 mm；图内文字 8 pt。统一同类指标的坐标范围、方法配色、线型和标记。移除图内流程状态页脚，实验条件与限制写入图注和正文。曲线保留全部顶点，标记每五点显示一次以降低遮挡；不是删除数据。高召回图视窗为 0.90–1.00。

## 重绘和编译

在项目根目录执行：

```bash
python3 paper/sigmod_draft/figures/disk_w32/plot_figures.py
python3 paper/sigmod_draft/build.py
```

绘图依赖 NumPy、Matplotlib、pypdf。每图同时输出可编辑文字 PDF/SVG、300 dpi PNG 和 600 dpi TIFF。图注见 captions.md，各图 .tex 可单独修改。include_figures.tex 提供四图汇总引用；论文已在正文逐图引用，不应再次插入以免重复。

## 证据范围

使用冻结的 12 个完整结果，共 543 个测量点。AGNews、GIST 各五系统，DBpedia 两系统；未合成缺失结果。原始快照、SHA256、CSV 和召回阈值选择记录在 source_data。Ours 是 locality 布局版本。每设置只有一次测量，未添加跨运行误差条。正式验收、完整分类内存账本仍待完成。已验证的进程地址空间上限不能替代完整搜索内存公平性。

在最低召回 0.95 要求下，AGNews/GIST 的 Ours 吞吐量约为 DiskANN 的 2.99/3.44 倍，但读取量也约为 6.45/6.42 倍；两者达到的实际召回略有不同。论文已据此写明权衡，不声称统一减少 I/O。筛选计数不替代因果消融实验。

## QA

静态检查 20 项全部通过；六组 PDF 的最小字体均为 8 pt。检查了各面板、共享图例和整张图，并渲染最终论文第 8–9 页检查实际排版。详细记录见 qa/revision2_review.md、panel_review.csv、source_validation.json 和各 PDF 文字审计。旧三组比较图已归档至 archive_v1。
