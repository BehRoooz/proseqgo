# ProSeqGO

Production-oriented MLOps platform for **multi-label Gene Ontology (GO) prediction** from protein sequences: reproducible training, registry-based serving, secured gateway routing, async job queues, and observability.

```text
sequence → embedding (ESM2 / ProtBERT / T5) → GO term predictions
```

| Audience | Entry point |
|----------|-------------|
| Product / lab users | Streamlit UI (`/ui/`) or predict APIs |
| ML engineers | CLI (`scripts/`) and Training API |
| Platform / ops | Docker Compose, NGINX, Prometheus / Grafana |
| Auditors | MLflow tracking and model registry (`/mlflow/`) |

## Overview

Protein function annotation is a high-throughput, multi-label problem where operational risk matters as much as model quality: untracked promotions, embedding/model dimension drift, and weak runtime signals can silently degrade predictions.

ProSeqGO provides:

1. Preprocess CAFA labels and deterministic train/holdout splits
2. Generate embeddings from protein sequences
3. Train and evaluate a multi-label GO predictor
4. Log runs and register models in MLflow
5. Promote a `champion` alias after metric threshold checks
6. Serve predictions (`embedding → GO` and `sequence → GO`)
7. Observe health, latency, errors, and queue backlog

**Data source:** [cafa-5-6-train-dataset](https://www.kaggle.com/datasets/behrouzmirabdi/cafa-5-6-train-dataset) on Kaggle (required for training/evaluation; not required for inference-only serving once a champion model exists).

## Key features

- Reproducible training on a version-pinned Kaggle dataset (checksums documented)
- Config-driven preprocess → embed → train → evaluate → promote pipeline
- MLflow experiment tracking and model registry (`@champion` serving)
- Containerized multi-service stack (Compose base + GPU / CI overlays)
- Async embedding and training jobs (Postgres history + Redis/RQ)
- NGINX gateway with Basic Auth tiers, rate limits, and body-size controls
- Streamlit UI for interactive sequence → GO prediction
- Prometheus / Grafana monitoring with alert rules
- CI: lint, unit tests, image builds; GHCR publish on `main`

## Architecture

```text
User / Client
   |
   v
NGINX (Basic Auth + Rate Limit + Routing)  :80
   |-----------------------> /ui/ -----------------------> Streamlit UI
   |-----------------------> /api/v1/* ------------------> Embedding API
   |                                                     |-> Go Prediction API
   |-----------------------> /api/predict/* -------------> Go Prediction API
   |-----------------------> /api/train* ----------------> Training API (profile: training)
   |-----------------------> /mlflow/* ------------------> MLflow UI / Registry

Prometheus <---------------- /metrics (APIs, workers, redis-exporter)
Grafana <------------------- Prometheus
```

| Layer | Components |
|-------|------------|
| Ingress | `nginx` |
| Inference | `embedding-api`, `embedding-worker`, `go-prediction-api`, `streamlit-ui` |
| ML lifecycle | `mlflow`, `trainer-api` / `trainer-worker` (training profile) |
| Data plane | `postgres` (MLflow + `proseqgo_jobs`), `redis` (RQ), `minio` (artifacts) |
| Observability | `prometheus`, `grafana`, `redis-exporter` (monitoring profile) |

Serving loads `models:/cafa-go-model@champion` by default (`REGISTERED_MODEL_NAME` / `MODEL_URI`).

**Details:** [docs/architecture.md](docs/architecture.md)

## Repository structure

```text
proseqgo/
├── configs/                 # Global YAML (data / model / train / inference)
├── data/                    # Kaggle raw data, embeddings, HF cache (gitignored payloads)
├── docker/                  # Service Dockerfiles and Postgres init
├── docs/                    # Architecture, data, training, deploy, ops
├── examples/                # Sample FASTA / inputs
├── monitoring/              # Prometheus, Grafana, alert rules
├── nginx/                   # Gateway config and htpasswd (generated)
├── outputs/                 # Splits, labels, checkpoints, service artifacts
├── scripts/                 # CLI: preprocess, embed, train, evaluate, promote
├── services/
│   ├── embedding-api/       # Async embeddings + sequence→GO orchestration
│   ├── go-prediction-api/   # Embedding→GO inference
│   ├── streamlit-ui/        # Product UI
│   └── training-api/        # Async train / retrain jobs
├── src/                     # Core modeling / training / inference libraries
├── tests/
│   ├── unit/                # Fast pytest (CI; no Docker/GPU)
│   └── smoke/               # Compose acceptance scripts
├── .github/                 # CI workflows and CI notes
├── docker-compose.yml       # Portable base stack
├── docker-compose.gpu.yml   # GPU overlay
├── docker-compose.ci.yml    # CPU CI / smoke overlay
├── Makefile                 # up / train / monitor / lint / test / smoke
└── README.md
```

## Data source and versioning

| Field | Value |
|-------|-------|
| Dataset | [cafa-5-6-train-dataset](https://www.kaggle.com/datasets/behrouzmirabdi/cafa-5-6-train-dataset) |
| Expected layout | `data/cafa-5-cafa-6-protein-function-prediction/Train/{train_sequences.fasta,train_terms.tsv}` |
| Access | Kaggle API (`~/.kaggle/kaggle.json`) or browser download |
| Integrity | SHA-256 checksums in [docs/data.md](docs/data.md) |

```bash
mkdir -p data/cafa-5-cafa-6-protein-function-prediction/Train
kaggle datasets download -d behrouzmirabdi/cafa-5-6-train-dataset \
  -p data/cafa-5-cafa-6-protein-function-prediction/Train --unzip
```

Serving (`make up`) does **not** require these files. Training, embedding generation, and holdout evaluation do.

Raw vs processed vs feature data are separated under `data/` and `outputs/`. Do not hardcode machine-specific paths; use `configs/config.yaml` and environment variables.

**Details:** [docs/data.md](docs/data.md)

## Quickstart

### Prerequisites

- Docker + Docker Compose v2
- `make`, `bash`, `curl`
- NVIDIA Container Toolkit (optional; GPU overlay)
- Kaggle credentials (only if downloading training data)
- Python 3.10+ (for CLI training outside containers)

### 1. Environment and gateway auth

```bash
make ci-env          # .env.example → .env if missing
# Edit .env: Postgres, MinIO, GATEWAY_* passwords (admin ≠ user)
make gateway-auth    # writes nginx/.htpasswd-admin and .htpasswd-user
```

Never commit `.env` or real htpasswd files.

### 2. Start the core stack

```bash
make up
```

`make up` uses the portable base compose file and **adds** `docker-compose.gpu.yml` when `nvidia-smi` is available. On CPU-only hosts, inference uses `CAFA_DEVICE=auto` → CPU.

Default services: `nginx`, `embedding-api`, `embedding-worker`, `go-prediction-api`, `streamlit-ui`, `mlflow`, `postgres`, `redis`, `minio` (plus backup sidecars outside CI).

### 3. Optional profiles

```bash
make monitoring-up   # Prometheus + Grafana
make training-up     # Training API + worker
make all-up          # default + monitoring + training
```

### 4. Access points

| Endpoint | URL |
|----------|-----|
| Gateway | `http://localhost` |
| Streamlit UI | `http://localhost/ui/` |
| MLflow | `http://localhost/mlflow/` |
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3000` |

```bash
curl -sk -u admin:PASSWORD http://localhost/api/v1/health
curl -sk -u user:PASSWORD http://localhost/api/predict/health
```

### 5. CI / CPU smoke

```bash
make ci-up
make smoke
make ci-down
```

**Full deploy guide:** [docs/deployment.md](docs/deployment.md)

## Configuration

| Group | Variables (see [`.env.example`](.env.example)) |
|-------|--------------------------------------------------|
| Gateway auth | `GATEWAY_ADMIN_*`, `GATEWAY_USER_*` |
| Postgres | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| Jobs DB | `JOBS_DATABASE_URL` |
| Redis / RQ | `REDIS_URL`, `EMBEDDING_JOB_TIMEOUT_SEC`, `TRAINING_JOB_TIMEOUT_SEC` |
| MinIO / S3 | `MINIO_*`, `AWS_*`, `MLFLOW_S3_ENDPOINT_URL`, `MLFLOW_ARTIFACT_ROOT` |
| Registry | `REGISTERED_MODEL_NAME`, `PROMOTION_THRESHOLD` |

Pipeline hyperparameters live in [`configs/config.yaml`](configs/config.yaml). Keep `embedding.backend` aligned with training embeddings and the GO predictor’s expected dimension.

## Training workflow

```text
preprocess → split → embed → train → evaluate_holdout → promote_model
```

| Mode | When to use |
|------|-------------|
| CLI (`scripts/retrain_pipeline.py`) | Research iteration, full control |
| Training API (`/api/train/train`) | Ops automation (training profile) |
| Hybrid | Embed via API; train/promote via CLI |

Primary promotion metric: `holdout_f1_micro` (default threshold `0.35`). Serving consumes the `champion` alias unless `MODEL_URI` is overridden.

**Details:** [docs/training.md](docs/training.md)

## Serving and UI

| Path | Purpose | Auth tier |
|------|---------|-----------|
| `/ui/` | Streamlit product UI | gateway |
| `/api/predict/*` | Embedding → GO | user / admin |
| `/api/v1/predict-go-from-sequences` | Sequence → GO (JSON) | user / admin |
| `/api/v1/predict-go-from-fasta` | Sequence → GO (FASTA) | user / admin |
| `/api/v1/jobs*` | Async embedding jobs | admin |
| `/api/train*` | Async train / retrain | admin (training profile) |
| `/mlflow/*` | Tracking + registry UI | admin |

Example (sequence → GO):

```bash
curl -sk -u user:PASSWORD -X POST \
  http://localhost/api/v1/predict-go-from-sequences \
  -H "Content-Type: application/json" \
  -d '{
    "backend": "esm2",
    "pooling": "mean",
    "batch_size": 2,
    "max_length": 1280,
    "top_k": 10,
    "sequences": [
      {"id": "P1", "sequence": "MKTAYIAKQRQISFVKSHFSRQ"}
    ]
  }'
```

Service-specific notes:

- [services/embedding-api/README.md](services/embedding-api/README.md)
- [services/training-api/README.md](services/training-api/README.md)
- [services/streamlit-ui/README.md](services/streamlit-ui/README.md)

## Monitoring and operations

```bash
make monitoring-up
```

| Signal | Why it matters |
|--------|----------------|
| Target `up` | Service scrape health |
| HTTP 5xx ratio / latency | Serving quality |
| Embedding / training queue depth | Backlog and worker health |
| Inference validation failures | Embedding dimension / schema drift |
| `redis_up` | Job dispatch dependency |

Alert rules live in `monitoring/alerts.yml` (service down, high 5xx, queue backlog, worker-down-with-backlog, Redis down).

**Details:** [docs/monitoring.md](docs/monitoring.md) · [monitoring/README.md](monitoring/README.md)

## Testing and CI

| Check | Command | Notes |
|-------|---------|-------|
| Lint | `make lint` | Ruff on `src/`, `services/`, `scripts/` |
| Unit tests | `make test` | `tests/unit` — no Docker/GPU/network |
| Smoke | `make smoke` | Compose stack must already be up |
| Images | `make build-images` / `make pull-images` | Local build or GHCR pull |

GitHub Actions (PR / `main`): lint → unit tests → parallel image builds. Merges to `main` publish to GHCR (`sha-<fullsha>` + `main`). See [`.github/CI.md`](.github/CI.md).

CI does **not** run GPU training or full retrain jobs.

## Deployment

| Environment | Compose | Typical use |
|-------------|---------|-------------|
| Local CPU | `docker-compose.yml` | Dev without NVIDIA |
| Local GPU | base + `docker-compose.gpu.yml` | `make up` auto-detects |
| CI / smoke | base + `docker-compose.ci.yml` | `make ci-up` |
| Full stack | base (+ GPU) + `training` + `monitoring` | Integration / demos |

Suggested production posture: keep NGINX + inference + MLflow always on; run training on dedicated compute; promote only via metric gates; enable monitoring by default.

**Details:** [docs/deployment.md](docs/deployment.md)

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/architecture.md](docs/architecture.md) | Services, flows, security boundaries |
| [docs/data.md](docs/data.md) | Kaggle source, layout, checksums, versioning |
| [docs/training.md](docs/training.md) | Pipeline, metrics, promotion, MLflow |
| [docs/deployment.md](docs/deployment.md) | Environments, secrets, Compose, GHCR |
| [docs/monitoring.md](docs/monitoring.md) | Metrics, alerts, operational response |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Symptom → cause → fix |
| [docs/contributing.md](docs/contributing.md) | Dev setup, PR workflow, standards |

## Contributing

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make lint && make test
```

Prefer small, reviewable PRs. Discuss design first for public API contract changes, registry/alias strategy, new heavy dependencies, jobs DB schema, or security model changes.

**Details:** [docs/contributing.md](docs/contributing.md)

## Troubleshooting

Start here when something fails:

```bash
docker compose ps
docker compose logs --tail=100 <service>
curl -s http://localhost:9090/-/ready
curl -sk -u admin:PASS http://localhost/api/v1/health
```

Common issues covered in the runbook: missing `.env` / htpasswd, `proseqgo_jobs` DB, gateway 401/502, GPU not visible, MLflow / MinIO artifact errors, embedding–model dimension mismatch, queue backlog.

**Details:** [docs/troubleshooting.md](docs/troubleshooting.md)

## Useful Make targets

```bash
make up / make down
make ci-up / make ci-down
make training-up / make training-down
make monitoring-up / make monitoring-down
make all-up / make all-down
make lint / make test / make smoke
make build-images / make pull-images
make ci-env / make gateway-auth
```

## License

MIT
