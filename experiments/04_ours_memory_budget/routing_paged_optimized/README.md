# Routing 分页批量化优化

本目录对比相同索引、查询、硬内存预算和全局在途额度下的三种分页实现：
历史 v1、批量化 LRU、批量化 CLOCK。历史实验目录和二进制不覆盖。

```bash
python experiments/04_ours_memory_budget/routing_paged_optimized/run.py --build --run
python experiments/04_ours_memory_budget/routing_paged_optimized/run.py --regression
python experiments/04_ours_memory_budget/routing_paged_optimized/report.py
python experiments/04_ours_memory_budget/routing_paged_optimized/audit.py
```

实验产物：`results/04_ours_memory_budget/routing_paged_optimized/`。
构建快照：`work/ours_memory_budget/routing_paged_optimized/`。
旧版源文件和二进制：该工作目录的 `before/`。
进度与报告：`ours_routing_paged_optimized_tests.md`。

## 实现

- 一批请求一次跨 FFI 获取并固定页；就绪页直接返回只读指针。
  Rust 在锁外只复制记录需要的字节，随后一次批量释放。旧单页复制 API 只用于兼容测试。
- 每个查询线程复用一个 C++ 等待对象。完成时只通知依赖相关页的等待者；
  容量释放额外唤醒一个等待者。登记与条件复查都在同一把锁下，避免丢失通知。
- 提交和完成各有一个 eventfd。完成线程用 poll 等待两类事件、非阻塞收取完成，
  回收一部分控制块后立即补交；新请求也可以在其他 I/O 尚未完成时唤醒线程。
- 64 记录窗口复用线程本地 Vec 和固定数组；页请求由连续数组排序去重形成。
  就绪评分以至少 8 条合并（最后一批无此限制），估计结果缓冲在查询内复用。
  搜索状态仍按原候选顺序更新，未改变 beam、剪枝或精算规则。
- 新增 `--routing-cache-policy clock|lru`，分页默认 CLOCK。
  CLOCK 命中只设置访问位，淘汰时扫描未固定页；LRU 保留用于实测比较。
  两者使用相同的容量计账、在途上限及页固定规则。没有增加分片的额外容量。

常驻 routing 路径不创建以上读取器或线程，不分配分页工作区。
旧 `--routing-cache-bytes` / `--routing-max-inflight-pages` 接口保持不变。

## 计账与指标

仍采用每页 4096+256 字节、10 MiB 服务预留和既有分页 scratch 预留，
每种策略在同一缓存比例下使用完全相同的硬预算与页槽数量。
新增指标包括 lock_calls、lock_wait_ns（查询 API 获取元数据锁的等待）、
notifications（逐等待者通知）、wait_calls、submit_calls、submitted_pages。
wait_ns 包含条件变量等待；锁等待和条件等待分开记录，不从 QPS 反推耗时。
这是保守预算准入；未把“选择分页”解释为实测常驻必然 OOM。

## 验证和实验

主对照：GIST、AG News，既有测试顺序前 100 条，L100、beam1、workers32、
100 查询预热，两轮反向交错顺序。同期测常驻、v1、优化 LRU、优化 CLOCK；
分页分别覆盖约 25% / 75% sidecar 页。
扩展回归：两套数据 L300/580，常驻及两策略、两容量各一次。
逐查询核对结果 ID、召回、访问/距离/剪枝相关计数、精算和重排候选及读页。
总 I/O 增量与 routing 物理读取一一对账，并验收 RLIMIT_AS、VmPeak、RSS 和在途峰值。
所有运行沿用记录在案的 O_DIRECT 全文件预读协议，不宣称冷 SSD。

原生测试覆盖 1 页容量、跨页/尾页、并发合并和失败唤醒；四种查询精度逐位评分；
完成事件反序及部分提交失败。`test_rolling.cpp` 扣住一个完成事件，要求补交第三个
请求后才释放它，用来验证滚动补交确实不依赖整个旧批次完成。
ASan/UBSan 的故障注入运行关闭 LeakSanitizer，因为沙箱 ptrace 不兼容它。

## L100 两轮对照结果

| 数据集 | 缓存比例 | CLOCK QPS | 相对同期 v1 |
|---|---:|---:|---:|
| GIST | 25% | 10.21 | +3.3% |
| GIST | 75% | 38.96 | +77.8% |
| AG News | 25% | 17.22 | +20.4% |
| AG News | 75% | 52.57 | +62.8% |

GIST 的 25% 缓存改善有限；75% 缓存收益更明显。两轮小样本不作显著性结论。
分页仍慢于常驻。完整 LRU 对照、尾延迟、内存及原始计数见结果目录，
扩展回归只用于正确性验收，不混入此性能表。
