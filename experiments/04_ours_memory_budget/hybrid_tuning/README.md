# Hybrid比例选择实验

固定新增缓存额度1,247,580,160字节（1189.78515625 MiB），原搜索参数和BFS布局不变；整进程仍限制2 GiB。独立复制并核对已验收v3的原生源码，只让hybrid精细记录配额可由profile目录中的hybrid_ratio.json指定；不改已完成v3二进制、结果和原测试入口。

## 验证与选择

- 原保存顺序的200条验证查询前100条用于训练热点，后100条用于选择比例；不使用800条测试查询调参。
- 邻接表上限保持25%；精细记录上限测试0%、12.5%、25%、37.5%、50%、75%。扣除静态实际占用后的剩余全部给page，统计配额与实际节点/页数量，避免把热点覆盖饱和后的同配置误当不同算法。
- 完整40档L、workers32、beam1、100预热、原INT8/DB1与rerank规则不变；调参阶段100条查询均会在预热中出现，明确为重复查询预热口径。
- 每比例两轮，固定随机顺序＋反序。选择指标是两轮全部40档合计查询数/合计查询时间；最高值1%以内优先较小精细记录配额，不根据某一测试集L挑选。1%是预先声明规则，不是显著性检验。
- 两次调参基线夹住配比序列，完整吞吐偏差超过0.75–1.35停止。每个搜索进程前执行统一03 O_DIRECT预读；不控制硬件缓存，不加NUMA绑定。
- selection_lock.json先写入，再采用已验收的200条全验证集profile作为最终排名，原25%和所选比例各做两次测试（800条，40档），按原→选→选→原运行，并在前后各测一次基线。若所选也是25%，只有两次同方案测量，不伪造另一对照。
- 每组验收结果一致性、I/O计数、内存和预读身份。性能门槛不能替代统计重复与更多数据集验证。

## 运行与输出

```bash
python -u experiments/04_ours_memory_budget/hybrid_tuning/run.py
cat results/04_ours_memory_budget/hybrid_tuning/state.json
tail -f results/04_ours_memory_budget/hybrid_tuning/queue.log
python -m unittest discover -s experiments/04_ours_memory_budget/hybrid_tuning -p 'test_tuning.py' -v
```

后台已经启动时不要重复运行。拒绝覆盖或隐式续跑已有目录；失败时保留证据，人工核查修复后再决定继续点，不自动改参数或清除失败数据。

独立源码/程序：work/ours_memory_budget/hybrid_tuning/。结果：results/04_ours_memory_budget/hybrid_tuning/。根目录ours_hybrid_ratio_tests.md每20秒更新阶段及完整档位数。runs下保存原生结果、逐查询trace、内存及预读证据、配额和排名身份；selection_lock.json保存调参结果和验收哈希；全部完成后生成results.csv、summary.json、test_comparison.json及文档合并结果。

QPS不包含离线训练、统一预读、初始化、预热；正式查询的缓存查找/维护/复制及I/O全部计时。缓存计入RLIMIT_AS，内核及设备缓存不在进程内存上限内。
