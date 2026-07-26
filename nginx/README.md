# NGINX Milestone Summary

This milestone introduced NGINX as the single ingress gateway for the CAFA-5 MLOps stack and moved public access from direct service ports to centralized reverse proxying.

## What was implemented

- Added `nginx/nginx.conf` as the production-style gateway configuration.
- Added NGINX service to `docker-compose.yml` and exposed only `80/443` publicly.
- Removed direct host port exposure for internal APIs (`embedding-api`, `go-prediction-api`, `mlflow`), so they are now reachable through gateway routes only.
- Added optional training service routing support (`/api/train`) with runtime DNS resolution, allowing NGINX startup even when the training profile is not enabled.

## Gateway behavior and controls

- **TLS termination:** HTTP requests on port 80 are redirected to HTTPS on 443.
- **Authentication segmentation:**  
  - Admin-only access for embedding endpoints and MLflow (`.htpasswd-admin`).  
  - User-level access for prediction endpoints (`.htpasswd-user`).
  - Local discipline: set distinct `GATEWAY_ADMIN_*` and `GATEWAY_USER_*` in `.env`, then `make gateway-auth`.
- **Route-level rate limiting:** Separate request budgets for admin and prediction paths with `429` on limit exceed.
- **Route-specific payload limits:** Enforced `client_max_body_size` per endpoint group to protect upstream services.
- **Hardened proxy timeouts:** Long read/send timeouts for model/training workloads; bounded connect timeout.
- **Structured gateway logging:** Access logs include trace metadata, authenticated user, request tier, and latency fields.

## Routed endpoints

- `/api/v1/` -> `embedding-api:8000`
- `/api/predict/` -> `go-prediction-api:8000/`
- `/api/v1/predict-go-from-sequences` -> `go-prediction-api:8000/predict-go-from-sequences`
- `/api/train` -> `trainer-api:8000` (optional profile)
- `/mlflow/` -> `mlflow:5000/`

## Operational impact

- Centralized ingress policy for security, observability, and traffic governance.
- Clear separation between externally exposed interface and internal service network.
- Better production alignment through HTTPS, auth boundaries, bounded request sizes, and explicit throttling.
