#!/usr/bin/env bash
# Smoke test for the Embedding API (direct uvicorn or via nginx gateway on port 80).
# Usage:
#   ./scripts/smoke_embedding_api.sh
#   BASE_URL=http://127.0.0.1:8000 ./scripts/smoke_embedding_api.sh   # direct service port
#   CURL_INSECURE=1 API_USER=user API_PASS=secret \
#     BASE_URL=https://localhost ./scripts/smoke_embedding_api.sh     # docker compose (nginx)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1}"
FASTA_EXAMPLE="${REPO_ROOT}/examples/small_sequences.fasta"
MAX_FASTA_UPLOAD_BYTES=$((5 * 1024 * 1024))

CURL_OPTS=(-sS)
if [[ -n "${CURL_INSECURE:-}" ]]; then
  CURL_OPTS+=(-k)
fi
if [[ -n "${API_USER:-}" && -n "${API_PASS:-}" ]]; then
  CURL_OPTS+=(-u "${API_USER}:${API_PASS}")
fi

echo "==> Health: GET ${BASE_URL}/api/v1/health"
curl "${CURL_OPTS[@]}" "${BASE_URL}/api/v1/health"
echo

echo "==> Submit job: POST ${BASE_URL}/api/v1/jobs"
RESP="$(curl "${CURL_OPTS[@]}" -X POST "${BASE_URL}/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "stage": "test",
    "backend": "esm2",
    "pooling": "mean",
    "batch_size": 2,
    "max_length": 1280,
    "sequences": [
      {"id": "smoke_P1", "sequence": "MKTAYIAKQRQISFVKSHFSRQ"},
      {"id": "smoke_P2", "sequence": "GAVLIPFYWSTCMNQDEKRH"}
    ]
  }')"
echo "$RESP"

JOB_ID="$(printf '%s' "$RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_id'])")"
echo "==> Job ID: ${JOB_ID}"

echo "==> Poll until succeeded (max ~120s)"
for _ in $(seq 1 60); do
  ST="$(curl "${CURL_OPTS[@]}" "${BASE_URL}/api/v1/jobs/${JOB_ID}")"
  STATUS="$(printf '%s' "$ST" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")"
  if [[ "$STATUS" == "succeeded" ]]; then
    echo "$ST" | python3 -m json.tool
    break
  fi
  if [[ "$STATUS" == "failed" ]]; then
    echo "$ST" | python3 -m json.tool
    exit 1
  fi
  sleep 2
done

if [[ "${STATUS:-}" != "succeeded" ]]; then
  echo "Timed out waiting for job ${JOB_ID}"
  exit 1
fi

OUT_DIR="$(mktemp -d)"
echo "==> Download artifacts to ${OUT_DIR}"
curl "${CURL_OPTS[@]}" -o "${OUT_DIR}/test_ids.npy" \
  "${BASE_URL}/api/v1/jobs/${JOB_ID}/artifacts/test_ids.npy"
curl "${CURL_OPTS[@]}" -o "${OUT_DIR}/test_embeddings.npy" \
  "${BASE_URL}/api/v1/jobs/${JOB_ID}/artifacts/test_embeddings.npy"

echo "==> Verify shapes (expect N=2, D=1280 for esm2, float32)"
python3 <<PY
import numpy as np
import pathlib
d = pathlib.Path("${OUT_DIR}")
emb = np.load(d / "test_embeddings.npy")
ids = np.load(d / "test_ids.npy", allow_pickle=True)
print("test_embeddings.npy:", emb.shape, emb.dtype)
print("test_ids.npy:", ids.shape, ids.dtype)
assert emb.ndim == 2 and emb.shape[0] == len(ids) == 2
assert emb.shape[1] == 1280
assert str(emb.dtype) == "float32"
print("OK")
PY

echo "==> Predict GO from FASTA: POST ${BASE_URL}/api/v1/predict-go-from-fasta"
PRED_RESP="$(curl "${CURL_OPTS[@]}" --max-time 1800 -X POST \
  "${BASE_URL}/api/v1/predict-go-from-fasta" \
  -F "fasta_file=@${FASTA_EXAMPLE}" \
  -F "backend=esm2" \
  -F "pooling=mean" \
  -F "batch_size=2" \
  -F "max_length=1280" \
  -F "top_k=10" \
  -F "fail_fast=true")"
echo "$PRED_RESP"

printf '%s' "$PRED_RESP" | python3 <<'PY'
import json
import sys

data = json.load(sys.stdin)
assert data["status"] == "succeeded", data
results = data["results"]
assert len(results) == 2, results
for item in results:
    assert item.get("sequence_id"), item
    assert "predictions" in item and isinstance(item["predictions"], list), item
print("predict-go-from-fasta OK:", [r["sequence_id"] for r in results])
PY

echo "==> FASTA upload too large: expect HTTP 413 (max ${MAX_FASTA_UPLOAD_BYTES} bytes)"
LARGE_FASTA="$(mktemp)"
python3 -c "import sys; sys.stdout.buffer.write(b'x' * (${MAX_FASTA_UPLOAD_BYTES} + 1))" >"${LARGE_FASTA}"
HTTP_CODE="$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/predict-go-from-fasta" \
  -F "fasta_file=@${LARGE_FASTA};type=text/plain" \
  -F "backend=esm2")"
rm -f "${LARGE_FASTA}"
if [[ "${HTTP_CODE}" != "413" ]]; then
  echo "Expected HTTP 413 for oversized FASTA upload, got ${HTTP_CODE}"
  exit 1
fi
echo "FASTA_FILE_TOO_LARGE OK (HTTP 413)"

echo "==> Smoke test passed."
