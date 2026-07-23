# Smoke / acceptance checks against a running Compose stack.
#
# These are not unit tests: they expect docker compose services to be up.
#
# From repo root:
#   ./tests/smoke/smoke_embedding_api.sh
#   ./tests/smoke/test_embedding_worker_crash_recovery.sh
#   MLFLOW_TRACKING_URI=http://127.0.0.1/mlflow python tests/smoke/mlflow_smoke_test.py
