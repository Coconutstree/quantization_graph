# 低维 1-bit 距离估计修正实验

目的：保持图、full4/residual 磁盘数据、worker=32、width=49、beam=1、
缓存策略以及 keep/threshold 不变，测试逐向量统计能否改善距离估计。
这不是继续压低 keep，也不是新增完整 DB1。

令 y=P(x−mean)，b=sign(y)，s=mean(abs(y))，r²=||y||²，z=P(q−mean)。
忽略对当前 query 固定的 ||z||²，三个对照为：

- 旧版本：d s² − 2s〈z,b〉。
- `_n`：r² − 2s〈z,b〉，用真实投影范数替换重建范数。
- `_c`：r² − 2a〈z,b〉，a=r²/(d s)。理想无 FP16 误差时，
  在 z=y 处恢复零自身距离；但不保证其他 query 的内积无偏或准确。

这两个修正仍是经验估计，均未校正被投影丢弃的方向，也不是原空间
距离下界。范数差与重建误差有关，但单独补范数不保证总体估计改善。
FP16 scale 来自原有码文件，校准后仍以 FP32 常驻。新增范数用 FP32
存储，AGNews 769382 行增加 3,077,528 字节（约 2.935 MiB）。
`resident_bytes` 按实际 Vec 容量包含这部分。Query 仍不二值化。

编码仅读取 base 和既有投影，输出到独立目录，原码与磁盘索引不变：
`results/disk_environment/06_adaptive_disk_ann/agnews/norm_correction_d256/`。
manifest 记录 base、投影、范数 SHA-256、行数和新增字节数；不使用查询/GT。
原始 projected norms 的生成只需一次，`_c` 载入时改写原有 scale
向量而非再增加一份 scale。不开启 norms 参数时恢复旧版估计。

## 复现

```bash
# 编码输出目录须是尚无同名文件的新目录，避免覆盖旧数据。
OPENBLAS_NUM_THREADS=8 OMP_NUM_THREADS=8 python legacy/06_adaptive_disk_ann/encode_route_norms.py --base data/agnews/agnews_base.fvecs --projection-dir results/disk_environment/06_adaptive_disk_ann/agnews/full_route_encode_20260909/projection_nnp --output-dir NEW_NORM_DIRECTORY --dimension 256
cargo build --manifest-path src/graph_core/Cargo.toml --release --bin qgraph05_shared_graph_port
cargo test --manifest-path src/graph_core/Cargo.toml --bin run_diskann_fair adaptive_numerics_tests
python legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py --queries 100 --budget-gib 0.5 --norm-dir results/disk_environment/06_adaptive_disk_ann/agnews/norm_correction_d256 --variants baseline,d256_k32_t100,d256_k16_t100,d256_k32_t100_n,d256_k16_t100_n,d256_k32_t100_c,d256_k16_t100_c
```

数值单测覆盖原非对称公式、FP16 极小数和校准自身距离/内存计数。
自身距离测试不代表保召回，必须以图搜索实验验证。

## 前 100 条筛选（已完成）

| 配置 | Recall@10 | QPS | I/O/query | Codec MiB |
|---|---:|---:|---:|---:|
| 原有 Ours | 0.992 | 42.59 | 612.4 | 108.6 |
| 旧估计 keep32 | 0.989 | 44.01 | 523.5 | 27.4 |
| 范数修正 keep32 | 0.975 | 32.85 | 674.3 | 30.4 |
| 范数+尺度校准 keep32 | 0.989 | 42.95 | 536.4 | 30.4 |
| 旧估计 keep16 | 0.982 | 55.07 | 356.4 | 27.4 |
| 范数修正 keep16 | 0.918 | 50.10 | 430.4 | 30.4 |
| 范数+尺度校准 keep16 | 0.981 | 57.87 | 355.8 | 30.4 |

所有低维配置 dim=256、threshold ratio=1.0，不改变 keep 比较策略。
只补范数的 `_n` 变差，排除；`_c` 未显示明确收益，进入后 900 条验证。
因此不能因公式看似更完整就默认开启。误差项、内积近似和筛选阈值
存在相互影响；本实验没有证明单个因素是结果变化的唯一原因。

原始记录目录：

- 前 100 条：`results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260910_001454/`
- 后 900 条：`results/disk_environment/06_adaptive_disk_ann/agnews/disk_replacement_20260910_001549/`

后 900 条在前面的开发实验中已使用，不是全新最终测试集。
每目录都有参数、JSON 汇总、逐查询轨迹和日志。单次 pilot，非正式曲线；
0.5 GiB 为记账预算，未证明强制硬内存限制。未做五次重复。

## 后 900 条确认（全部完成）

| 配置 | Recall@10 | QPS | I/O/query | 峰值 RSS MiB |
|---|---:|---:|---:|---:|
| 原有 Ours | 0.9896 | 49.64 | 590.5 | 318.8 |
| 旧估计 keep32 | 0.9891 | 50.16 | 515.2 | 250.6 |
| 范数+尺度校准 keep32 | 0.9890 | 49.66 | 522.6 | 258.6 |
| 旧估计 keep16 | 0.9830 | 68.89 | 345.0 | 204.7 |
| 范数+尺度校准 keep16 | 0.9828 | 58.55 | 343.1 | 208.2 |

**结论：不启用本轮两种范数修正。** `_n` 明显损失 Recall，`_c` 没有
显示足以抵偿新增内存的收益。keep16 校准版读盘仅少约 0.6%，不能称作
有效 I/O 优化；QPS 单次波动，不能证明稳定变慢的幅度，但也没有提速证据。
推荐仍保持旧估计 d256/keep32/ratio1.0 作为保守候选，norms 开关默认关闭。
实验代码与独立生成的元数据保留供复核，没有替换正式索引。

全部运行正常退出；release 编译、三项数值单测、Python 编译检查、
`git diff --check` 通过。脚本新增了范数 manifest 与投影 SHA 检查，
以拒绝与当前投影不匹配的范数文件。后续预算选维/候选参数联合选择
仍需另外实现；本轮不宣称已经完成整个自适应方案。
