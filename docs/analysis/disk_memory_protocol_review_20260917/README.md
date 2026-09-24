# 飞书实验方案核验与内存协议修订

> 后续更新（2026-09-17）：本文保留初次核验时点的记录。用户已授权修复代码与两份飞书，并明确每个配置只跑一次；原先五重复要求不再适用，单次运行本身不是失效理由。当前实施状态和验证见 [修复记录](../disk_protocol_fix_20260917/README.md)，当前实验条件以 [新版协议](../../plans/DISK_EXPERIMENT_PROTOCOL_20260917.md)为准。

日期：2026-09-17。

本次以用户指定的[《32 线程磁盘实验方案》](https://my.feishu.cn/docx/BpvBdhAILonRVkxT0Jicnwf4nfH) revision 52 为底稿，保留 05A/05B/05C 结构、查询划分与正式 5 次重复要求，准备并执行 26 处局部操作。旧段落保存在 `before.json` / `before.xml`，每次写入均回读全文核对；网络失败先读取现状，再判定是否需要重试。

本地修订全文：[DISK_EXPERIMENT_PROTOCOL_20260917.md](../../plans/DISK_EXPERIMENT_PROTOCOL_20260917.md)。主预算拟 4 GiB cgroup、32 线程；1/2/4/8 GiB 扫描保持相同线程数。cgroup 执行器、新验收字段与正式测量均未在此次任务中实现或运行。

随后按要求用飞书 CLI 读取[《AGNews 磁盘环境 32 线程实验分析》](https://my.feishu.cn/docx/AhoKdQxEBogmWoxfk6xcxHKjnsg) revision 316，交叉核验本地历史 CSV、artifact、原生命令和 preflight。该文档只读，不修改；核验结论作为实验方案第 15 节的补充依据。

- [AGNews 配置核验全文](AGNEWS_CONFIG_AUDIT.md)
- [AGNews CSV 提取](agnews_csv_check.json)
- [原始资源、设备与调用证据](agnews_evidence.json)
- [目标数据头部核验](input_header_check.json)
- [局部修改清单](patches.json)
- [逐步发布及核对状态](publish_progress.json)

本次不修改算法、旧结果或实际系统资源设置；没有重算全量输入哈希或 ground truth。数据头部核验不能替代输入来源和真值正确性验证。已核查的内存入口硬编码、局部消融限制与既有诊断仅支撑对应范围的结论。

发布完成：原实验方案 revision 52 → 78，26 处局部操作均逐步回读通过，21 张表格与正文核对一致。见 [verification.json](verification.json)。AGNews 文档仍为只读核验。
