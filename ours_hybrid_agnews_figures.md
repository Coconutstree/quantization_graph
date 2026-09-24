# AG News：hybrid Recall–QPS图

## 六档配额对比（验证集）

![AG News六档配额对比](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_quotas.png)

基线与0%、12.5%、25%、37.5%、50%、75%全部六档；100条调参验证查询，每档两轮。主线为合计查询数/合计查询时间，阴影为两轮最小–最大范围，不是置信区间。所有40档L均保留，按L连接，不平滑、不插值。另100条独立验证查询训练热点，测试查询不参与配额选择。

[PDF](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_quotas.pdf) · [SVG](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_quotas.svg)

测试集尚未全部完成，目前不绘制测试集验收图。完成后重新运行本绘图脚本可生成。
