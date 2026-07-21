# Training API

HTTP service that queues **model training** and **retraining** jobs and runs them in a background worker. Each job executes the same CLI entrypoints used for local development (`scripts/train.py` or `scripts/retrain_pipeline.py`), logs to **MLflow**, and can register the PyTorch model in the **Model Registry**.

In the default Docker Compose stack, the Training API is **not** exposed on a host port directly. Traffic goes through the **NGINX gateway** on port **80** with HTTP Basic Auth.

## What this service does

- Accepts async training job requests (`POST /api/train/train`).
- Persists job state in a SQLite database (`outputs/training_api/jobs.db`).
- Runs one job at a time in a background worker thread.
- Invokes the same training scripts as the CLI — no separate training logic in the API layer.
- Returns MLflow run IDs, registry version, and browser-friendly MLflow UI links when a job succeeds.

## Architecture

```text
Client (curl / automation)
        |
        v
  NGINX gateway :80          (Basic Auth: admin tier)
        |
        v
  trainer-api :8000          (FastAPI — POST /api/train/train)
        |
        +--> JobStore (SQLite)     queued / running / succeeded / failed
        |
        +--> Worker thread         picks next queued job (FIFO)
                 |
                 +-- mode=train    --> scripts/train.py
                 |
                 +-- mode=retrain  --> scripts/retrain_pipeline.py
                                           |
                                           +--> scripts/train.py
                                           +--> scripts/evaluate_holdout.py
                                           +--> scripts/promote_model.py
                 |
                 v
           outputs/train_run_summary.json   (read by worker on success)
                 |
                 v
           MLflow tracking + Model Registry
```

### Components

| Component | Role |
|-----------|------|
| `main.py` | FastAPI app: health, submit job, poll status, Prometheus `/metrics` |
| `worker.py` | Background loop; spawns training subprocess; parses summary JSON |
| `job_store.py` | SQLite persistence for job lifecycle and results |
| `config.py` | `API_PREFIX`, artifact paths, MLflow defaults |

Jobs are processed **sequentially** (one running job at a time). Additional submissions are queued in arrival order.

## Training vs retraining

The API exposes a single submit endpoint. The behavior is selected by the `mode` field in the request body.

### `mode: "train"` — training only

Runs `scripts/train.py` with the given config. This pipeline:

1. Loads embeddings, labels, and train/val splits from paths in `configs/config.yaml`.
2. Trains the multi-label GO predictor (CNN/MLP).
3. Logs metrics, checkpoints, and artifacts to MLflow experiment `cafa-train`.
4. Registers a new model version in MLflow Model Registry (default name: `cafa-go-model`).
5. Writes `outputs/train_run_summary.json` with `train_run_id`, `model_uri`, and registry fields.

Use this when you want to **train and register** a model without automatic holdout evaluation or champion promotion.

### `mode: "retrain"` — full retraining pipeline (default)

Runs `scripts/retrain_pipeline.py`, which chains three steps:

```text
train.py  -->  evaluate_holdout.py  -->  promote_model.py
   |                    |                        |
   v                    v                        v
train_run_summary   holdout_eval_summary    champion alias
```

1. **Train** — same as `mode: "train"` above.
2. **Holdout evaluation** — `scripts/evaluate_holdout.py` scores the trained model on the holdout split and logs metrics to MLflow.
3. **Promotion** — `scripts/promote_model.py` compares holdout F1 against `PROMOTION_THRESHOLD` (default `0.35`). If the metric passes, it sets the `champion` alias on the registered model version so `go-prediction-api` can serve it via `models:/cafa-go-model@champion`.

Use this for **production-style retraining**: train, evaluate, and optionally promote in one job.

| | `train` | `retrain` |
|---|---------|-----------|
| Script | `scripts/train.py` | `scripts/retrain_pipeline.py` |
| MLflow training run | yes | yes |
| Holdout evaluation | no | yes |
| Champion promotion | no | yes (if metric ≥ threshold) |
| Default in API schema | no | **yes** |

## Prerequisites

Training expects the same artifacts as local CLI training. Prepare these **before** submitting API jobs:

1. **Labels and splits** — run preprocessing so `outputs/splits/` and label matrices exist.
2. **Embeddings** — generate train/val/holdout embeddings for the backend in config (e.g. ESM2 under `data/embeddings/`).
3. **Writable volumes** — `outputs/` must be writable inside the container (mounted from the repo in Compose). MLflow metadata and artifacts are stored in Postgres + MinIO (no local `mlruns/` mount).
4. **MLflow** — the `mlflow` service must be running (started by default in Compose).

See the [repository README](../../README.md) for the full offline data-prep workflow.

## How to run

### Docker Compose (recommended)

From the **repository root**:

```bash
# Core stack (nginx, embedding-api, go-prediction-api, mlflow, streamlit-ui)
docker compose up --build

# Add the training API (optional profile)
docker compose --profile training up -d --build
```

Or:

```bash
make training-up
```

This starts `trainer-api` on the internal Docker network. Access it through the gateway:

| Route | Auth | Upstream |
|-------|------|----------|
| `http://localhost/api/train/*` | admin (`.htpasswd-admin`) | `trainer-api:8000` |

Check health:

```bash
curl -sk -u admin:YOUR_PASSWORD http://localhost/api/train/health
```

Example response:

```json
{
  "status": "ok",
  "device": "cuda",
  "cuda_available": true,
  "cafa_device": "auto",
  "cuda_device_name": "NVIDIA GeForce RTX 4080 Laptop GPU"
}
```

> **Note:** On some Docker Desktop setups, use `http://localhost` rather than `http://127.0.0.1` — IPv6 loopback (`::1`) may work while IPv4 loopback does not for gateway port 80.

### Local Uvicorn (development)

From the **repository root**, with dependencies installed:

```bash
export PYTHONPATH="$(pwd)"
export MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-http://127.0.0.1:5000}"
uvicorn main:app --app-dir services/training-api --host 0.0.0.0 --port 8002 --reload
```

Health (no gateway auth in this mode):

```bash
curl -sS http://127.0.0.1:8002/api/train/health
```

Start an MLflow server separately if you use an HTTP tracking URI.

## API reference

Route prefix: **`/api/train`** (set in `config.py` as `API_PREFIX`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/train/health` | Liveness + device info |
| POST | `/api/train/train` | Submit a training job (202 + `job_id`) |
| GET | `/api/train/jobs/{job_id}` | Poll job status, progress, errors, MLflow fields |
| GET | `/metrics` | Prometheus metrics (internal; not behind gateway auth) |

### Request body: `POST /api/train/train`

```json
{
  "config": "configs/config.yaml",
  "mode": "retrain"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `config` | string | `configs/config.yaml` | Path to YAML config **inside the container** (`/app/...`) |
| `mode` | `"train"` \| `"retrain"` | `"retrain"` | See [Training vs retraining](#training-vs-retraining) |

**Response (202 Accepted):**

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued",
  "poll_url": "/api/train/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### Job status: `GET /api/train/jobs/{job_id}`

`status` lifecycle: `queued` → `running` → `succeeded` | `failed`

**Queued / running example:**

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "mode": "retrain",
  "config": "configs/config.yaml",
  "progress": {
    "percent": null,
    "message": "running"
  },
  "error": null,
  "train_run_id": null,
  "registered_model_name": null,
  "registered_model_version": null,
  "model_uri": null,
  "mlflow": null
}
```

**Succeeded example:**

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "succeeded",
  "mode": "retrain",
  "config": "configs/config.yaml",
  "progress": { "percent": 100.0, "message": "completed" },
  "error": null,
  "train_run_id": "abc123def456",
  "registered_model_name": "cafa-go-model",
  "registered_model_version": "3",
  "model_uri": "runs:/abc123def456/model",
  "mlflow": {
    "tracking_uri": "http://mlflow:5000",
    "train_run_id": "abc123def456",
    "experiment_id": "1",
    "run_ui_url": "http://127.0.0.1/mlflow/#/experiments/1/runs/abc123def456",
    "registered_model_name": "cafa-go-model",
    "registered_model_version": "3",
    "model_registry_ui_url": "http://127.0.0.1/mlflow/#/models/cafa-go-model/versions/3"
  }
}
```

**Failed example:**

```json
{
  "job_id": "...",
  "status": "failed",
  "mode": "train",
  "config": "configs/config.yaml",
  "progress": { "percent": 1.0, "message": "starting training subprocess" },
  "error": {
    "code": "TRAINING_SUBPROCESS_FAILED",
    "message": "...",
    "exit_code": 1,
    "stderr_len": 4096,
    "stdout_len": 128
  },
  "train_run_id": null,
  "registered_model_name": null,
  "registered_model_version": null,
  "model_uri": null,
  "mlflow": null
}
```

Common error codes: `TRAINING_SUBPROCESS_FAILED`, `TRAIN_SUMMARY_MISSING`, `TRAIN_SUMMARY_INVALID`, `TRAINING_RUNTIME_FAILURE`.

## Examples

Set these once for the examples below (gateway deployment):

```bash
GATEWAY="http://localhost"
AUTH="admin:YOUR_PASSWORD"
```

For local Uvicorn without nginx, use `GATEWAY="http://127.0.0.1:8002"` and omit `-u "$AUTH"`.

### Example 1: Train only (`mode: "train"`)

Submit a job that runs `scripts/train.py` — training and MLflow registration, no holdout eval or promotion.

```bash
curl -sk -u "$AUTH" -X POST "${GATEWAY}/api/train/train" \
  -H "Content-Type: application/json" \
  -d '{"config": "configs/config.yaml", "mode": "train"}'
```

Save the `job_id` from the response:

```bash
JOB_ID="<paste-job-id-here>"
```

Poll until the job finishes:

```bash
while true; do
  RESP=$(curl -sk -u "$AUTH" "${GATEWAY}/api/train/jobs/${JOB_ID}")
  echo "$RESP" | python3 -m json.tool
  STATUS=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  if [ "$STATUS" = "succeeded" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  sleep 10
done
```

On success, inspect `train_run_id` and `model_uri` in the final JSON. Open MLflow via the gateway:

```text
http://localhost/mlflow/
```

### Example 2: Full retraining pipeline (`mode: "retrain"`)

Submit a job that trains, evaluates on holdout, and promotes to `champion` if F1 ≥ `PROMOTION_THRESHOLD`.

```bash
RESP=$(curl -sk -u "$AUTH" -X POST "${GATEWAY}/api/train/train" \
  -H "Content-Type: application/json" \
  -d '{"config": "configs/config.yaml", "mode": "retrain"}')

echo "$RESP" | python3 -m json.tool
JOB_ID=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
echo "JOB_ID=$JOB_ID"
```

Poll (same loop as above), then read MLflow links from the succeeded response:

```bash
curl -sk -u "$AUTH" "${GATEWAY}/api/train/jobs/${JOB_ID}" | python3 -m json.tool
```

After a successful retrain with promotion:

- MLflow run UI: `mlflow.run_ui_url` in the response.
- Model registry: `mlflow.model_registry_ui_url`.
- `go-prediction-api` serves `models:/cafa-go-model@champion` — restart or wait for the next model load if needed.

### Example 3: One-liner submit + poll (retrain)

```bash
GATEWAY="http://localhost"
AUTH="admin:YOUR_PASSWORD"

JOB_ID=$(curl -sk -u "$AUTH" -X POST "${GATEWAY}/api/train/train" \
  -H "Content-Type: application/json" \
  -d '{"mode": "retrain"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")

echo "Submitted job: $JOB_ID"

until curl -sk -u "$AUTH" "${GATEWAY}/api/train/jobs/${JOB_ID}" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['status']; print(s); exit(0 if s in ('succeeded','failed') else 1)"; do
  sleep 15
done

curl -sk -u "$AUTH" "${GATEWAY}/api/train/jobs/${JOB_ID}" | python3 -m json.tool
```

### Example 4: Python client

```python
import time

import requests
from requests.auth import HTTPBasicAuth

GATEWAY = "http://localhost"
AUTH = HTTPBasicAuth("admin", "YOUR_PASSWORD")

# Submit retraining job
resp = requests.post(
    f"{GATEWAY}/api/train/train",
    json={"config": "configs/config.yaml", "mode": "retrain"},
    auth=AUTH,
    timeout=30,
)
resp.raise_for_status()
job_id = resp.json()["job_id"]
print(f"job_id={job_id}")

# Poll until terminal state
while True:
    status_resp = requests.get(
        f"{GATEWAY}/api/train/jobs/{job_id}",
        auth=AUTH,
        timeout=30,
    )
    status_resp.raise_for_status()
    body = status_resp.json()
    print(body["status"], body.get("progress", {}))
    if body["status"] in ("succeeded", "failed"):
        break
    time.sleep(10)

if body["status"] == "succeeded":
    print("train_run_id:", body["train_run_id"])
    print("model_uri:", body["model_uri"])
    if body.get("mlflow", {}).get("run_ui_url"):
        print("MLflow run:", body["mlflow"]["run_ui_url"])
else:
    print("error:", body.get("error"))
```

## Environment variables

Set in `docker-compose.yml` for the `trainer-api` service (or export for local runs):

| Variable | Default | Purpose |
|----------|---------|---------|
| `MLFLOW_TRACKING_URI` | `http://mlflow:5000` | MLflow tracking server (Postgres backend + MinIO artifacts) |
| `MLFLOW_S3_ENDPOINT_URL` | `http://minio:9000` | MinIO endpoint for artifact upload/download (trainer + serving containers) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | from `.env` | MinIO credentials for MLflow S3 artifact store |
| `MLFLOW_EXTERNAL_UI_BASE` | `http://127.0.0.1:5000` | Browser-reachable MLflow UI base for links in job JSON (Compose: `http://127.0.0.1/mlflow`) |
| `REGISTERED_MODEL_NAME` | `cafa-go-model` | Model Registry name passed to training scripts |
| `PROMOTION_THRESHOLD` | `0.35` | Holdout F1 threshold for `champion` promotion (retrain mode) |
| `TRAINING_API_ARTIFACT_ROOT` | `outputs/training_api` | Directory for SQLite job DB |
| `CAFA_DEVICE` | `auto` | Device selection (`auto`, `cuda`, `cpu`) |
| `PYTHON_EXECUTABLE` | `python` | Python binary for subprocess invocation |

## MLflow and artifacts

After a successful job, the worker reads `outputs/train_run_summary.json` (written by `scripts/train.py`) and enriches the API response with MLflow UI links.

Key on-disk artifacts (repo `outputs/` volume):

| File | Written by | Contents |
|------|------------|----------|
| `outputs/train_run_summary.json` | `train.py` | `train_run_id`, registry version, `model_uri` |
| `outputs/holdout_eval_summary.json` | `evaluate_holdout.py` | `eval_run_id`, holdout metrics (retrain only) |
| `outputs/checkpoints/best_model.pt` | `train.py` | Best validation checkpoint |
| `outputs/training_api/jobs.db` | Training API | Job queue and status |

## Monitoring

Prometheus metrics (scraped when the `monitoring` profile is active):

- `cafa5_training_jobs_total` — terminal jobs by status and mode
- `cafa5_training_queue_jobs` — jobs per lifecycle state
- `cafa5_training_job_duration_seconds` — job duration histogram
- `cafa5_training_subprocess_failures_total` — failure reasons

HTTP metrics: `cafa5_http_requests_total`, `cafa5_http_request_duration_seconds`.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `401 Authorization Required` | Missing or wrong gateway Basic Auth. Use `-u admin:PASSWORD`. |
| `502 Bad Gateway` on `/api/train/*` | Training profile not started. Run `docker compose --profile training up -d`. |
| `TRAIN_SUMMARY_MISSING` | `train.py` exited 0 but did not write `outputs/train_run_summary.json` (e.g. no checkpoint produced). |
| `TRAINING_SUBPROCESS_FAILED` | Check `error.message` in job JSON for stderr excerpt. |
| `localhost` works but `127.0.0.1` does not | Docker Desktop IPv4/IPv6 loopback quirk on port 80. Use `localhost` or your LAN IP. |
| Retrain succeeds but no promotion | Holdout F1 below `PROMOTION_THRESHOLD`. Check `holdout_eval_summary.json` and MLflow eval run. |

Inspect trainer logs:

```bash
docker compose logs -f trainer-api
```

## Security

The Training API runs long GPU jobs and writes to shared `data/` and `outputs/` volumes. MLflow state is stored in Postgres/MinIO services. In Compose it is reachable only through the NGINX gateway with admin-tier Basic Auth. Do not expose it on a public network without authentication, TLS, and network controls.

## Changing the URL prefix

`API_PREFIX` is defined in `services/training-api/config.py` (default `/api/train`). Update it there and align the matching `location` block in `nginx/nginx.conf` if you change the path.
