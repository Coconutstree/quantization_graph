# GIST：统一RAM预算与实测RSS验收

默认实验为 **512 / 2048 / 4096 / 8192 MiB × Ours / DiskANN × 40 widths × 800 queries × 1轮**。
按预算配置路由及缓存，以实际进程RSS验收；不设置RLIMIT_AS或cgroup硬上限，不需要sudo。
原目录名保留以兼容调用；新默认结果写入 `results/04_ours_memory_budget/gist_ram_budget_rss/`，不混入旧cgroup/地址空间结果。
SymphonyQG使用共享03入口的同一预算与RSS验收协议；此GIST专用扫描仍是Ours与DiskANN两方法。

## 运行

```bash
python experiments/04_ours_memory_budget/gist_same_ram/run.py --prepare
python experiments/04_ours_memory_budget/gist_same_ram/run.py --run
```

`--prepare`只核验/冻结准备资料；`--run`依次进行train校准、tune缓存选参、冻结参数、test100工程验证、test800正式扫描和报告。
准备会核验冻结二进制、源码、索引和输入哈希。未通过不会自动替换输入或二进制。
使用 `--output` 指定新目录。拒绝修改已冻结配置及覆盖未完成运行。

## 统一口径

- 预算是计划中的进程RAM额度，涵盖路由编码、参数、缓存与元数据、线程工作区及校准后的固定开销；不是虚拟地址空间限额。
- 每个width独立启动进程；RSS监控涵盖加载、预热、查询及结束阶段，取wait4与采样VmRSS/VmHWM最大观测值。VmPeak单列但不用作RAM验收。
- RSS超过预算记录 `budget_exceeded`，保留日志，不计入有效曲线；观测到swap则记 `swap_observed`。这是测量后验收，不是内核强制限制，不承诺阻止临时超额分配。
- 每50 ms采样；RSS含进程用户态内存，不能等同包含内核/file cache的cgroup记账，也不包含子进程合计。当前直接运行原生搜索程序。
- 设置 `QG05_MEASURE_WHOLE_PROCESS=1`，支持该开关的原生程序不重置HWM。采样仍可能漏掉不支持此开关的程序的短暂峰值。
- 返回码、搜索正确性和资源验收分别检查。预算拒绝/分配失败不填QPS=0，其他程序错误停止队列。

## Ours(B)

使用既有冻结的inflight256实现和新RSS校准锁。`U=B-fixed-reserve-factors`；能放下完整codes则常驻，余量进入hot→dynamic记录缓存；否则扣除分页服务/scratch，按4352 bytes/槽分配route页槽。
固定开销由train100、完整40 widths的resident/1 MiB分页/64 MiB静态记录配置校准：RSS峰值减去确定驻留的数据，其余保守归为fixed，另加8 MiB完整查询集余量和独立64 MiB reserve。
不复用旧VmPeak或cgroup校准。native plan的expected_peak只是分配估计；RSS单独验收。

本次统一实验口径，**没有新增自动PQ回退**；若512 MiB现有表示不能准入，保留失败，不能替换为538 MiB冒充成功。

## DiskANN(B)

保留现有PQ与图。每档先在tune100运行c0，以RSS峰值校准基础开销；剩余预算减64 MiB reserve后，扫描25%/50%/100%的原生节点缓存配置，保留原生10%节点缓存上限。
原生CLI缓存规划额度可小于总预算，外部RSS验收始终使用该档完整预算。PQ、码本、线程工作区均需计入。
候选均扫描40 widths，优先达成更多Recall门槛，再最大化Recall≥0.90/0.95/0.98下最佳QPS的log之和；平局选择更小缓存配置。
c0失败也保留失败类别，不能泛化为所有PQ长度均不可行。冻结配置测试失败后可补c0诊断，诊断不替代主曲线。

## 输入和结果

train/tune/test按向量内容互斥；test保留原800行及顺序。两边32 workers、beam1、每width预热100条；各阶段独立保存，热点只来自train。
使用O_DIRECT，无额外整文件预读，不宣称冷SSD；两方法遵循相同规则。该规则与共享03入口的整文件预读不同，禁止跨批次拼接QPS。
QPS使用原生查询批次墙钟，排除加载/预热/Recall计算。宿主机状态在每次运行前后记录。
报告只纳入通过资源与逐查询验收的test800数据。单轮无稳定性或置信区间声明；RSS模式不填写虚假的cgroup peak。

## 显式cgroup对照（可选）

```bash
python experiments/04_ours_memory_budget/gist_same_ram/run.py --memory-mode cgroup --cgroup-root /sys/fs/cgroup/your-delegated-group --run
```

该选项仍要求事先委派的可写cgroup，执行正向/OOM探针；不会自动降级为RSS模式。
默认另存 `gist_same_ram_cgroup_v2/`，按cgroup peak校准和验收。预算也为512/2048/4096/8192 MiB，不续写旧档位结果。

```bash
python -m unittest discover -s experiments/04_ours_memory_budget/gist_same_ram -p 'test_*.py' -v
python experiments/04_ours_memory_budget/gist_same_ram/report.py results/04_ours_memory_budget/gist_ram_budget_rss
```
