# CAFA-5 MLOps Solution

End-to-end MLOps platform for CAFA-5 protein function prediction (sequence -> embedding -> GO terms), with model lifecycle management, secured gateway routing, and production-oriented monitoring.

## Problem This Project Solves

Protein function annotation is a high-throughput, multi-label prediction problem where operational risks are as important as model quality: inconsistent embedding backends, untracked model promotions, and weak runtime observability can silently degrade prediction quality.

This project provides:

- A reproducible training and retraining workflow.
- Online inference APIs for both embedding-level and sequence-level use cases.
- MLflow-backed experiment tracking and registry-based model serving.
- A secured NGINX gateway for TLS, auth, and rate/body constraints.
- Monitoring with Prometheus, Grafana dashboards, and actionable alert rules.

## High-Level Workflow

1. Preprocess CAFA labels and generate deterministic train/holdout splits.
2. Generate embeddings from protein sequences (ESM2/ProtBERT/T5).
3. Train and evaluate multi-label GO predictor.
4. Log runs/artifacts to MLflow and register model versions.
5. Promote model alias (`champion`) after metric threshold checks.
6. Serve predictions:
   - `embedding -> GO` via GO prediction API.
   - `sequence -> GO` in one call via embedding API orchestration.
7. Observe service health and model-serving behavior with Prometheus/Grafana.

## Architecture Overview

```text
User / Client
   |
   v
NGINX (TLS + Basic Auth + Rate Limit + Routing)
   |-----------------------> /ui/ -----------------------> Streamlit UI
   |-----------------------> /api/v1/* ------------------> Embedding API
   |                                                     |-> Go Prediction API (/predict)
   |-----------------------> /api/predict/* -------------> Go Prediction API
   |-----------------------> /api/train* ----------------> Training API (profile: training)
   |-----------------------> /mlflow/* ------------------> MLflow UI / Registry

Prometheus <---------------- /metrics from embedding/go/training/prometheus
Grafana <------------------- Prometheus datasource
```

## Repository Structure

```text
CAFA-5-MLOps-solution/
├── configs/                      # Global YAML config for data/model/train/inference
├── data/                         # CAFA data, embeddings, HF cache
├── docker/                       # Service-specific Dockerfiles
├── docs/                         # Additional project docs
├── examples/                     # Example sequences/inputs
├── mlruns/                       # MLflow file backend store
├── monitoring/                   # Prometheus, Grafana provisioning, alert rules
├── nginx/                        # Gateway config, TLS certs, htpasswd files
├── outputs/                      # Splits, labels, checkpoints, artifacts, submissions
├── scripts/                      # CLI pipeline entrypoints (preprocess/train/evaluate/predict)
├── services/
│   ├── embedding-api/            # Async embedding jobs + sequence->GO orchestration endpoint
│   ├── go-prediction-api/        # Embedding->GO inference API
│   ├── streamlit-ui/             # Interactive UI over gateway endpoint
│   └── training-api/             # Async train/retrain job API
├── src/                          # Core modeling/training/inference modules
├── docker-compose.yml            # Full integrated deployment
├── Makefile                      # Convenience targets for compose profiles
└── README.md
```

## Quick Start

### 1) Bring core stack up

```bash
docker compose up --build
```

or:

```bash
make up
```

Core services started by default: `nginx`, `embedding-api`, `go-prediction-api`, `streamlit-ui`, `mlflow`.

### 2) Bring monitoring up

```bash
docker compose --profile monitoring up -d
```

or:

```bash
make monitoring-up
```

### 3) Optional: bring training API profile up

```bash
docker compose --profile training up -d --build
```

or:

```bash
make training-up
```

## Access Points

- Gateway root: `https://localhost`
- Streamlit UI: `https://localhost/ui/`
- MLflow via gateway: `https://localhost/mlflow/`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`

## NGINX Architecture

`nginx` is the single public ingress on ports `80/443` and enforces operational policy:

- HTTP->HTTPS redirect.
- Basic auth tiers:
  - Admin routes: `/api/v1/*`, `/api/train*`, `/mlflow/`.
  - User routes: `/api/predict/*`, `/api/v1/predict-go-from-sequences`, `/api/v1/predict-go-from-fasta`.
- Per-route body size controls:
  - `/api/v1/`: 512 MB
  - `/api/v1/predict-go-from-fasta`: 5 MB
  - `/api/train`: 64 MB
  - `/api/predict/`: 8 MB
  - `/mlflow/`: 32 MB
- Request rate limits:
  - admin zone `15 r/s` (burst 40)
  - predict zone `30 r/s` (burst 80)
- Long-job compatible upstream timeouts (`600s` read/send).
- Trace headers forwarded upstream (`X-Trace-Id`, auth tier/user context).

## MLflow Architecture

MLflow runs as an internal service and is exposed through gateway path `/mlflow/`.

- Tracking server command:
  - `mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri file:///mlruns ...`
- Backend/artifacts storage:
  - repository-mounted `./mlruns` volume (`/mlruns` in container).
- Model registry:
  - registered model name defaults to `cafa-go-model`.
  - serving API (`go-prediction-api`) loads `models:/cafa-go-model@champion` by default.
- Training API returns MLflow run/model URLs in job status payload when available.

## Monitoring Architecture

Monitoring is profile-based and isolated from public ingress:

- Prometheus scrapes:
  - `prometheus:9090`
  - `embedding-api:8000/metrics`
  - `go-prediction-api:8000/metrics`
  - `trainer-api:8000/metrics` (when training profile is active)
- Grafana datasource is provisioned from `monitoring/grafana/provisioning/datasources/prometheus.yml`.
- Dashboards are file-provisioned from `monitoring/grafana/dashboards`.
- Metrics family includes:
  - HTTP request/latency/in-flight metrics (`cafa5_http_*`)
  - embedding queue and sequence-length telemetry
  - inference latency, validation failures, top_k distribution

## Alert Rules

Defined in `monitoring/alerts.yml`:

- `Cafa5ServiceMetricsTargetDown`
  - condition: `up == 0` for monitored jobs for >2m
  - severity: `critical`
- `Cafa5HighHttp5xxRatio`
  - condition: service 5xx ratio >5% over 5m and enough traffic, sustained 10m
  - severity: `warning`
- `Cafa5EmbeddingQueueBacklogHigh`
  - condition: queued embedding jobs >20 for 10m
  - severity: `warning`

Verify:

```bash
curl -s http://localhost:9090/-/ready
curl -s http://localhost:9090/api/v1/rules
curl -s http://localhost:9090/api/v1/alerts
```

## APIs and Route Map

All gateway examples below use TLS and basic auth.

- Embedding API (admin): `/api/v1/...`
  - `/api/v1/health`
  - `/api/v1/jobs`
  - `/api/v1/jobs/fasta`
  - `/api/v1/jobs/{job_id}`
  - `/api/v1/jobs/{job_id}/artifacts/{name}`
  - `/api/v1/jobs/{job_id}/predict-go`
  - `/api/v1/predict-go-from-sequences`
  - `/api/v1/predict-go-from-fasta`
- GO prediction API (user/admin): `/api/predict/...`
  - `/api/predict/health`
  - `/api/predict/predict`
- Training API (admin, training profile): `/api/train/...`
  - `/api/train/health`
  - `/api/train/train`
  - `/api/train/jobs/{job_id}`

## Practical Testing Playbook

### 1) Health checks

```bash
curl -sk -u USER:PASS https://localhost/api/v1/health
curl -sk -u USER:PASS https://localhost/api/predict/health
curl -sk -u USER:PASS https://localhost/api/train/health
```

### 2) Async embeddings from sequences

```bash
curl -sk -u ADMIN:ADMIN_PASS -X POST https://localhost/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "stage": "test",
    "backend": "esm2",
    "pooling": "mean",
    "batch_size": 2,
    "max_length": 1280,
    "sequences": [
      {"id": "P1", "sequence": "MKTAYIAKQRQISFVKSHFSRQ"},
      {"id": "P2", "sequence": "GAVLIPFYWSTCMNQDEKRH"}
    ]
  }'
```

Poll job and download artifacts:

```bash
curl -sk -u ADMIN:ADMIN_PASS https://localhost/api/v1/jobs/<JOB_ID>
curl -sk -u ADMIN:ADMIN_PASS -o test_ids.npy \
  https://localhost/api/v1/jobs/<JOB_ID>/artifacts/test_ids.npy
curl -sk -u ADMIN:ADMIN_PASS -o test_embeddings.npy \
  https://localhost/api/v1/jobs/<JOB_ID>/artifacts/test_embeddings.npy
```

### 3) Direct embedding -> GO inference

```bash
python - <<'PY'
import json
import numpy as np
import requests

embedding = np.load("test_embeddings.npy")[0].astype(float).tolist()
r = requests.post(
    "https://localhost/api/predict/predict",
    auth=("USER", "USER_PASS"),
    json={"embedding": embedding, "top_k": 10},
    verify=False,
    timeout=60,
)
print(r.status_code)
print(json.dumps(r.json(), indent=2))
PY
```

### 4) Sequence -> GO in one call (main integration endpoints)

Two sync wrappers share the same response contract (`PredictGoResponse`): embed → wait for job completion → call GO prediction API per sequence.

#### Option A: JSON sequences

Endpoint: `POST /api/v1/predict-go-from-sequences`

Request contract:

- `backend`: `esm2 | protbert | t5`
- `pooling`: `mean | cls`
- `batch_size`: `1..128`
- `max_length`: `8..8192`
- `top_k`: `1..500`
- `sequences`: non-empty list of `{id, sequence}`
- optional:
  - `indices`: subset prediction indices
  - `fail_fast`: `true|false`
  - `timeout_seconds`: `5..7200` (default `1800`)
  - `poll_interval_seconds`: `0.1..5.0` (default `1.0`)

Example:

```bash
curl -sk -u USER:USER_PASS -X POST \
  https://localhost/api/v1/predict-go-from-sequences \
  -H "Content-Type: application/json" \
  -d '{
    "backend": "esm2",
    "pooling": "mean",
    "batch_size": 2,
    "max_length": 1280,
    "top_k": 10,
    "fail_fast": true,
    "sequences": [
      {"id": "P1", "sequence": "MKTAYIAKQRQISFVKSHFSRQ"},
      {"id": "P2", "sequence": "GAVLIPFYWSTCMNQDEKRH"}
    ]
  }'
```

#### Option B: FASTA upload

Endpoint: `POST /api/v1/predict-go-from-fasta` (`multipart/form-data`)

Form fields mirror the JSON endpoint (`backend`, `pooling`, `batch_size`, `max_length`, `top_k`, `fail_fast`, `timeout_seconds`, `poll_interval_seconds`) plus required `fasta_file`. There is no `indices` form field — all parsed records are predicted. `sequence_id` in results is the first token after `>` in each FASTA header.

Example:

```bash
curl -sk -u USER:USER_PASS -X POST \
  https://localhost/api/v1/predict-go-from-fasta \
  -F "fasta_file=@examples/small_sequences.fasta" \
  -F "backend=esm2" \
  -F "pooling=mean" \
  -F "batch_size=2" \
  -F "max_length=1280" \
  -F "top_k=10" \
  -F "fail_fast=true"
```

**Residue normalization (both endpoints):** Sequences are normalized at embedding time: uppercase, whitespace stripped, canonical amino acids kept, and `X`/`U`/`O`/`B`/`Z`/`J` plus any other unknown symbols mapped to `X` (see `normalize_sequence` in `scripts/embed_sequences.py`).

**Operational limits:**

- FASTA upload cap: **5 MB** (API + NGINX on `/api/v1/predict-go-from-fasta`). Larger uploads return `413` (`FASTA_FILE_TOO_LARGE`).
- No sequence-count cap on the FASTA endpoint — only file size. Dense FASTA files may still time out because GO inference is sequential.
- Default sync timeout: **1800 s** (embedding wait + per-sequence GO calls).
- Large or proteome-scale jobs: `POST /api/v1/jobs/fasta` → poll → `POST /api/v1/jobs/{job_id}/predict-go`.

What happens internally:

1. `embedding-api` creates and runs an embedding job (from JSON sequences or parsed FASTA).
2. It waits for completion with polling (`timeout_seconds`, `poll_interval_seconds`).
3. It loads generated `test_embeddings.npy`.
4. For each selected item, it calls `go-prediction-api /predict`.
5. It returns aggregated predictions and per-item failures.

Response shape (simplified):

```json
{
  "job_id": "uuid",
  "status": "succeeded",
  "model_version": "12",
  "top_k": 10,
  "results": [
    {
      "index": 0,
      "sequence_id": "P1",
      "predictions": [{"go_term": "GO:0000000", "score": 0.82}]
    }
  ],
  "failures": []
}
```

### 5) Trigger retraining via API

```bash
curl -sk -u ADMIN:ADMIN_PASS -X POST https://localhost/api/train/train \
  -H "Content-Type: application/json" \
  -d '{"config":"configs/config.yaml","mode":"retrain"}'
```

Poll:

```bash
curl -sk -u ADMIN:ADMIN_PASS https://localhost/api/train/jobs/<JOB_ID>
```

## Retraining Workflow Options

### Option A: Pure CLI retraining (recommended for research iteration)

```bash
python scripts/retrain_pipeline.py --config configs/config.yaml \
  --promotion-threshold 0.35 \
  --model-name cafa-go-model
```

### Option B: API-driven retraining (recommended for ops automation)

- Start `training` profile.
- Submit `/api/train/train` with mode `retrain`.
- Poll until completion.
- Use MLflow links in final job payload for audit and model version traceability.

### Option C: Hybrid flow

- Generate embeddings in service mode (`/api/v1/jobs`) for online or ad-hoc data.
- Train/evaluate in CLI for maximum flexibility.
- Promote registry alias after metric gates.

## Different Ways to Implement/Deploy This Project

### 1) Monolith-like local stack (current default)

- One compose file, one host, local volumes.
- Best for development and reproducible demos.

### 2) API-first deployment

- Keep `nginx + embedding-api + go-prediction-api + mlflow`.
- Add `training-api` only in restricted environments.
- Good for production inference where retraining is decoupled.

### 3) Training separated from serving

- Run training pipeline on dedicated compute (GPU/HPC/batch scheduler).
- Push selected model versions to shared MLflow registry.
- Serving stack only consumes `@champion` alias.

### 4) Monitoring-hardened setup

- Enable monitoring profile by default.
- Add Alertmanager and external notification integrations.
- Formalize SLOs around 5xx ratio, p95 latency, and queue backlog.

## Reproducibility and QC Recommendations

- Keep `embedding.backend` and trained model embedding dimension aligned to avoid invalid inference inputs.
- Use deterministic seeds and fixed splits for comparable retrains.
- Monitor class imbalance and threshold sensitivity (multi-label GO bias risk).
- Track data snapshot/version metadata in MLflow to prevent annotation drift confusion.
- Watch inference validation failure reasons for upstream schema drift.

## Useful Make Targets

```bash
make up
make down
make training-up
make training-down
make monitoring-up
make monitoring-down
```

## Service-Specific Documentation

- `services/embedding-api/README.md`
- `services/training-api/README.md`
- `services/streamlit-ui/README.md`
- `monitoring/README.md`

## License

MIT
