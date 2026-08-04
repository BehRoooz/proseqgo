# Deployment

This document explains how to run and ship ProSeqGO across local development, CI, and production-like environments.

## Supported environments

| Environment | Compose files | Typical use |
|-------------|---------------|-------------|
| Local dev (CPU) | `docker-compose.yml` | PCs without NVIDIA GPU |
| Local dev (GPU) | `docker-compose.yml` + `docker-compose.gpu.yml` | `make up` auto-detects NVIDIA |
| CI / CPU smoke | `docker-compose.yml` + `docker-compose.ci.yml` | `make ci-up` |
| Full stack | base + GPU + `training` + `monitoring` profiles | Integration testing, demos |

Differences:

- **CI overlay:** forces `CAFA_DEVICE=cpu`, CPU PyTorch wheels, skips `postgres-backup` / `backup-offload`
- **GPU overlay:** adds `gpus: all` to embedding/training workers
- **Training profile:** starts `trainer-api` and `trainer-worker`
- **Monitoring profile:** starts Prometheus, Grafana, `redis-exporter`

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker + Docker Compose v2 | Required for all deployment modes |
| NVIDIA Container Toolkit | Optional; for GPU overlay |
| `make`, `bash`, `curl` | Convenience targets and smoke tests |
| Kaggle credentials | Only for downloading training data (not for serving) |
| Python 3.10+ | For CLI training outside containers |

## First-time setup

### 1. Environment and secrets

```bash
make ci-env          # copies .env.example → .env if missing
# Edit .env: Postgres, MinIO, GATEWAY_* passwords
make gateway-auth    # writes nginx/.htpasswd-admin and .htpasswd-user
```

Never commit `.env` or htpasswd files with real credentials.

### 2. Start core stack

```bash
make up
```

Starts: `nginx`, `embedding-api`, `embedding-worker`, `go-prediction-api`, `streamlit-ui`, `mlflow`, `postgres`, `redis`, `minio`, backup sidecars (non-CI).

Equivalent manual command:

```bash
docker compose up -d --build
# GPU host:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

### 3. Optional profiles

```bash
make monitoring-up    # Prometheus + Grafana
make training-up      # Training API + worker
make all-up           # default + monitoring + training
```

### 4. Verify access

| Endpoint | URL |
|----------|-----|
| Gateway / UI | `http://localhost/` (public Streamlit; `/ui/` redirects here) |
| MLflow | `http://localhost/mlflow/` |
| Prometheus | `http://localhost:9090` (monitoring profile) |
| Grafana | `http://localhost:3000` (monitoring profile) |

Public production UI: [https://proseqgo.com](https://proseqgo.com). Keep TLS, DNS, and firewall details in a private ops runbook—not in this repository.

Health checks (local):

```bash
curl -sk -u user:PASSWORD http://localhost/api/v1/health
curl -sk -u user:PASSWORD http://localhost/api/predict/health
```

## Configuration by environment

### `.env` variables (required)

See [`.env.example`](../.env.example) for the full list. Key groups:

| Group | Variables |
|-------|-----------|
| Gateway auth | `GATEWAY_ADMIN_USER`, `GATEWAY_ADMIN_PASSWORD`, `GATEWAY_USER`, `GATEWAY_USER_PASSWORD` |
| Postgres | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| MinIO / S3 | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| MLflow | `MLFLOW_S3_ENDPOINT_URL`, `MLFLOW_ARTIFACT_ROOT` |
| Model registry | `REGISTERED_MODEL_NAME`, `PROMOTION_THRESHOLD` |
| Job queues | `REDIS_URL`, `JOBS_DATABASE_URL`, `EMBEDDING_JOB_TIMEOUT_SEC`, `TRAINING_JOB_TIMEOUT_SEC` |

### Service-specific overrides

Set in `docker-compose.yml` or overlays; do not duplicate secrets in multiple files.

| Service | Notable env |
|---------|-------------|
| `go-prediction-api` | `MODEL_URI=models:/${REGISTERED_MODEL_NAME}@champion` |
| `embedding-api` | `GO_PREDICTION_API_URL=http://go-prediction-api:8000` |
| `streamlit-ui` | `GATEWAY_BASE_URL=http://nginx` |

## Networking and routing

NGINX is the application ingress inside Compose. Terminate TLS and restrict ports at the host or edge proxy.

| Gateway path | Upstream | Notes |
|--------------|----------|-------|
| `/` | `streamlit-ui` | Public UI |
| `/ui/` | redirect → `/` | Compatibility |
| `/api/v1/*` | `embedding-api:8000` | Auth required (admin for most routes; user for sync predict-go) |
| `/api/predict/*` | `go-prediction-api:8000` | Auth required |
| `/api/train/*` | `trainer-api:8000` | Training profile only |
| `/mlflow/` | `mlflow:5000` | Admin auth |

Auth tiers:

- **Admin:** most `/api/v1/*` job/admin routes, `/api/train*`, `/mlflow/`
- **User:** `/api/predict/*`, `/api/v1/predict-go-from-sequences`, `/api/v1/predict-go-from-fasta`
- **Public:** `/` (Streamlit UI)

See [nginx/README.md](../nginx/README.md) for rate limits and body size caps.

## Deployment patterns

### 1. Monolith-like local stack (default)

Single host, local volumes, all services in Compose. Best for development and reproducible demos.

### 2. API-first production inference

- Strong unique `GATEWAY_*`, Postgres, and MinIO secrets; rotate when staff changes
- Distinct admin vs user gateway accounts
- TLS certificates and DNS managed outside this repo
- Modal (or other) cloud credentials only in host env / secret store
- Health-check UI and predict APIs after each deploy

### 3. Training separated from serving

- Training pipeline on GPU node or batch scheduler
- Push model versions to shared MLflow registry
- Serving stack consumes only `@champion` alias

### 4. Monitoring-hardened

Enable `monitoring` profile by default; add Alertmanager and external notifications for production.

## CI/CD flow

Documented in [`.github/CI.md`](../.github/CI.md).

| Trigger | Actions |
|---------|---------|
| PR | Lint, unit tests, image builds (no push) |
| Push to `main` | Lint, unit tests, build + push to GHCR |

**Registry:** `ghcr.io/behroooz/proseqgo-{embedding-api,go-prediction-api,streamlit-ui,trainer-api,mlflow}`

**Tags:** `main`, `sha-<fullsha>`

```bash
make pull-images                    # pull :main
GHCR_TAG=sha-<commit> make pull-images
```

Local build:

```bash
make build-images                   # CUDA index by default (cu132)
TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu make build-images  # CPU wheels
```

### CI smoke (Compose)

```bash
make ci-up
make smoke
make ci-down
```

Smoke runs `tests/smoke/smoke_embedding_api.sh`. Does not start training profile or GPU jobs.

## Release procedure

1. Merge to `main` → CI publishes images to GHCR
2. Tag release in git if using versioned deploys
3. Pull images on target host: `GHCR_TAG=sha-<commit> make pull-images`
4. Update `.env` if config/secrets changed
5. `docker compose pull && docker compose up -d`
6. Run smoke/health checks
7. Confirm Grafana dashboards and MLflow registry

## Rollback procedure

### Service rollback

```bash
GHCR_TAG=sha-<previous-commit> make pull-images
docker compose up -d
```

### Model rollback

Set `champion` alias to previous version in MLflow (see [training.md](training.md)), then restart `go-prediction-api`.

### Database rollback

Restore from `./backups/postgres/` or MinIO bucket `mlflow-db-backups`. Test restore procedure in staging before production need.

## Scaling notes

| Component | Scaling approach |
|-----------|------------------|
| `embedding-worker` | Add worker replicas (same Redis queue) |
| `go-prediction-api` | Horizontal replicas behind NGINX (shared model cache volume or pull from MLflow) |
| `trainer-worker` | Single worker recommended per GPU; scale via dedicated training nodes |
| Postgres / MinIO / Redis | Use managed services or clustered setups for production |

Current Compose file targets single-host deployment; multi-host requires external orchestration (Kubernetes, etc.).

## Backup and restore

| Asset | Mechanism | Location |
|-------|-----------|----------|
| Postgres (MLflow + jobs) | `postgres-backup` sidecar | `./backups/postgres/` |
| Offsite DB dumps | `backup-offload` hourly | MinIO `mlflow-db-backups` |
| MLflow artifacts | MinIO volume | `minio_data` volume |
| Local outputs | Bind mount | `./outputs/` |

**Existing Postgres volume without `proseqgo_jobs` DB:**

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -c 'CREATE DATABASE proseqgo_jobs;'
```

Or recreate volume so `docker/postgres/init-proseqgo-jobs.sh` runs on first init.

## Useful Make targets

```bash
make up / make down
make ci-up / make ci-down
make training-up / make training-down
make monitoring-up / make monitoring-down
make lint / make test
make build-images / make pull-images
make smoke
make gateway-auth
```

## Related documentation

- [architecture.md](architecture.md) — service topology
- [monitoring.md](monitoring.md) — observability setup
- [troubleshooting.md](troubleshooting.md) — common deployment failures
- [data.md](data.md) — training data setup (not required for serving-only deploy)
