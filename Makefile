.PHONY: help up down restart training-up training-down monitoring-up monitoring-down \
	all-up all-down lint test build-images pull-images smoke ci-env gateway-auth ci-up ci-down

# Product Python paths linted in CI (Phase 1A). Expand later if needed.
LINT_PATHS := src services scripts
PYTHON ?= python3

# Compose file sets (Design A: portable base + optional overlays).
COMPOSE ?= docker compose
COMPOSE_BASE := -f docker-compose.yml
COMPOSE_GPU := -f docker-compose.gpu.yml
COMPOSE_CI := -f docker-compose.ci.yml
# Auto-enable GPU overlay when nvidia-smi works (local dev).
HAS_NVIDIA := $(shell command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && echo 1)
ifeq ($(HAS_NVIDIA),1)
COMPOSE_DEV_FILES := $(COMPOSE_BASE) $(COMPOSE_GPU)
else
COMPOSE_DEV_FILES := $(COMPOSE_BASE)
endif
COMPOSE_CI_FILES := $(COMPOSE_BASE) $(COMPOSE_CI)

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
	@echo "  make up               - Start default services (GPU overlay if NVIDIA detected)"
	@echo "  make down             - Stop default Docker Compose services"
	@echo "  make ci-up            - Start serving stack for CI/CPU smoke (base + ci overlay)"
	@echo "  make ci-down          - Stop CI stack and remove volumes"
	@echo "  make training-up      - Start services with the training profile"
	@echo "  make training-down    - Stop services started with the training profile"
	@echo "  make monitoring-up    - Start services with the monitoring profile"
	@echo "  make monitoring-down  - Stop services started with the monitoring profile"
	@echo "  make lint             - Ruff check on src/ services/ scripts/"
	@echo "  make test             - Unit tests (tests/unit; Phase 1B)"
	@echo "  make build-images     - Build all product Docker images"
	@echo "  make pull-images      - Pull product images from GHCR (GHCR_TAG=main|sha-...)"
	@echo "  make smoke            - Run Compose smoke scripts (stack must be up)"
	@echo "  make ci-env           - Copy .env.example -> .env if missing"
	@echo "  make gateway-auth     - Write nginx/.htpasswd-* from GATEWAY_* in .env"

up:
	$(COMPOSE) $(COMPOSE_DEV_FILES) up -d --build

down:
	$(COMPOSE) $(COMPOSE_DEV_FILES) down

restart:
	$(COMPOSE) $(COMPOSE_DEV_FILES) down
	$(COMPOSE) $(COMPOSE_DEV_FILES) up -d --build

ci-up: ci-env gateway-auth
	$(COMPOSE) $(COMPOSE_CI_FILES) up -d --build

ci-down:
	$(COMPOSE) $(COMPOSE_CI_FILES) down -v

training-up:
	$(COMPOSE) $(COMPOSE_DEV_FILES) --profile training up -d --build

training-down:
	$(COMPOSE) $(COMPOSE_DEV_FILES) --profile training down

monitoring-up:
	$(COMPOSE) $(COMPOSE_DEV_FILES) --profile monitoring up -d --build

monitoring-down:
	$(COMPOSE) $(COMPOSE_DEV_FILES) --profile monitoring down

all-up:
	$(COMPOSE) $(COMPOSE_DEV_FILES) --profile monitoring --profile training up -d --build

all-down:
	$(COMPOSE) $(COMPOSE_DEV_FILES) --profile monitoring --profile training down

# --- CI / quality (same commands locally and in GitHub Actions) ---

ci-env:
	@test -f .env.example || (echo "Missing .env.example"; exit 1)
	@if [ -f .env ]; then \
		echo ".env already present (left unchanged)"; \
	else \
		cp .env.example .env; \
		echo "Created .env from .env.example"; \
	fi

gateway-auth: ci-env
	./scripts/prepare_gateway_auth.sh

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
