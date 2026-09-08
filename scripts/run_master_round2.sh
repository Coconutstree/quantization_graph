#!/usr/bin/env bash
# Full three-suite reproduction: 01 quantizer fair -> 02 payload fair ->
# 03 system fair, per dataset (agnews -> gist -> dbpedia).
# All outputs go to ${OUT_ROOT}/<suite>/<dataset>/ (suite-first, logs/ not raw/).
#
# Usage (OUT_ROOT / PYTHON / DATASETS / M / L are optional env vars; defaults: M=64, L=400, all datasets):
#   OUT_ROOT=results/disk_environment PYTHON=python3 bash scripts/run_master_round2.sh
#   DATASETS=agnews bash scripts/run_master_round2.sh
#   DATASETS="agnews gist" M=32 L=200 bash scripts/run_master_round2.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}" || exit 1

PY="${PYTHON:-python3}"
BIN01="${BIN01:-${ROOT}/build/formal_local/01_quantizer_fair/faiss_quantizer_smoke}"
BIN01_CAND="${BIN01_CAND:-${ROOT}/build/formal_local/01_quantizer_fair/faiss_hard_negative_candidates}"
BIN02="${BIN02:-${ROOT}/experiments/02_diskann_fair/target/release/run_diskann_fair}"
OUT="${OUT_ROOT:-results/disk_environment}"
LOG_DIR="${ROOT}/logs"
STATUS_LOG="${LOG_DIR}/round2.status"
RERANK="10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460,1000"
M="${M:-64}"
L="${L:-400}"
DATASETS="${DATASETS:-agnews gist dbpedia}"
THREADS="${THREADS:-32}"
LOCAL_PREFIX="${ROOT}/baselines/deps/local"
LOCAL_LIB="${LOCAL_PREFIX}/usr/lib/x86_64-linux-gnu"
LOCAL_OPENBLAS_LIB="${LOCAL_LIB}/openblas-pthread"
export LD_LIBRARY_PATH="${LOCAL_LIB}:${LOCAL_OPENBLAS_LIB}:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="${LOCAL_LIB}:${LOCAL_OPENBLAS_LIB}:${LIBRARY_PATH:-}"
export CPLUS_INCLUDE_PATH="${LOCAL_PREFIX}/usr/include:${CPLUS_INCLUDE_PATH:-}"

log() { echo "[r2] $(date '+%F %T') $*" | tee -a "${STATUS_LOG}"; }
mkdir -p "${LOG_DIR}" "${OUT}"
echo $$ > "${LOG_DIR}/round2.pid"

run_01() {
  local ds="$1"
  log "== 01 ${ds}: PQ/SQ/Ours_K1 =="
  # shellcheck source=/dev/null
  source experiments/01_quantizer_fair/configs/formal_faiss_4bit.env
  local workdir="work/01_quantizer_fair/${ds}"
  mkdir -p "${workdir}"
  if [[ "${REBUILD_CANDIDATES:-0}" != "1" \
        && -f "${workdir}/fixed_candidates_k${CANDIDATE_SIZE}.bin" \
        && -f "${workdir}/fixed_candidates_k${CANDIDATE_SIZE}.meta.json" ]]; then
    echo "${ds}: reuse ${workdir}/fixed_candidates_k${CANDIDATE_SIZE}.bin"
  else
    "${BIN01_CAND}" \
      --dataset "${ds}" --data-root "${ROOT}/data" --out-root "${OUT}" \
      --candidate-root work --candidate-size "${CANDIDATE_SIZE}" \
      --search-k "${HARD_NEGATIVE_SEARCH_K}" --hnsw-M "${HARD_NEGATIVE_HNSW_M}" \
      --efConstruction "${HARD_NEGATIVE_EF_CONSTRUCTION}" \
      --efSearch "${HARD_NEGATIVE_EF_SEARCH}" \
      --seed "${SEED}" --force \
      > "${LOG_DIR}/round2_01_${ds}_candidates.log" 2>&1 \
      || log "01 ${ds} candidates FAILED"
  fi
  local one_logs="${OUT}/01_quantizer_fair/${ds}/logs"
  mkdir -p "${one_logs}"
  "${BIN01}" \
    --dataset "${ds}" --data-root "${ROOT}/data" --out-root "${OUT}" \
    --candidate-root work --candidate-size "${CANDIDATE_SIZE}" \
    --max-train "${MAX_TRAIN}" --max-queries "${MAX_QUERIES}" \
    --methods PQ,SQ,Ours_RaBitQ_K1 --rerank-candidates "${RERANK}" \
    --repeat-id "${REPEAT_ID}" --seed "${SEED}" --overwrite-summary \
    2>&1 | tee "${LOG_DIR}/round2_01_${ds}_pqsqours.log" > "${one_logs}/faiss_quantizer_smoke.log" \
    || log "01 ${ds} PQ/SQ/Ours FAILED"
  # 按方法拆分原始日志，与 SAQ 的 logs/SAQ 布局一致
  mkdir -p "${one_logs}/PQ" "${one_logs}/SQ" "${one_logs}/Ours_RaBitQ_K1"
  grep -E '^PQ ' "${one_logs}/faiss_quantizer_smoke.log" > "${one_logs}/PQ/PQ.log" || true
  grep -E '^SQ ' "${one_logs}/faiss_quantizer_smoke.log" > "${one_logs}/SQ/SQ.log" || true
  grep -E '^Ours_RaBitQ_K1 ' "${one_logs}/faiss_quantizer_smoke.log" > "${one_logs}/Ours_RaBitQ_K1/Ours.log" || true
  log "== 01 ${ds}: SAQ =="
  DATASETS="${ds}" RERANK_CANDIDATES="${RERANK}" OUT_ROOT="${OUT}" WORK_ROOT="work" \
    SAQ_CXX_BIN="$(command -v g++)" \
    SAQ_CMAKE_MODULE_PATH="${LOCAL_PREFIX}/usr/share/glog/cmake" \
    SAQ_UNWIND_INCLUDE_DIR="${LOCAL_PREFIX}/usr/include" \
    SAQ_UNWIND_LIBRARY="${LOCAL_LIB}/libunwind.so" \
    bash scripts/run_saq_fixed_candidates_fair.sh \
    > "${LOG_DIR}/round2_01_${ds}_saq.log" 2>&1 \
    || log "01 ${ds} SAQ FAILED"
  # Pad short SAQ rows to the current summary schema (inner-product columns).
  "${PY}" - "${OUT}/01_quantizer_fair/${ds}/csv/faiss_quantizer_summary.csv" <<'EOF'
import csv
import sys
from pathlib import Path

p = Path(sys.argv[1])
if not p.exists():
    sys.exit(0)
rows = list(csv.reader(p.open()))
if not rows:
    sys.exit(0)
width = len(rows[0])
changed = 0
out = []
for row in rows:
    if len(row) < width:
        row = row + [""] * (width - len(row))
        changed += 1
    out.append(row)
if changed:
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(out)
print(f"padded {changed} short rows in {p}")
EOF
  log "== 01 ${ds}: export paper table =="
  "${PY}" scripts/export_paper_quantizer_table.py \
    --datasets "${ds}" --out-root "${OUT}" --work-root work \
    >> "${LOG_DIR}/round2_01_${ds}_pqsqours.log" 2>&1 \
    || log "01 ${ds} export FAILED"
  log "01 ${ds} done"
}

run_02() {
  local ds="$1" valq="$2"
  log "== 02 ${ds}: per-method quantized graphs R=${M}/L=${L} (PQ,SQ,SAQ,Ours) =="
  local split_root="${OUT}/03_system_fair/${ds}/csv/_query_splits"
  mkdir -p "${split_root}"
  "${PY}" - "${ds}" "${valq}" "${ROOT}/data" <<'EOF'
import sys
from pathlib import Path
from Ours.experiments.run_ours import prepare_query_splits

ds, valq, data_root = sys.argv[1], int(sys.argv[2]), sys.argv[3]
prepare_query_splits(ds, Path(data_root), Path("results/disk_environment"), valq)
print(f"query splits ready: {ds} val={valq}")
EOF
  "${BIN02}" \
    --dataset "${ds}" --methods PQ,SQ,SAQ,Ours --max-degree "${M}" --build-beam "${L}" \
    --query-coarse-codec int8 \
    --out-root "${OUT}" --repeats 1 --threads "${THREADS}" --refine-passes 1 --build-prune-cap 256 --build-early-stop-hops 2 \
    --query-path "${split_root}/test_query.fvecs" \
    --gt-path "${split_root}/test_gt.ivecs" \
    > "${LOG_DIR}/round2_02_${ds}.log" 2>&1 \
    || log "02 ${ds} FAILED"
  log "02 ${ds} done"
}

run_03() {
  local ds="$1" valq="$2"
  log "== 03 ${ds}: system fair (Ours,SymphonyQG,OG-LVQ,Glass-NSG) =="

  # 固定 03 配置（Ours M=64；SymphonyQG R=64/EF=400；OG-LVQ R=64/W=400；
  # Glass-NSG R=64/L=400）：直接写死 selected config，跳过验证自动选参，
  # 保证四个系统同参数（M/R=64、L/EF/W=400）对比。
  "${PY}" - "${OUT}/03_system_fair/${ds}/csv/tuning" "${M}" "${L}" <<'EOF'
import json
import sys
from pathlib import Path

tuning = Path(sys.argv[1])
m = int(sys.argv[2])
l = int(sys.argv[3])
tuning.mkdir(parents=True, exist_ok=True)
fixed = {
    "Ours": {
        "config_id": f"OursDiskANN_M{m}",
        "M": m, "R": m, "L_build": l, "alpha": 1.2,
        "rerank_candidates": 100, "residual_bits": 4, "centroid_count": 1,
        "query_coarse_codec": "int8",
        "selection_note": f"fixed M={m}/L={l}",
    },
    "SymphonyQG": {
        "config_id": f"R{m}_EF{l}_t3", "R": m, "EF": l, "iters": 3,
        "selection_note": f"fixed R={m}/EF={l}",
    },
    "OG-LVQ": {
        "config_id": f"LVQ4_R{m}_W{l}",
        "R": m, "W": l, "alpha": 1.2, "primary": 4, "residual": 0,
        "selection_note": f"fixed R={m}/W={l}",
    },
    "Glass-NSG": {
        "config_id": f"R{m}_L{l}", "R": m, "L": l,
        "selection_note": f"fixed R={m}/L={l}",
    },
}
for method, cfg in fixed.items():
    (tuning / f"{method}_selected_config.json").write_text(
        json.dumps(
            {
                "method": method,
                "selected_config": cfg,
                "status": "ok",
                "recall_target": 0.95,
                "fixed": True,
            },
            indent=2,
        )
    )
print(f"fixed 03 configs written to {tuning}")
EOF
  "${PY}" -u experiments/03_system_fair/run_system_fair.py \
    --dataset "${ds}" --systems Ours,SymphonyQG,OG-LVQ,Glass-NSG \
    --run --repeats 1 --threads "${THREADS}" --val-queries "${valq}" --out-root "${OUT}" \
    > "${LOG_DIR}/round2_03_${ds}.log" 2>&1 \
    || log "03 ${ds} FAILED"
  "${PY}" scripts/plot_system_fair.py --dataset "${ds}" --out-root "${OUT}" \
    >> "${LOG_DIR}/round2_03_${ds}.log" 2>&1 \
    || log "03 ${ds} plots FAILED"
  "${PY}" scripts/consolidate_system_logs.py --dataset "${ds}" --out-root "${OUT}" \
    >> "${LOG_DIR}/round2_03_${ds}.log" 2>&1 \
    || log "03 ${ds} consolidate FAILED"
  "${PY}" scripts/generate_experiment_audit.py --dataset "${ds}" --out-root "${OUT}" \
    >> "${LOG_DIR}/round2_03_${ds}.log" 2>&1 \
    || log "03 ${ds} audit FAILED"
  log "03 ${ds} done"
}

log "started round2 (out-root=${OUT}) datasets=${DATASETS} M=${M} L=${L}"
valq_for() {
  case "$1" in
    agnews|gist) echo 200 ;;
    dbpedia) echo 1000 ;;
    *) echo 200 ;;
  esac
}
for DS in ${DATASETS}; do
  VALQ="$(valq_for "${DS}")"
  log "######## dataset ${DS} (val_queries=${VALQ}) ########"
  run_01 "${DS}"
  run_02 "${DS}" "${VALQ}"
  run_03 "${DS}" "${VALQ}"
done
log "ALL DONE (round2)"
