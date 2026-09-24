# 本地向量数据清单

统计日期：2026-09-18。范围：项目 `data/` 目录中的向量数据，索引文件不计入向量数量。数量来自实际文件头和文件大小，不按数据集名称推断。

## 当前数据集

| 数据集 / 目录 | 向量维度 | 底库向量数（base） | 查询向量数（query） | Ground truth 行数 × K |
|---|---:|---:|---:|---:|
| [glove](data/glove/) | 25 | 1,183,514 | 10,000 | 10,000 × 100 |
| [deep1B](data/deep1B/) | 96 | 9,990,000 | 10,000 | 10,000 × 100 |
| [sift10m](data/sift10m/) | 128 | 10,000,000 | 10,000 | 10,000 × 1,000 |
| [bigann10m](data/bigann10m/) | 128 | 10,000,000 | 10,000 | 10,000 × 1,000 |
| [gist](data/gist/) | 960 | 1,000,000 | 1,000 | 1,000 × 100 |
| [cohere10m](data/cohere10m/) | 768 | 10,000,000 | 10,000 | 10,000 × 100 |
| [agnews](data/agnews/) | 1,024 | 769,382 | 1,000 | 1,000 × 100 |
| [msmarco](data/msmarco/) | 1,024 | 113,520,750 | 1,677 | 1,677 × 1,000 |
| [dbpedia](data/dbpedia/) | 1,536 | 990,000 | 10,000 | 10,000 × 100 |
| [smoke03](data/smoke03/) | 128 | 5,000 | 200 | 200 × 100 |

文件命名通常为 `data/<数据集>/<数据集>_base.fvecs`、`<数据集>_query.fvecs` 和 `<数据集>_groundtruth.ivecs`。
.fvecs 存储 float32 向量；ground truth 的 K 是每个查询保存的邻居 ID 数量，不是向量维度。

注意：

- `deep1B` 是目录名，本地底库实际为 **9,990,000** 条，并非 10 亿条。
- `msmarco` 本地底库实际为 **113,520,750** 条、**1,024** 维。
- GloVe 底库实际文件名为 [`glove_base.fvecscs`](data/glove/glove_base.fvecscs)，扩展名多了 `cs`，但按 fvecs 格式读取首尾记录头及大小均正常；ground truth 文件名为 [`glove.ivecs`](data/glove/glove.ivecs)。本次仅记录，未重命名。
- `sift10m` 当前清单标识为独立的 UCI SIFT10M，见 [`dataset_manifest.json`](data/sift10m/dataset_manifest.json)。
- **当前 `sift10m` 与 `bigann10m` 只是维度和规模相同，文件内容并不相同。** 实际读取两者 base、query、ground truth 的首条记录，三组均不同，且对应文件不是同一 inode。底库首个向量的前 8 维分别为 SIFT10M `[55, 23, 21, 15, 43, 100, 116, 63]` 和 BIGANN10M `[0, 0, 0, 1, 8, 7, 3, 2]`。历史错误是将 BIGANN 派生数据标成独立 SIFT10M，该误标旧版本及其对应清单已于 2026-09-18 删除；当前这两行的维度与数量没有填错。
- `smoke03` 为小规模测试数据。

## 同时保留的原始 / 其他格式文件

| 文件或数据项 | 维度 / 每行元素数 | 向量数 / 行数 | 说明 |
|---|---:|---:|---|
| `data/sift10m/sift10m_base.u8bin` | 128 | 10,000,000 | uint8 底库版本 |
| `data/sift10m/sift10m_query.u8bin` | 128 | 10,000 | uint8 查询版本 |
| `data/deep1B/deep-image-96-angular.hdf5` → `train` | 96 | 9,990,000 | float32 底库 |
| 同一 HDF5 → `test` | 96 | 10,000 | float32 查询 |
| 同一 HDF5 → `neighbors` / `distances` | 100 | 各 10,000 | 邻居 ID / 距离，不是特征向量 |
| `data/msmarco-v2.1-embed-english-v3/passages_npy/*.npy` | 1,024 | 合计 113,520,750 | 60 个 float16 分片 |
| `data/msmarco-v2.1-embed-english-v3/queries_parquet/queries.parquet` → `emb` | 1,024 | 1,677 | 已核对全部查询的 embedding 长度 |

这些文件是对应数据的来源或其他存储格式，不应与上表相加当作独立向量总数。

## 归档 / 非正式派生数据

以下文件位于 `data/_nonofficial_derived/`，单独列出，避免与当前同名数据集混淆。

| 子目录 | 维度 | 底库向量数 | 查询向量数 | Ground truth 行数 × K |
|---|---:|---:|---:|---:|
| `bigann10m_from_sift10m_hardlinks` | 128 | 10,000,000 | 1,000 | 1,000 × 1,000 |
| `cohere10m_from_msmarco_prefix` | 1,024 | 10,000,000 | 913 | 913 × 100 |

归档的 `cohere10m_from_msmarco_prefix` 是 1,024 维 MS MARCO 子集，当前 `data/cohere10m/` 是 768 维。派生数据说明见各目录中的 README。

## 核对方法

- fvecs / ivecs：读取首行维度，按 `文件字节数 / (4 × (维度 + 1))` 计算记录数，并核对文件大小整除及末行维度头；当前数据集还核对了 base/query 维度一致、query/ground truth 行数一致。
- u8bin：读取头部的记录数和维度，并核对 `8 + 记录数 × 维度` 与文件大小一致。
- NPY：读取全部 60 个分片的 shape 和 dtype 元数据并汇总；HDF5 读取各数据项 shape；Parquet 读取行数元数据和全部 `emb` 列的长度。
- 这是文件规模核对，未全量扫描向量值，也未重算 ground truth。
