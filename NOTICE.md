# Third-party notices

| Component | Origin | License | Pin / note |
|---|---|---|---|
| `Ours/core/hnswlib/` | Coconutstree/hnsw_rabitq（派生自 nmslib/hnswlib） | Apache-2.0 | commit `c8ea9195d629e6f7140136bdb03d7b9ec5afc0df`；仅保留 `hnswlib/` 头文件 |
| `baselines/diskann/` | microsoft/DiskANN | MIT | commit `3218478b5f7d1721840c29309e22d9fd74b1b4bc`；含本地修改：`diskann/src/graph/index.rs` 构图进度插桩及 README 备注 |
| Faiss | facebookresearch/faiss | MIT | commit `a424dcb809fd725c44dd976d9063febd4837d16a`；由 `scripts/setup_faiss.sh` 拉取 |
| 03 系统基线（pyglass / Intel SVS / SymphonyQG / NGT / SAQ） | 见 `BASELINE_EXPERIMENT_PLAN_MS_V2.md` | 各自许可 | 锁定提交见协议文档 |

`Ours/` 下的 Python 代码为原创。各组件完整 LICENSE 见对应目录。
