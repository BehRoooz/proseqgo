#!/usr/bin/env bash
# Crash-recovery acceptance test for embedding RQ worker.
#
# What: submit a job, hard-kill the worker mid-job, restart worker, confirm
#       the durable Postgres job is requeued/retries and does NOT stay
#       stuck as running forever.
#
# Prerequisites: docker compose stack with embedding-api, embedding-worker,
# postgres, and redis running.
#
# Usage (from repo root):
#   ./tests/smoke/test_embedding_worker_crash_recovery.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose)
POSTGRES_USER="${POSTGRES_USER:-mlflow}"

post_job() {
  "${COMPOSE[@]}" exec -T embedding-api python - <<'PY'
import json, urllib.request
payload = {
    "stage": "test",
    "backend": "esm2",
    "pooling": "mean",
    "batch_size": 1,
    "max_length": 1280,
    "sequences": [
      {"id": "crash-test-1", "sequence": "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTLGQHDFSAGEGDPDVELWKGIQ"},
      {"id": "crash-test-2", "sequence": "MKTVRQERLKSIVRILERSKEPVSGAQLAEELSVSRQVIVQDIAYLRSLGYNIVATPRGYVLAGG"},
      {"id": "crash-test-3", "sequence": "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSHGSAQVKGHGKKVADALTNAVAHVDDMPNALSALSDLHAHKLRVDPVNFKLLSHCLLVTLAAHLPAEFTPAVHASLDKFLASVSTVLTSKYR"}
    ],
}
data = json.dumps(payload).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/jobs",
    data=data,
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=30) as resp:
    print(resp.read().decode())
PY
}

get_status() {
  local job_id="$1"
  "${COMPOSE[@]}" exec -T embedding-api python - <<PY
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/jobs/${job_id}", timeout=30) as resp:
    print(json.load(resp)["status"])
PY
}

echo "==> Health check"
"${COMPOSE[@]}" exec -T embedding-api python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health", timeout=30).read().decode())
PY

echo "==> Ensuring worker has a kill window after mark_running"
export EMBEDDING_JOB_START_DELAY_SEC="${EMBEDDING_JOB_START_DELAY_SEC:-45}"
"${COMPOSE[@]}" up -d --no-deps --force-recreate embedding-worker
sleep 5

echo "==> Submitting embedding job"
JOB_JSON="$(post_job)"
JOB_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$JOB_JSON")"
echo "    job_id=${JOB_ID}"

echo "==> Waiting until status=running (timeout 120s)"
deadline=$((SECONDS + 120))
status="queued"
while (( SECONDS < deadline )); do
  status="$(get_status "$JOB_ID" | tr -d '\r')"
  echo "    status=${status}"
  if [[ "$status" == "running" ]]; then
    break
  fi
  if [[ "$status" == "succeeded" || "$status" == "failed" ]]; then
    echo "Job finished before kill window (status=${status}). Increase EMBEDDING_JOB_START_DELAY_SEC and re-run."
    exit 2
  fi
  sleep 1
done
if [[ "$status" != "running" ]]; then
  echo "Timed out waiting for running"
  exit 1
fi

echo "==> Hard-killing embedding-worker (SIGKILL)"
"${COMPOSE[@]}" kill -s SIGKILL embedding-worker

echo "==> Confirm Postgres status after kill"
pg_status="$("${COMPOSE[@]}" exec -T postgres \
  psql -U "$POSTGRES_USER" -d proseqgo_jobs -Atc \
  "SELECT status FROM embedding_jobs WHERE job_id='${JOB_ID}';" || true)"
echo "    postgres status after kill: ${pg_status:-<unknown>}"

echo "==> Restarting embedding-worker (orphan requeue on startup)"
"${COMPOSE[@]}" up -d --no-deps embedding-worker

echo "==> Waiting for recovery (queued->running->succeeded|failed), timeout 600s"
deadline=$((SECONDS + 600))
while (( SECONDS < deadline )); do
  status="$(get_status "$JOB_ID" | tr -d '\r')"
  echo "    status=${status}"
  if [[ "$status" == "succeeded" || "$status" == "failed" ]]; then
    echo "==> PASS: job reached terminal status=${status} (did not hang as running forever)"
    exit 0
  fi
  sleep 2
done

echo "==> FAIL: job did not reach a terminal state within timeout (last status=${status})"
exit 1
