.PHONY: help up down restart training-up training-down monitoring-up monitoring-down \
	up-all down-all lint test build-images pull-images smoke ci-env

# Product Python paths linted in CI (Phase 1A). Expand later if needed.
LINT_PATHS := src services scripts
PYTHON ?= python3

# Image names match docker-compose.yml local tags.
EMBEDDING_IMAGE ?= proseqgo-embedding-api:local
GO_PRED_IMAGE ?= proseqgo-go-prediction-api:local
STREAMLIT_IMAGE ?= proseqgo-streamlit-ui:local
TRAINER_IMAGE ?= proseqgo-trainer-api:local
MLFLOW_IMAGE ?= proseqgo-mlflow:local
# Local default matches GPU Compose; CI passes cpu index via TORCH_INDEX_URL.
TORCH_INDEX_URL ?= https://download.pytorch.org/whl/cu132

# GHCR (Phase 2). Owner must be lowercase. Tag: main | sha-<fullsha>
GHCR_OWNER ?= behroooz
GHCR_REGISTRY ?= ghcr.io
GHCR_TAG ?= main
GHCR_EMBEDDING_IMAGE ?= $(GHCR_REGISTRY)/$(GHCR_OWNER)/proseqgo-embedding-api:$(GHCR_TAG)
GHCR_GO_PRED_IMAGE ?= $(GHCR_REGISTRY)/$(GHCR_OWNER)/proseqgo-go-prediction-api:$(GHCR_TAG)
GHCR_STREAMLIT_IMAGE ?= $(GHCR_REGISTRY)/$(GHCR_OWNER)/proseqgo-streamlit-ui:$(GHCR_TAG)
GHCR_TRAINER_IMAGE ?= $(GHCR_REGISTRY)/$(GHCR_OWNER)/proseqgo-trainer-api:$(GHCR_TAG)
GHCR_MLFLOW_IMAGE ?= $(GHCR_REGISTRY)/$(GHCR_OWNER)/proseqgo-mlflow:$(GHCR_TAG)

help:
	@echo "Available targets:"
	@echo "  make up               - Start default services with Docker Compose"
	@echo "  make down             - Stop and remove default Docker Compose services"
	@echo "  make training-up      - Start services with the training profile"
	@echo "  make training-down    - Stop services started with the training profile"
	@echo "  make monitoring-up    - Start services with the monitoring profile"
	@echo "  make monitoring-down  - Stop services started with the monitoring profile"
	@echo "  make lint             - Ruff check on src/ services/ scripts/"
	@echo "  make test             - Unit tests (tests/unit; Phase 1B)"
	@echo "  make build-images     - Build all product Docker images"
	@echo "  make pull-images      - Pull product images from GHCR (GHCR_TAG=main|sha-...)"
	@echo "  make smoke            - Run Compose smoke scripts (stack must be up)"
	@echo "  make ci-env           - Copy .env.example -> .env for local/CI Compose"

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose down
	docker compose up -d --build

training-up:
	docker compose --profile training up -d --build

training-down:
	docker compose --profile training down

monitoring-up:
	docker compose --profile monitoring up -d --build

monitoring-down:
	docker compose --profile monitoring down

up-all:
	docker compose --profile monitoring --profile training up -d --build

down-all:
	docker compose --profile monitoring --profile training down

# --- CI / quality (same commands locally and in GitHub Actions) ---

ci-env:
	@test -f .env.example || (echo "Missing .env.example"; exit 1)
	@if [ -f .env ]; then \
		echo ".env already present (left unchanged)"; \
	else \
		cp .env.example .env; \
		echo "Created .env from .env.example"; \
	fi

lint:
	ruff check $(LINT_PATHS)

test:
	$(PYTHON) -m pytest tests/unit -q

build-images:
	docker build \
		--build-arg TORCH_INDEX_URL=$(TORCH_INDEX_URL) \
		-f docker/docker_embedding/Dockerfile.embedding-api \
		-t $(EMBEDDING_IMAGE) .
	docker build \
		--build-arg TORCH_INDEX_URL=$(TORCH_INDEX_URL) \
		-f docker/docker_go_term/Dockerfile.api \
		-t $(GO_PRED_IMAGE) .
	docker build -f docker/docker_streamlit/Dockerfile.streamlit -t $(STREAMLIT_IMAGE) .
	docker build \
		--build-arg TORCH_INDEX_URL=$(TORCH_INDEX_URL) \
		-f docker/docker_training/Dockerfile.training \
		-t $(TRAINER_IMAGE) .
	docker build -f docker/docker_mlflow/Dockerfile -t $(MLFLOW_IMAGE) .

pull-images:
	docker pull $(GHCR_EMBEDDING_IMAGE)
	docker pull $(GHCR_GO_PRED_IMAGE)
	docker pull $(GHCR_STREAMLIT_IMAGE)
	docker pull $(GHCR_TRAINER_IMAGE)
	docker pull $(GHCR_MLFLOW_IMAGE)

# Expects default Compose stack already healthy. Does not start training/GPU jobs.
smoke:
	./tests/smoke/smoke_embedding_api.sh
	@echo "Optional: MLFLOW_TRACKING_URI=http://127.0.0.1/mlflow python tests/smoke/mlflow_smoke_test.py"
	@echo "Optional (heavier): ./tests/smoke/test_embedding_worker_crash_recovery.sh"
