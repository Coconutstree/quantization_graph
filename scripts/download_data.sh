#!/usr/bin/env bash
# Download and convert the three paper datasets (dbpedia / gist / agnews)
# into data/<dataset>/. Re-runnable: skips datasets that already exist.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
PY="${PYTHON:-python3}"

mkdir -p downloads data/dbpedia data/gist data/agnews

# 1) DBpedia-1M (1536-d, 990k base / 10k queries)
if [[ ! -f data/dbpedia/dbpedia_base.fvecs ]]; then
  echo "== dbpedia: download + convert =="
  if [[ ! -f downloads/dbpedia_openai_1M.tgz ]]; then
    wget -c https://storage.googleapis.com/ann-filtered-benchmark/datasets/dbpedia_openai_1M.tgz \
      -O downloads/dbpedia_openai_1M.tgz
  fi
  echo "dc5fecd77592b669643a5e1ea0541887  downloads/dbpedia_openai_1M.tgz" | md5sum -c -
  tar -xzf downloads/dbpedia_openai_1M.tgz -C downloads
  if [[ -f downloads/dbpedia_openai_1M/vectors.npy ]]; then
    mv downloads/dbpedia_openai_1M/vectors.npy data/dbpedia/
    mv downloads/dbpedia_openai_1M/tests.jsonl data/dbpedia/
  else
    mv downloads/vectors.npy data/dbpedia/
    mv downloads/tests.jsonl data/dbpedia/
  fi
  "${PY}" data/convert_dbpedia_npy_jsonl.py --data-dir data/dbpedia --prefix dbpedia
else
  echo "== dbpedia: already present, skip =="
fi

# 2) GIST-1M (960-d, 1M base / 1k queries)
if [[ ! -f data/gist/gist_base.fvecs ]]; then
  echo "== gist: download + convert =="
  if [[ ! -f data/gist/gist-960-euclidean.hdf5 ]]; then
    wget -c http://ann-benchmarks.com/gist-960-euclidean.hdf5 \
      -O data/gist/gist-960-euclidean.hdf5
  fi
  "${PY}" data/convert_hdf5_to_ann.py \
    --input data/gist/gist-960-euclidean.hdf5 \
    --output-dir data/gist --prefix gist
else
  echo "== gist: already present, skip =="
fi

# 3) AGNews (1024-d, 769,382 base / 1k queries), VIBE benchmark (HuggingFace)
if [[ ! -f data/agnews/agnews_base.fvecs ]]; then
  echo "== agnews: download + convert =="
  if [[ ! -f data/agnews/agnews-mxbai-1024-euclidean.hdf5 ]]; then
    wget -c https://huggingface.co/datasets/vector-index-bench/vibe/resolve/main/agnews-mxbai-1024-euclidean.hdf5 \
      -O data/agnews/agnews-mxbai-1024-euclidean.hdf5
  fi
  "${PY}" data/convert_hdf5_to_ann.py \
    --input data/agnews/agnews-mxbai-1024-euclidean.hdf5 \
    --output-dir data/agnews --prefix agnews
else
  echo "== agnews: already present, skip =="
fi

echo "== validate =="
"${PY}" scripts/check_datasets.py --datasets dbpedia gist agnews \
  --data-root data --out-root results
echo "all datasets ready under data/"
