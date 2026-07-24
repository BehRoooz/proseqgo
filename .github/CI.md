# Continuous Integration notes (ProSeqGo)

## What runs today

| Trigger | Job | Command |
|---------|-----|---------|
| `pull_request`, `push` to `main` | Lint | `make lint` → `ruff check src services scripts` |
| `pull_request`, `push` to `main` | Unit tests (after lint) | `make test` → `pytest tests/unit -q` |
| `pull_request`, `push` to `main` | Image builds (after lint, parallel) | Buildx build of all 5 product images (**no push**) |

Unit tests are **Docker/GPU/network free**. They cover sequence normalization, config loading, API schema bounds, embedding vector validation, and UI input gates.

### Image builds (Phase 1C)

| Image | Dockerfile |
|-------|------------|
| `proseqgo-embedding-api` | `docker/docker_embedding/Dockerfile.embedding-api` |
| `proseqgo-go-prediction-api` | `docker/docker_go_term/Dockerfile.api` |
| `proseqgo-streamlit-ui` | `docker/docker_streamlit/Dockerfile.streamlit` |
| `proseqgo-trainer-api` | `docker/docker_training/Dockerfile.training` |
| `proseqgo-mlflow` | `docker/docker_mlflow/Dockerfile` |

- PR/`main`: **build only** (GHA layer cache, `outputs: type=cacheonly`)
- Torch images use **CPU wheels** in CI (`TORCH_INDEX_URL=.../cpu`) for smaller/faster builds; local Compose still defaults to CUDA index
- Local: `make build-images` (or `TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu make build-images`)

## Registry (Phase 2)

Images will publish to **GHCR** (`ghcr.io/BehRoooz/...`) with immutable `sha-<gitsha>` tags. Not wired yet.

## Compose / secrets in CI (Phase 3)

Workflows must never commit real secrets. Pattern:

```bash
make ci-env   # copies .env.example → .env if missing
```

Use throwaway passwords from `.env.example` only inside ephemeral CI runners.

## Explicit non-goals for CI

- No training / GPU / full retrain jobs in PR or `main` CI
- No Training API profile in automated smoke (serving stack only)
- No registry push yet (Phase 2); Compose smoke lands in Phase 3
