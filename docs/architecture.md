# Architecture

This document describes how ProSeqGO is structured: services, data flows, security boundaries, and key design decisions. For operational runbooks, see [monitoring.md](monitoring.md) and [troubleshooting.md](troubleshooting.md).

## System context

ProSeqGO is an end-to-end MLOps platform for **multi-label Gene Ontology (GO) prediction** from protein sequences. It targets:

| Audience | Primary entry point |
|----------|---------------------|
| Product / lab users | Streamlit UI (`/ui/`) or GO prediction API (`/api/predict/`) |
| ML engineers | CLI scripts (`scripts/`), Training API (`/api/train/`) |
| Platform / ops | Docker Compose, NGINX gateway, Prometheus/Grafana |
| Auditors | MLflow tracking UI (`/mlflow/`) |

**In scope:** preprocessing, embedding generation, model training, registry-based serving, secured gateway routing, async job queues, observability.

**External dependencies:**

- [Kaggle CAFA 5/6 training dataset](https://www.kaggle.com/datasets/behrouzmirabdi/cafa-5-6-train-dataset)
- Hugging Face protein language models (ESM2, ProtBERT, ProtT5)
- Local or containerized infrastructure (Postgres, Redis, MinIO)

## Logical architecture

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

Prometheus <---------------- /metrics from embedding/go/training workers
Grafana <------------------- Prometheus datasource
```

### Service inventory

| Service | Role | Profile |
|---------|------|---------|
| `nginx` | Single public ingress (ports 80/443) | default |
| `embedding-api` | FastAPI: async embedding jobs, sequence→GO orchestration | default |
| `embedding-worker` | RQ worker: runs embedding jobs, exposes worker metrics | default |
| `go-prediction-api` | FastAPI: embedding→GO inference from registry `@champion` | default |
| `streamlit-ui` | Interactive UI over gateway | default |
| `mlflow` | Experiment tracking and model registry | default |
| `postgres` | MLflow backend store + `proseqgo_jobs` job history DB | default |
| `redis` | RQ job dispatch (embedding + training) | default |
| `minio` | S3-compatible artifact store for MLflow | default |
| `trainer-api` / `trainer-worker` | Async retrain jobs | `training` |
| `prometheus` / `grafana` / `redis-exporter` | Observability | `monitoring` |
| `postgres-backup` / `backup-offload` | Daily Postgres dumps, MinIO offload | default (skipped in CI overlay) |

## Request flows

### 1. Embedding → GO (direct inference)

```text
Client → NGINX (/api/predict/predict) → go-prediction-api
                                              ↓
                                    MLflow registry (@champion)
                                              ↓
                                    GO term predictions (top_k)
```

The GO prediction API loads `models:/<REGISTERED_MODEL_NAME>@champion` (default: `cafa-go-model@champion`) and validates embedding dimension before inference.

### 2. Sequence → GO (orchestrated)

```text
Client → NGINX (/api/v1/predict-go-from-sequences|fasta)
              → embedding-api
                    → create embedding job (Postgres + Redis/RQ)
                    → embedding-worker processes job
                    → load test_embeddings.npy
                    → for each sequence: go-prediction-api /predict
              → aggregated PredictGoResponse
```

Sync wrappers poll the embedding job with configurable `timeout_seconds` (default 1800 s) and `poll_interval_seconds`.

### 3. Async embedding job

```text
Client → embedding-api POST /api/v1/jobs
              → Postgres (proseqgo_jobs.embedding_jobs, status=queued)
              → Redis/RQ enqueue → embedding-worker
              → artifacts under outputs/service_artifacts/{job_id}/
Client polls GET /api/v1/jobs/{job_id}
Client downloads GET /api/v1/jobs/{job_id}/artifacts/{name}
```

Postgres is the source of truth for job status; Redis holds transient dispatch state.

### 4. Training / retraining (optional profile)

```text
Client → NGINX (/api/train/train) → trainer-api
              → Postgres + Redis/RQ → trainer-worker
              → scripts/retrain_pipeline.py (train → eval → promote)
              → MLflow runs, model registration, optional champion promotion
```

## Data and artifact flow

```text
Kaggle dataset (FASTA + terms)
        ↓
scripts/preprocess.py → label matrix (outputs/)
scripts/split_train_holdout.py → deterministic splits (outputs/splits/)
scripts/embed_sequences.py → embeddings (data/embeddings/)
        ↓
scripts/train.py → checkpoints + MLflow run + model version
scripts/evaluate_holdout.py → holdout metrics
scripts/promote_model.py → champion alias (if metric ≥ threshold)
        ↓
go-prediction-api serves @champion
```

See [data.md](data.md) and [training.md](training.md) for detail.

## Deployment topology

Compose uses a **portable base** plus optional overlays:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | All services; CPU-safe base (no `gpus:`) |
| `docker-compose.gpu.yml` | Adds `gpus: all` for inference/training workers |
| `docker-compose.ci.yml` | CPU smoke: `CAFA_DEVICE=cpu`, skips backup sidecars |

`make up` auto-adds the GPU overlay when `nvidia-smi` is available.

**Networks:** all services share the `proseqgo` bridge network. Only NGINX (80/443), Prometheus (9090), Grafana (3000), and MinIO console (9000/9001) bind to the host; APIs are internal and reached via NGINX.

**Volumes:**

| Volume / mount | Contents |
|----------------|----------|
| `postgres_data` | MLflow backend + job DB |
| `minio_data` | MLflow artifacts, DB backups |
| `redis_data` | RQ persistence (AOF) |
| `./data` | CAFA raw data, embeddings, HF cache |
| `./outputs` | Splits, checkpoints, service artifacts |
| `./backups/postgres` | Local Postgres dumps |

## Security architecture

NGINX is the single public ingress. See [nginx/README.md](../nginx/README.md) for gateway specifics.

| Control | Implementation |
|---------|----------------|
| TLS | HTTP→HTTPS redirect on port 80 |
| Authentication | Basic auth: admin tier (`.htpasswd-admin`) vs user tier (`.htpasswd-user`) |
| Rate limiting | Admin zone 15 r/s; predict zone 30 r/s |
| Body size limits | Per-route caps (512 MB embedding jobs, 5 MB FASTA, 8 MB predict, etc.) |
| Timeouts | 600 s read/send for long jobs |
| Trace headers | `X-Trace-Id`, auth tier/user forwarded upstream |

**Secrets:** copy [`.env.example`](../.env.example) to `.env`; generate htpasswd with `make gateway-auth`. Never commit real credentials.

**Internal metrics:** `/metrics` endpoints are scraped on the Docker network and are not exposed through NGINX.

## Integration points

| System | Internal URI | Notes |
|--------|--------------|-------|
| MLflow tracking | `http://mlflow:5000` | Gateway: `https://localhost/mlflow/` |
| MLflow artifacts | `s3://mlflow-artifacts/` via MinIO | Requires S3 env vars in clients |
| Job history DB | `postgresql://…/proseqgo_jobs` | Init: `docker/postgres/init-proseqgo-jobs.sh` |
| Redis / RQ | `redis://redis:6379/0` | Queues: `embedding-jobs`, training queue |
| GO prediction (internal) | `http://go-prediction-api:8000` | Used by embedding-api orchestration |

## Design decisions

### Async jobs for embedding and training

Long-running GPU work is offloaded to RQ workers. The API returns immediately with a job ID; Postgres records durable state for polling and audit.

### Champion alias for serving

Production inference always resolves `models:/<name>@champion`. Promotion is gated on holdout `holdout_f1_micro ≥ PROMOTION_THRESHOLD` (default 0.35). This decouples experiment versions from the live model.

### Gateway in front of all user-facing services

Centralizes TLS, auth tiers, rate limits, and payload caps. Internal services are not directly exposed on the host.

### Separate job database on Postgres

`proseqgo_jobs` coexists with the MLflow backend DB on the same Postgres instance but isolates embedding/training job history from MLflow schema.

### CPU-portable base compose

The default stack runs on CPU-only hosts (`CAFA_DEVICE=auto` → CPU). GPU is an opt-in overlay for local development.

## Non-functional requirements

| Concern | Approach |
|---------|----------|
| Latency | Sync sequence→GO default timeout 1800 s; embedding jobs async |
| Concurrency | RQ workers; GO inference sequential per orchestration call |
| Reproducibility | Fixed seeds, deterministic splits, config-driven pipelines |
| Observability | Prometheus metrics (`cafa5_*`), Grafana dashboards, alert rules |
| Failure recovery | Embedding worker requeues orphaned RQ jobs on startup |

## Related documentation

- [data.md](data.md) — dataset layout and versioning
- [training.md](training.md) — ML lifecycle and promotion
- [deployment.md](deployment.md) — environment setup and compose profiles
- [monitoring.md](monitoring.md) — metrics, dashboards, alerts
- Service READMEs: `services/embedding-api/`, `services/training-api/`, `services/streamlit-ui/`
