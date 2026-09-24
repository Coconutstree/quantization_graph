# PCA residual 三组消融，固定 M=32

三组均使用同一 GIST 图、538 MiB 整进程预算、32 workers、beam=1、M=32。M 是每次扩展的邻居 shortlist；不改变已有 R64 图的构建参数。

1. PCA + 1-bit：`max(0, projected_estimate - native_error)`。
2. PCA + 1-bit + residual norm：在第一式上加 `(sqrt(x_tail_squared)-sqrt(q_tail_squared))²`。
3. 完整 1-bit：原 960 维量化器，code 补齐至 1024 bit；完整 codes 在该预算下分页。

PCA 不再扣截断误差。残差只在门控阶段加入，top-M 排序使用同一 epsilon=0 分数，防止把 residual 排序变化混进门控消融。三组都保留 top32，然后以 epsilon=1.9 重新计算 gate。DB1 checks 因而计入排序和 gate 两次估计。

三组完整 4-bit verification 都使用 `RECOMPUTE_FULL`；不复用 INT8 或低维 short_ip。这里的 full1bit 基线是本次统一 M32/验证口径的完整维度对照，并非直接复用旧版生产 baseline 的历史结果。

该实验沿用 native 统计误差界，并与近似 4-bit pool 阈值比较，属于**经验门控**。它不能证明真实 L2 下界与量化阈值比较是严格安全的；Recall 必须实测。严格安全的同分数证书留在独立的后续工作中，不混作已完成成果。

## 内存和控制

按已锁定 fixed=426 MiB、reserve=64 MiB 选择最高可常驻 PCA 维度。两组均选 128 维；无 residual 组 record cache=12 MiB，有 residual 组因增加 4,000,000 bytes 统计量，record cache=8 MiB。原始 1-bit 基线使用 BFS route 页布局、最多 16 MiB 页槽额度和共享并发 256。

额外两个控制不算第四种主方法：train100 关闭门控、两组均 cache8，检查 residual 不影响 top32 和完整4bit的结果；validation width100 下补无 residual/cache8，与有 residual/cache8 比较，隔离缓存代价。

## 协议和运行

validation100 width=40/60/100/180；若某组无 Recall≥0.95 的点，则只为该组补 width=260/380。各组在达标点中选最快 width，锁定后独立 test800 两轮，第二轮反转方法顺序。不得根据 test 重新选参。

```bash
python3 experiments/04_ours_memory_budget/gist_pca_residual_ablation/prepare.py
python3 experiments/04_ours_memory_budget/gist_pca_residual_ablation/benchmark.py
python3 experiments/04_ours_memory_budget/gist_pca_residual_ablation/report.py
python3 experiments/04_ours_memory_budget/gist_pca_residual_ablation/plot.py
python3 experiments/04_ours_memory_budget/gist_pca_residual_ablation/audit.py
```

完整原始记录在 `results/04_ours_memory_budget/gist_pca_residual_ablation/`。实验使用 O_DIRECT，预读及 warmup 不计入查询计时；设备缓存不受控。既有 competing build 只在 benchmark 期间暂停，并由 finally 恢复。
