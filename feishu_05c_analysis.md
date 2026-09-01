05C 指标分析：在 Recall@10 0.93–0.994 的论文工作区里，Ours-Disk 的 recall–QPS 前沿整体占优——最高 QPS 202.13、平均 latency 586.72 ms/query、I/O 请求 931.4 次与 3.64 MiB/query 均为四个方法里最低。四个方法都以 I/O wait 为主（≥94.7%），磁盘读取是共同瓶颈；Ours 的 distance compute 仅 0.08%，其瓶颈在 DB1 1bit 初筛后的 full4 页读取（809.7 页/query）。SymphonyQG 把 recall 上限推到 0.9992，但 QPS 只有 5.18（约 Ours 的 1/39）、每查询读 57.95 MiB（约 Ours 的 16×），高 recall 靠高 I/O 成本换取；OG-LVQ（QPS ≤56.7、recall ≤0.953）与 Glass-NSG（QPS ≤135.5、recall ≤0.945）位于中间带。

### Figure 2：05B shared graph Recall-QPS（02 层）
