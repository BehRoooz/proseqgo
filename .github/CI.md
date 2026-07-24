# Continuous Integration notes (ProSeqGo)

## What runs today

| Trigger | Job | Command |
|---------|-----|---------|
| `pull_request`, `push` to `main` | Lint | `make lint` → `ruff check src services scripts` |
| `pull_request`, `push` to `main` | Unit tests (after lint) | `make test` → `pytest tests/unit -q` |
| `pull_request` | Image builds (after lint, parallel) | Buildx build of all 5 product images (**no push**) |
| `push` to `main` | Image builds + GHCR publish | Build and push `sha-<fullsha>` + `main` tags |

Unit tests are **Docker/GPU/network free**. They cover sequence normalization, config loading, API schema bounds, embedding vector validation, and UI input gates.

### Image builds

| Image | Dockerfile | GHCR repository |
|-------|------------|-----------------|
| `proseqgo-embedding-api` | `docker/docker_embedding/Dockerfile.embedding-api` | `ghcr.io/behroooz/proseqgo-embedding-api` |
| `proseqgo-go-prediction-api` | `docker/docker_go_term/Dockerfile.api` | `ghcr.io/behroooz/proseqgo-go-prediction-api` |
| `proseqgo-streamlit-ui` | `docker/docker_streamlit/Dockerfile.streamlit` | `ghcr.io/behroooz/proseqgo-streamlit-ui` |
| `proseqgo-trainer-api` | `docker/docker_training/Dockerfile.training` | `ghcr.io/behroooz/proseqgo-trainer-api` |
| `proseqgo-mlflow` | `docker/docker_mlflow/Dockerfile` | `ghcr.io/behroooz/proseqgo-mlflow` |

Policy:

```text
PR     → build only (GHA layer cache, no push)
main   → build + push sha-<fullsha> and moving main
```

- Torch images use **CPU wheels** in CI (`TORCH_INDEX_URL=.../cpu`) for smaller/faster builds; local Compose still defaults to CUDA index
- Local build: `make build-images`
- Pull published images: `make pull-images` or `GHCR_TAG=sha-<fullsha> make pull-images`
- First publish happens after this workflow runs on **`main`**. Packages may be private by default; set package visibility in GitHub Packages if others need to pull.

## Compose / secrets in CI (Phase 3)

Workflows must never commit real secrets. Pattern:

```bash
make ci-env   # copies .env.example → .env if missing
```

Use throwaway passwords from `.env.example` only inside ephemeral CI runners.

## Explicit non-goals for CI

- No training / GPU / full retrain jobs in PR or `main` CI
- No Training API profile in automated smoke (serving stack only)
- Compose smoke lands in Phase 3
