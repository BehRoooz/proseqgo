# Contributing

Guidelines for changing ProSeqGO safely: setup, workflow, standards, and what to update with each change type.

## Contribution scope

Contributions welcome for:

- Bug fixes in pipelines, APIs, and infrastructure
- Tests for critical logic (validation, config, schemas)
- Documentation improvements
- Monitoring dashboards and alert tuning
- CI and Docker improvements

**Requires design discussion before implementation:**

- New public API endpoints or breaking request/response contracts
- Changes to model input/output schema or registry alias strategy
- New external dependencies (especially GPU/torch-related)
- Database schema changes for `proseqgo_jobs`
- Security model changes (auth tiers, TLS, exposed ports)

## Development setup

### Minimal setup (code changes only)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make lint
make test
```

Unit tests do **not** require Docker, GPU, or network access.

### Full stack (integration work)

```bash
make ci-env
make gateway-auth
make up
make monitoring-up   # optional
make smoke           # after stack is healthy
```

See [deployment.md](deployment.md) for profiles and secrets.

## Branch and PR workflow

1. Branch from `main` with a descriptive name (e.g. `fix/embedding-validation`, `docs/monitoring-runbook`)
2. Keep PRs focused; prefer small reviewable diffs
3. Ensure CI passes: lint, unit tests, image builds on PR
4. Include a **test plan** in the PR description (commands run, screenshots for UI)
5. Request review from a maintainer familiar with the affected area (ML, API, infra)

**Merge policy:** squash or merge commit per team convention; `main` publishes images to GHCR.

## Coding standards

### Python

- Target Python **3.10+**
- Style: [Ruff](https://docs.astral.sh/ruff/) (`make lint` → `ruff check src services scripts`)
- Line length: 100 (see `pyproject.toml`)
- Type hints where they clarify non-obvious contracts
- Match existing patterns in the module you edit

### Layout conventions

| Path | Purpose |
|------|---------|
| `src/` | Core ML, preprocessing, training, inference |
| `services/` | FastAPI/Streamlit service code |
| `scripts/` | CLI entrypoints |
| `configs/` | YAML configuration |
| `tests/unit/` | Fast pytest (CI) |
| `tests/smoke/` | Compose acceptance scripts (manual/CI Phase 3) |

### Config and logging

- Use `src/config.py` / `load_config()` for pipeline config
- No hardcoded secrets, paths, or machine-specific settings
- Use `setup_logger()` from `src/utils.py` for CLI scripts

### Comments

Prefer self-explanatory code. Comment only non-obvious business logic, protocol quirks, or operational constraints.

## Testing expectations

### Unit tests (`make test`)

Located in `tests/unit/`. Current coverage areas:

- Sequence normalization
- Config loading
- API schema bounds (Pydantic)
- Embedding vector validation
- UI input validation

**Required before merge:** `make lint` and `make test` pass.

### Smoke tests

```bash
make ci-up && make smoke && make ci-down
```

Scripts in `tests/smoke/` — not part of unit test suite. Run when touching embedding API, workers, or compose wiring.

### What CI does not run

- Full training or retrain jobs
- GPU workloads
- Training API profile (see [`.github/CI.md`](../.github/CI.md))

Do not rely on CI to catch training regressions; document manual validation in PR test plan.

## Change categories and required updates

| Change type | Update |
|-------------|--------|
| New/changed API endpoint | Service README, request schemas, unit tests, [troubleshooting.md](troubleshooting.md) if user-facing |
| Config key added | `configs/config.yaml`, `src/config.py`, [training.md](training.md) or [data.md](data.md) |
| Model pipeline change | [training.md](training.md), reproducibility notes in PR |
| Compose / env var | `.env.example`, [deployment.md](deployment.md), `Makefile` if new target |
| New metric or alert | `monitoring/alerts.yml`, dashboard JSON, [monitoring.md](monitoring.md) |
| NGINX route change | `nginx/nginx.conf`, `nginx/README.md`, [architecture.md](architecture.md) |
| Dataset path or checksum | [data.md](data.md) only (do not commit raw data) |

**Do not modify `README.md` unless the PR explicitly scopes documentation at the top level** — deep docs live under `docs/`.

## Commit and PR description

**Commit messages:** concise, imperative mood, explain *why* when not obvious.

Examples:

- `fix embedding-api route label cardinality for Prometheus`
- `docs: add deployment rollback procedure`
- `gate champion promotion on holdout_f1_micro threshold`

**PR description should include:**

- Summary of change (1–3 bullets)
- Test plan (commands executed)
- Rollout notes if deploy or model promotion is affected
- Screenshots for Streamlit/Grafana changes

## Release and model promotion

- **Image releases:** merging to `main` triggers GHCR publish (`sha-<commit>` and `main` tags)
- **Model promotion:** only after holdout evaluation passes threshold; document `train_run_id` and `eval_run_id` in change log or MLflow
- **Production config:** gateway passwords, Postgres, and MinIO credentials must be rotated via ops process — never in git

Restrict who can:

- Set `champion` alias in production MLflow
- Change production `.env` and NGINX configuration
- Modify alert thresholds that page on-call

## Security practices

- Never commit `.env`, `kaggle.json`, htpasswd files with real passwords, or raw datasets
- Use `.env.example` for variable names only
- Run `make gateway-auth` locally; use throwaway passwords in CI
- Report security issues privately to maintainers (do not open public issues for active vulnerabilities)

## Dependency updates

- Pin breaking changes (e.g. `mlflow==2.13.0` in `pyproject.toml`)
- Torch: local `make build-images` uses CUDA index; CI uses CPU wheels — test both paths when upgrading torch
- Avoid new dependencies unless necessary; prefer stdlib and existing stack

## Getting help

- Architecture questions → [architecture.md](architecture.md)
- Operational issues → [troubleshooting.md](troubleshooting.md)
- CI behavior → [`.github/CI.md`](../.github/CI.md)
- Service-specific behavior → `services/*/README.md`

## License

By contributing, you agree that your contributions are licensed under the project MIT license.
