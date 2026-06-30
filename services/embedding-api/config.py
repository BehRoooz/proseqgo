from pathlib import Path
import os

API_PREFIX = "/api/v1"
ARTIFACT_ROOT = Path("outputs/service_artifacts")
DB_PATH = ARTIFACT_ROOT / "jobs.db"

DEFAULT_STAGE = "test"
DEFAULT_BACKEND = "esm2"
DEFAULT_POOLING = "mean"
DEFAULT_BATCH_SIZE = 8
DEFAULT_MAX_LENGTH = 1280

MAX_FASTA_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

WORKER_POLL_INTERVAL_SEC = 1.0
GO_PREDICTION_API_URL = os.getenv("GO_PREDICTION_API_URL", "http://go-prediction-api:8000")