from __future__ import annotations

import os
from pathlib import Path

API_PREFIX = "/api/train"
ARTIFACT_ROOT = Path(os.getenv("TRAINING_API_ARTIFACT_ROOT", "/app/outputs/training_api"))

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns")
# Browser-reachable MLflow UI (host port), for links returned to API clients.
MLFLOW_EXTERNAL_UI_BASE = os.getenv("MLFLOW_EXTERNAL_UI_BASE", "http://127.0.0.1:5000")

JOBS_DATABASE_URL = os.getenv(
    "JOBS_DATABASE_URL",
    "postgresql://mlflow:change-me-postgres@postgres:5432/proseqgo_jobs",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
RQ_QUEUE_NAME = os.getenv("TRAINING_RQ_QUEUE", "training-jobs")
JOB_TIMEOUT_SEC = int(os.getenv("TRAINING_JOB_TIMEOUT_SEC", "86400"))
RQ_RETRY_MAX = int(os.getenv("TRAINING_RQ_RETRY_MAX", "3"))
RQ_RETRY_INTERVALS = [10, 60, 180]
WORKER_METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "8001"))
