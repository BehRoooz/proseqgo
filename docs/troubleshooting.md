# Troubleshooting

Symptom → cause → fix guide for ProSeqGO. Organized by layer. For alert-specific first actions, see [monitoring.md](monitoring.md).

## Quick diagnostic commands

```bash
docker compose ps
docker compose logs --tail=100 <service>
curl -s http://localhost:9090/-/ready
curl -sk -u admin:PASS https://localhost/api/v1/health
curl -sk -u user:PASS https://localhost/api/predict/health
```

Replace `<service>` with: `nginx`, `embedding-api`, `embedding-worker`, `go-prediction-api`, `mlflow`, `postgres`, `redis`, `minio`, `trainer-api`.

---

## Startup failures

### Compose services won't start

**Symptoms:** `docker compose up` exits or containers restart loop.

**Checks:**

1. `.env` exists (`make ci-env`)
2. Required env vars set (Postgres, MinIO passwords)
3. Port conflicts on 80, 9090, 3000
4. `make gateway-auth` ran (htpasswd files exist)

**Fix:**

```bash
make ci-env && make gateway-auth
docker compose logs postgres minio mlflow
```

### `proseqgo_jobs` database does not exist

**Symptoms:** Embedding or training API fails with DB connection errors referencing `proseqgo_jobs`.

**Cause:** Postgres volume created before init script added the jobs database.

**Fix:**

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -c 'CREATE DATABASE proseqgo_jobs;'
```

Or tear down with volumes on a dev machine only: `make ci-down` (removes volumes).

### GPU not detected in containers

**Symptoms:** Slow inference; logs show CPU device.

**Checks:**

```bash
nvidia-smi
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config | grep -A2 gpus
```

**Fix:** Install NVIDIA Container Toolkit; use `make up` (auto-adds GPU overlay) or explicit GPU compose files.

---

## Authentication and gateway

### 401 Unauthorized from NGINX

**Cause:** Wrong credentials or missing htpasswd files.

**Fix:**

```bash
# Ensure .env has GATEWAY_ADMIN_* and GATEWAY_USER_*
make gateway-auth
docker compose restart nginx
```

Use admin credentials for `/api/v1/*` and `/mlflow/`; user credentials for `/api/predict/*`.

### 403 Forbidden

**Cause:** Correct auth tier but route requires different tier (e.g. user creds on admin route).

**Fix:** Match credential tier to route map in [deployment.md](deployment.md).

### 502 Bad Gateway / 504 Gateway Timeout

**Cause:** Upstream service down, slow, or unreachable from NGINX.

**Checks:**

```bash
docker compose ps embedding-api go-prediction-api mlflow
docker compose logs nginx --tail=50
```

**Fix:** Restart failed upstream. For long jobs, confirm 600s NGINX timeouts are sufficient; increase client `timeout_seconds` for sync predict-go calls.

### 413 Request Entity Too Large

**Cause:** Payload exceeds per-route `client_max_body_size`.

| Route | Limit |
|-------|-------|
| `/api/v1/predict-go-from-fasta` | 5 MB |
| `/api/predict/` | 8 MB |
| `/api/train` | 64 MB |
| `/api/v1/` (general) | 512 MB |

**Fix:** Reduce payload, use async `/api/v1/jobs/fasta` for large inputs, or split requests.

### 429 Too Many Requests

**Cause:** NGINX rate limit exceeded.

**Fix:** Back off and retry; adjust rate limit zones only in controlled environments.

### TLS / certificate errors

**Cause:** Self-signed cert on `https://localhost`.

**Fix:** Use `curl -k` or add cert to trust store for local dev. Production should use real certificates.

---

## Data issues

### Kaggle authentication fails

**Symptoms:** `403` or credential errors from `kaggle datasets download`.

**Fix:**

```bash
mkdir -p ~/.kaggle
chmod 600 ~/.kaggle/kaggle.json
```

Ensure API token is valid on kaggle.com → Account → API.

### Missing CAFA files

**Symptoms:** Preprocess or embed scripts fail with `FileNotFoundError`.

**Fix:** Download dataset per [data.md](data.md) and verify paths match `configs/config.yaml`.

### Checksum mismatch

**Symptoms:** sha256 does not match expected values in [data.md](data.md).

**Fix:** Re-download dataset; do not proceed with training until checksums match or divergence is documented in MLflow.

### Split / embedding mismatch

**Symptoms:** Training fails with shape errors or missing embedding files.

**Fix:** Regenerate embeddings for the same `ids.npy` and backend as configured:

```bash
python scripts/embed_sequences.py --config configs/config.yaml \
  --ids-npy outputs/splits/train_ids.npy --split train
```

---

## Training issues

### MLflow unreachable from CLI

**Symptoms:** Connection refused to `http://mlflow:5000` from host.

**Fix:** From host use gateway or published port mapping; inside containers use `http://mlflow:5000`. Set:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1/mlflow  # through gateway, with auth
# or direct if port-forwarded
```

For artifact upload, set `MLFLOW_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

### Training succeeds but no registered model

**Checks:**

1. MLflow logs in `scripts/train.py` output
2. MinIO bucket `mlflow-artifacts` exists (`minio-init` completed)
3. S3 credentials in environment

### Promotion rejected

**Symptoms:** `promote_model.py` prints metric below threshold; no `champion` update.

**Fix:** Expected behavior when `holdout_f1_micro < PROMOTION_THRESHOLD`. Lower threshold only with ML review, or improve model/data.

### Training API job stuck

**Checks:**

```bash
docker compose logs trainer-api trainer-worker
# Metrics: training queue depth in Grafana
```

**Fix:** Restart `trainer-worker`; check `TRAINING_JOB_TIMEOUT_SEC`; verify `data/` and `outputs/` mounts.

---

## Inference issues

### Empty or missing predictions

**Checks:**

1. `go-prediction-api` logs
2. Champion model exists in MLflow registry
3. Embedding dimension matches model input

### Wrong model version served

**Checks:**

```bash
# Response includes model_version field
curl -sk -u user:PASS -X POST http://localhost/api/predict/predict \
  -H "Content-Type: application/json" \
  -d '{"embedding": [...], "top_k": 5}'
```

**Fix:** Verify `champion` alias in MLflow UI; restart `go-prediction-api` after alias change.

### Embedding validation failures

**Symptoms:** 4xx from predict API; validation failure metrics increase.

**Cause:** Embedding length mismatch or NaN values.

**Fix:** Align `embedding.backend` with training; regenerate embeddings; check `go-prediction-api` logs for expected dimension.

### Sync predict-go timeout

**Symptoms:** 504 or client timeout on `/api/v1/predict-go-from-sequences`.

**Fix:**

- Increase `timeout_seconds` in request (max 7200)
- Use async flow: `POST /api/v1/jobs/fasta` → poll → `POST /api/v1/jobs/{id}/predict-go`
- Reduce batch size or sequence count

### Streamlit UI cannot reach API

**Checks:**

1. `streamlit-ui` env: `GATEWAY_BASE_URL=http://nginx`
2. `GATEWAY_USER` / `GATEWAY_USER_PASSWORD` match `make gateway-auth` output

---

## Monitoring gaps

### Prometheus target down

See [monitoring.md](monitoring.md) — verify container running and `/metrics` on internal port (8000 API, 8001 workers).

### Grafana shows no data

**Checks:**

1. Time range in Grafana
2. Datasource UID `prometheus` healthy
3. Query works in Prometheus UI directly
4. Training metrics absent if training profile not started (expected)

### Alerts never fire

**Checks:**

1. Evaluate rule expression in Prometheus graph
2. Confirm `for:` duration elapsed
3. Traffic floor on 5xx ratio rule (low traffic suppresses alert)

---

## Embedding worker crash recovery

**Symptoms:** Jobs stuck in `running` after worker kill.

**Expected behavior:** Worker startup requeues orphaned RQ jobs and resets Postgres status.

**Verify:**

```bash
./tests/smoke/test_embedding_worker_crash_recovery.sh
```

---

## Escalation path

| Severity | Action |
|----------|--------|
| Serving down | Check NGINX → upstream health → restart services → rollback model if recent promotion |
| Data corruption | Stop training jobs; restore Postgres/MinIO from backup |
| Security incident | Rotate gateway and DB passwords; review NGINX access logs |

## Related documentation

- [deployment.md](deployment.md) — setup and rollback
- [monitoring.md](monitoring.md) — alerts and dashboards
- [training.md](training.md) — promotion and MLflow workflow
- [data.md](data.md) — dataset download and validation
