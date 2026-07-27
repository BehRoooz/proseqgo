#!/usr/bin/env bash
# Smoke test for the Embedding API (direct uvicorn or via nginx gateway on port 80).
# Usage (from repo root):
#   make gateway-auth          # once: sync nginx htpasswd from .env
#   ./tests/smoke/smoke_embedding_api.sh
#   BASE_URL=http://127.0.0.1:8000 ./tests/smoke/smoke_embedding_api.sh
#
# Credentials come from .env:
#   GATEWAY_ADMIN_* → /api/v1/health, /api/v1/jobs, artifacts
#   GATEWAY_USER_*  → /api/v1/predict-go-from-fasta
# Overrides: ADMIN_USER/ADMIN_PASS, API_USER/API_PASS (predict user).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../scripts/load_gateway_env.sh
source "${REPO_ROOT}/scripts/load_gateway_env.sh"
load_gateway_env "${REPO_ROOT}"

BASE_URL="${BASE_URL:-http://localhost}"
FASTA_EXAMPLE="${REPO_ROOT}/examples/small_sequences.fasta"
MAX_FASTA_UPLOAD_BYTES=$((5 * 1024 * 1024))

ADMIN_USER="${ADMIN_USER:-${GATEWAY_ADMIN_USER}}"
ADMIN_PASS="${ADMIN_PASS:-${GATEWAY_ADMIN_PASSWORD}}"
API_USER="${API_USER:-${GATEWAY_USER}}"
API_PASS="${API_PASS:-${GATEWAY_USER_PASSWORD}}"

CURL_BASE=(-sS)
if [[ -n "${CURL_INSECURE:-}" ]]; then
  CURL_BASE+=(-k)
fi
ADMIN_CURL=("${CURL_BASE[@]}" -u "${ADMIN_USER}:${ADMIN_PASS}")
USER_CURL=("${CURL_BASE[@]}" -u "${API_USER}:${API_PASS}")

echo "==> Health: GET ${BASE_URL}/api/v1/health (admin)"
curl "${ADMIN_CURL[@]}" "${BASE_URL}/api/v1/health"
echo

echo "==> Submit job: POST ${BASE_URL}/api/v1/jobs (admin)"
RESP="$(curl "${ADMIN_CURL[@]}" -X POST "${BASE_URL}/api/v1/jobs" \
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
  ST="$(curl "${ADMIN_CURL[@]}" "${BASE_URL}/api/v1/jobs/${JOB_ID}")"
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
curl "${ADMIN_CURL[@]}" -o "${OUT_DIR}/test_ids.npy" \
  "${BASE_URL}/api/v1/jobs/${JOB_ID}/artifacts/test_ids.npy"
curl "${ADMIN_CURL[@]}" -o "${OUT_DIR}/test_embeddings.npy" \
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

echo "==> Predict GO from FASTA: POST ${BASE_URL}/api/v1/predict-go-from-fasta (user)"
PRED_RESP="$(curl "${USER_CURL[@]}" --max-time 1800 -X POST \
  "${BASE_URL}/api/v1/predict-go-from-fasta" \
  -F "fasta_file=@${FASTA_EXAMPLE}" \
  -F "backend=esm2" \
  -F "pooling=mean" \
  -F "batch_size=2" \
  -F "max_length=1280" \
  -F "top_k=10" \
  -F "fail_fast=true")"
echo "$PRED_RESP"


PRED_RESP="$PRED_RESP" python3 -c '
import json, os
data = json.loads(os.environ["PRED_RESP"])

assert data["status"] == "succeeded", data
results = data["results"]
assert len(results) == 2, results
for item in results:
    assert item.get("sequence_id"), item
    assert "predictions" in item and isinstance(item["predictions"], list), item
print("predict-go-from-fasta OK:", [r["sequence_id"] for r in results])
'

echo "==> FASTA upload too large: expect HTTP 413 (max ${MAX_FASTA_UPLOAD_BYTES} bytes)"
LARGE_FASTA="$(mktemp)"
python3 -c "import sys; sys.stdout.buffer.write(b'x' * (${MAX_FASTA_UPLOAD_BYTES} + 1))" >"${LARGE_FASTA}"
HTTP_CODE="$(curl "${USER_CURL[@]}" -o /dev/null -w "%{http_code}" -X POST \
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
