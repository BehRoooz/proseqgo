.PHONY: help up down training-up training-down monitoring-up monitoring-down

help:
	@echo "Available targets:"
	@echo "  make up               - Start default services with Docker Compose"
	@echo "  make down             - Stop and remove default Docker Compose services"
	@echo "  make training-up      - Start services with the training profile"
	@echo "  make training-down    - Stop services started with the training profile"
	@echo "  make monitoring-up    - Start services with the monitoring profile"
	@echo "  make monitoring-down  - Stop services started with the monitoring profile"

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
	docker compose --profile monitoring up -d

monitoring-down:
	docker compose --profile monitoring down

up-all:
	docker compose --profile monitoring up -d --build

down-all:
	docker compose down