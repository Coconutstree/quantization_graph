#!/usr/bin/env bash
# Download and convert supported HDF5 datasets (gist / agnews).
# into data/<dataset>/. Re-runnable: skips datasets that already exist.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
PY="${PYTHON:-python3}"

DATASETS="${DATASETS:-gist agnews}"
for dataset in $DATASETS; do
  case "$dataset" in
    gist|agnews) ;;
    *) echo "Unsupported automatic conversion: $dataset; see docs/REPRODUCING.md" >&2; exit 2 ;;
  esac
done
selected() { [[ " $DATASETS " == *" $1 "* ]]; }
mkdir -p downloads

# 2) GIST-1M (960-d, 1M base / 1k queries)
if selected gist; then
mkdir -p data/gist
if [[ ! -f data/gist/gist_base.fvecs || ! -f data/gist/gist_query.fvecs || ! -f data/gist/gist_groundtruth.ivecs ]]; then
  echo "== gist: download + convert =="
  if [[ ! -f data/gist/gist-960-euclidean.hdf5 ]]; then
    wget -c http://ann-benchmarks.com/gist-960-euclidean.hdf5 \
      -O data/gist/gist-960-euclidean.hdf5
  fi
  "${PY}" scripts/data_tools/convert_hdf5_to_ann.py \
    --input data/gist/gist-960-euclidean.hdf5 \
    --output-dir data/gist --prefix gist
else
  echo "== gist: already present, skip =="
fi

fi

# 3) AGNews (1024-d, 769,382 base / 1k queries), VIBE benchmark (HuggingFace)
if selected agnews; then
mkdir -p data/agnews
if [[ ! -f data/agnews/agnews_base.fvecs || ! -f data/agnews/agnews_query.fvecs || ! -f data/agnews/agnews_groundtruth.ivecs ]]; then
  echo "== agnews: download + convert =="
  if [[ ! -f data/agnews/agnews-mxbai-1024-euclidean.hdf5 ]]; then
    wget -c https://huggingface.co/datasets/vector-index-bench/vibe/resolve/main/agnews-mxbai-1024-euclidean.hdf5 \
      -O data/agnews/agnews-mxbai-1024-euclidean.hdf5
  fi
  "${PY}" scripts/data_tools/convert_hdf5_to_ann.py \
    --input data/agnews/agnews-mxbai-1024-euclidean.hdf5 \
    --output-dir data/agnews --prefix agnews
else
  echo "== agnews: already present, skip =="
fi

fi

echo "== validate =="
read -r -a selected_datasets <<< "$DATASETS"
"${PY}" scripts/check_datasets.py --datasets "${selected_datasets[@]}" --data-root data --out-root artifacts/dataset_manifests
