from pathlib import Path
import os

API_PREFIX = "/api/v1"
ARTIFACT_ROOT = Path(os.getenv("EMBEDDING_ARTIFACT_ROOT", "/app/outputs/service_artifacts"))

DEFAULT_STAGE = "test"
DEFAULT_BACKEND = "esm2"
DEFAULT_POOLING = "mean"
DEFAULT_BATCH_SIZE = 8
DEFAULT_MAX_LENGTH = 1280

MAX_FASTA_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

GO_PREDICTION_API_URL = os.getenv("GO_PREDICTION_API_URL", "http://go-prediction-api:8000")

JOBS_DATABASE_URL = os.getenv(
    "JOBS_DATABASE_URL",
    "postgresql://mlflow:change-me-postgres@postgres:5432/proseqgo_jobs",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
RQ_QUEUE_NAME = os.getenv("EMBEDDING_RQ_QUEUE", "embedding-jobs")
JOB_TIMEOUT_SEC = int(os.getenv("EMBEDDING_JOB_TIMEOUT_SEC", "3600"))
RQ_RETRY_MAX = int(os.getenv("EMBEDDING_RQ_RETRY_MAX", "3"))
RQ_RETRY_INTERVALS = [10, 60, 180]
WORKER_METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "8001"))
