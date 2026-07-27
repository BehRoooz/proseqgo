# Smoke / acceptance checks against a running Compose stack.
#
# These are not unit tests: they expect docker compose services to be up.
#
# Gateway credentials live in `.env` (`GATEWAY_ADMIN_*`, `GATEWAY_USER_*`).
# Sync nginx htpasswd (admin ≠ public user):
#
#   make gateway-auth
#
# Local (GPU if available):
#   make up
#   make smoke
#
# CPU / CI stack (base + docker-compose.ci.yml):
#   make ci-up      # also runs ci-env + gateway-auth
#   make smoke
#   make ci-down
#
# From repo root (stack must be up):
#   ./tests/smoke/smoke_embedding_api.sh
#   ./tests/smoke/test_embedding_worker_crash_recovery.sh
#   MLFLOW_TRACKING_URI=http://127.0.0.1/mlflow python tests/smoke/mlflow_smoke_test.py
#
# Admin credentials are used for /api/v1/jobs*; public user for predict-go-*.
