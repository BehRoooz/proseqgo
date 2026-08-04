# ProSeqGO

**Protein sequence → Gene Ontology (GO) term prediction** for functional annotation.

Web UI: **[https://proseqgo.com](https://proseqgo.com)**  
Source: [github.com/BehRoooz/proseqgo](https://github.com/BehRoooz/proseqgo)

```text
sequence → protein language model embedding → multi-label GO predictions
```

Paste a protein sequence (or upload FASTA) in the web UI and receive ranked GO terms with scores and metadata. The same capability is available through authenticated HTTP APIs for pipelines and lab tooling.

| Audience | Start here |
|----------|------------|
| Biologists / lab users | [Live demo](https://proseqgo.com) |
| API integrators | [Serving and UI](#serving-and-ui) |
| ML / platform engineers | [Local quickstart](#local-quickstart) · [docs/](docs/) |

## What ProSeqGO does

Protein function annotation is a multi-label problem: one sequence may map to many GO terms across molecular function, biological process, and cellular component.

ProSeqGO provides:

1. Interactive prediction from sequence or FASTA (public UI)
2. Sequence → GO and embedding → GO APIs behind the gateway
3. Reproducible training on a version-pinned CAFA-derived dataset
4. MLflow tracking and a registry-backed serving model (`@champion`)
5. Containerized local/CI stacks plus a production Compose overlay

**Training data:** [cafa-5-6-train-dataset](https://www.kaggle.com/datasets/behrouzmirabdi/cafa-5-6-train-dataset) on Kaggle (needed for training/evaluation only; not required to run inference against a registered champion model).

## Live product

| Item | Detail |
|------|--------|
| UI | [https://proseqgo.com](https://proseqgo.com) (public) |
| Predict APIs | Same host under `/api/...` (Basic Auth required) |
| Typical limits | Up to 20 sequences per request, 1000 aa per sequence, 2 MB FASTA upload |

The UI calls the gateway on your behalf. Direct API access needs credentials issued for your environment—never commit passwords or tokens.

> **Citation:** placeholder until the manuscript is published. Watch this README and the UI citation block for the final reference.

## Key features

- Transformer embeddings (ESM2 by default in the serving path) with mean pooling
- Multi-label GO predictor served from MLflow (`REGISTERED_MODEL_NAME` / `@champion`)
- Streamlit product UI with GO term metadata enrichment
- NGINX gateway: Basic Auth tiers, rate limits, body-size caps
- Async embedding/training jobs (Postgres history + Redis/RQ) for ops workloads
- Compose overlays for local GPU, CI/CPU smoke, and production inference
- CI: lint, unit tests, image builds; GHCR publish on `main`

## Architecture (high level)

```text
User / Client
   |
   v
NGINX gateway (auth + limits + routing)
   |-- /              → Streamlit UI (public)
   |-- /api/v1/*      → Embedding API  → (optional) remote embed provider
   |                 └→ GO Prediction API
   |-- /api/predict/* → GO Prediction API
   |-- /api/train*    → Training API (optional profile; not on public inference)
   |-- /mlflow/*      → MLflow UI / registry (admin)

Data plane (Compose): Postgres, Redis, MinIO
Observability (optional profile): Prometheus, Grafana
```

Serving loads `models:/${REGISTERED_MODEL_NAME}@champion` unless `MODEL_URI` is overridden.

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
│   ├── embedding-api/       # Embeddings + sequence→GO orchestration
│   ├── go-prediction-api/   # Embedding→GO inference
│   ├── streamlit-ui/        # Product UI
│   └── training-api/        # Async train / retrain jobs
├── src/                     # Core modeling / training / inference libraries
├── tests/
│   ├── unit/                # Fast pytest (CI; no Docker/GPU)
│   └── smoke/               # Compose acceptance scripts
├── .github/                 # CI workflows
├── docker-compose.yml       # Portable base stack
├── docker-compose.gpu.yml   # Local GPU overlay
├── docker-compose.ci.yml    # CPU CI / smoke overlay
├── Makefile
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

Local serving (`make up`) does **not** require these files. Training, embedding generation, and holdout evaluation do.

**Details:** [docs/data.md](docs/data.md)

## Local quickstart

For development on your machine (not the public site).

### Prerequisites

- Docker + Docker Compose v2
- `make`, `bash`, `curl`
- NVIDIA Container Toolkit (optional; GPU overlay)
- Kaggle credentials (only if downloading training data)
- Python 3.10+ (for CLI training outside containers)

### 1. Environment and gateway auth

```bash
make ci-env          # .env.example → .env if missing
# Edit .env: set strong, unique secrets for Postgres, MinIO, and GATEWAY_*
make gateway-auth    # writes nginx/.htpasswd-admin and .htpasswd-user
```

Never commit `.env`, htpasswd files, API tokens, or certificate private keys.

### 2. Start the core stack

```bash
make up
```

`make up` uses the base Compose file and adds `docker-compose.gpu.yml` when `nvidia-smi` is available. On CPU-only hosts, inference uses `CAFA_DEVICE=auto` → CPU. Local default embedding provider is `local` (`EMBED_PROVIDER`).

### 3. Optional profiles

```bash
make monitoring-up   # Prometheus + Grafana
make training-up     # Training API + worker
make all-up          # default + monitoring + training
```

### 4. Local access points

| Endpoint | URL |
|----------|-----|
| UI | `http://localhost/` (also redirects from `/ui/`) |
| MLflow | `http://localhost/mlflow/` |
| Prometheus | `http://localhost:9090` (monitoring profile) |
| Grafana | `http://localhost:3000` (monitoring profile) |

```bash
curl -sk -u user:PASSWORD http://localhost/api/v1/health
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
| Redis / RQ | `REDIS_URL`, job timeout vars |
| MinIO / S3 | `MINIO_*`, `AWS_*`, `MLFLOW_S3_ENDPOINT_URL`, `MLFLOW_ARTIFACT_ROOT` |
| Registry | `REGISTERED_MODEL_NAME`, `PROMOTION_THRESHOLD` |
| Request envelope | `MAX_SEQUENCES_PER_REQUEST`, `MAX_SEQUENCE_LENGTH_AA`, `MAX_FASTA_UPLOAD_MB` |
| Embed provider | `EMBED_PROVIDER` (e.g. `local`, or a remote backend when configured) |

Pipeline hyperparameters live in [`configs/config.yaml`](configs/config.yaml). Keep `embedding.backend` aligned with training embeddings and the GO predictor’s expected dimension.

Remote embedding backends, if used, are configured via environment variables on the host—never commit their credentials.

## Training workflow

```text
preprocess → split → embed → train → evaluate_holdout → promote_model
```

| Mode | When to use |
|------|-------------|
| CLI (`scripts/retrain_pipeline.py`) | Research iteration, full control |
| Training API (`/api/train/train`) | Ops automation (training profile) |
| Hybrid | Embed via API; train/promote via CLI |

Primary promotion metric: `holdout_f1_micro` (threshold from `PROMOTION_THRESHOLD`). Serving consumes the `champion` alias unless `MODEL_URI` is overridden.

**Details:** [docs/training.md](docs/training.md)

## Serving and UI

| Path | Purpose | Access |
|------|---------|--------|
| `/` | Streamlit product UI | Public |
| `/api/predict/*` | Embedding → GO | Authenticated (user/admin) |
| `/api/v1/predict-go-from-sequences` | Sequence → GO (JSON) | Authenticated (user/admin) |
| `/api/v1/predict-go-from-fasta` | Sequence → GO (FASTA) | Authenticated (user/admin) |
| `/api/v1/jobs*` | Async embedding jobs | Admin |
| `/api/train*` | Async train / retrain | Admin (training profile) |
| `/mlflow/*` | Tracking + registry UI | Admin |

Example against a **local** stack (replace `PASSWORD` with your local gateway user password):

```bash
curl -sk -u user:PASSWORD -X POST \
  http://localhost/api/v1/predict-go-from-sequences \
  -H "Content-Type: application/json" \
  -d '{
    "backend": "esm2",
    "pooling": "mean",
    "batch_size": 2,
    "max_length": 1000,
    "top_k": 10,
    "sequences": [
      {"id": "P1", "sequence": "MKTAYIAKQRQISFVKSHFSRQ"}
    ]
  }'
```

Service notes:

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

GitHub Actions (PR / `main`): lint → unit tests → parallel image builds. Merges to `main` publish to GHCR. See [`.github/CI.md`](.github/CI.md).

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

```bash
docker compose ps
docker compose logs --tail=100 <service>
curl -sk -u user:PASS http://localhost/api/v1/health
```

Common issues: missing `.env` / htpasswd, `proseqgo_jobs` DB, gateway 401/502, GPU not visible, MLflow / MinIO artifact errors, embedding–model dimension mismatch, queue backlog.

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
