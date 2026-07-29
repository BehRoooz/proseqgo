# Streamlit UI Service

This service provides a lightweight web UI for ProSeqGO (Protein Sequence Gene Ontology prediction platform).

## What Was Implemented

The Streamlit app in `services/streamlit-ui/app.py` includes:

- Project header and description for ProSeqGO sequence-to-GO inference.
- Prediction form with:
  - protein sequence input or FASTA upload,
  - `top_k` input (`1..500`).
- Gateway connection is injected from service environment only (not shown in the form):
  - `GATEWAY_BASE_URL`
  - `GATEWAY_USER` / `GATEWAY_USER_PASSWORD`
  - optional `GATEWAY_VERIFY_TLS` (`true`/`false`; defaults from URL scheme when unset)
- Input QC and validation:
  - trims whitespace/newlines from sequence,
  - uppercases sequence,
  - validates against amino-acid alphabet `ACDEFGHIKLMNPQRSTVWY`,
  - fails fast if gateway env config is missing.
- Prediction call to:
  - `POST /api/v1/predict-go-from-sequences`
- Request payload contract used by UI:
  - `backend: esm2`
  - `pooling: mean`
  - `batch_size: 1`
  - `max_length: 1280`
  - `top_k: <user input>`
  - `sequences: [{"id": "input_1", "sequence": "<cleaned sequence>"}]`
- Robust API error handling:
  - shows network/connection exceptions,
  - shows non-200 status and API `detail`,
  - parses JSON response errors when available.
- Result rendering:
  - model version,
  - GO term predictions as table (`GO Term`, `Score`),
  - per-sequence failures if returned by API.

## Dependencies

Runtime dependencies are pinned in `services/streamlit-ui/requirements.txt`:

- `streamlit`
- `requests`
- `pandas`

## Dockerization

The service container is defined in `docker/docker_streamlit/Dockerfile.streamlit`:

- Base image: `python:3.11-slim`
- Installs Streamlit requirements from `services/streamlit-ui/requirements.txt`
- Copies `services/streamlit-ui/` into `/app/services/streamlit-ui`
- Exposes port `8501`
- Starts app with:
  - `streamlit run /app/services/streamlit-ui/app.py --server.port=8501 --server.address=0.0.0.0`

## docker-compose.yml Modifications

`docker-compose.yml` was extended with `streamlit-ui`:

- New service: `streamlit-ui`
- Build context and dockerfile:
  - `context: .`
  - `dockerfile: docker/docker_streamlit/Dockerfile.streamlit`
- Image name: `proseqgo-streamlit-ui:local`
- Network: `proseqgo`
- Environment:
  - `GATEWAY_BASE_URL: http://nginx`
  - `GATEWAY_USER` / `GATEWAY_USER_PASSWORD` (from `.env`)
  - `GATEWAY_VERIFY_TLS: "false"`
- Dependencies:
  - `embedding-api`
  - `go-prediction-api`
  - `mlflow`
- NGINX now depends on `streamlit-ui` so UI route is available through gateway startup.

## NGINX Routing Modifications

`nginx/nginx.conf` was updated to expose Streamlit on `/ui/`:

- Redirect `/ui` -> `/ui/`
- New `location /ui/` proxy block that:
  - resolves upstream dynamically:
    - `set $streamlit_ui_upstream streamlit-ui:8501;`
  - rewrites path to remove `/ui/` prefix before proxying:
    - `rewrite ^/ui/(.*)$ /$1 break;`
  - proxies to Streamlit:
    - `proxy_pass http://$streamlit_ui_upstream;`
  - keeps websocket-compatible headers:
    - `proxy_set_header Upgrade $http_upgrade;`
    - `proxy_set_header Connection "upgrade";`
  - disables gateway basic auth for UI route:
    - `auth_basic off;`

Important auth boundary:

- Streamlit UI path itself is public (`/ui/`).
- Prediction endpoints still use NGINX basic auth (`.htpasswd-user`).
- The UI injects `GATEWAY_USER` / `GATEWAY_USER_PASSWORD` from the service environment (Compose / `.env`); users never enter credentials in the form. Run `make gateway-auth` so htpasswd matches.
- Admin credentials (`GATEWAY_ADMIN_*`) are for `/mlflow` and admin `/api/v1` routes — not used by the Streamlit UI.

## Run And Access

From repository root:

```bash
docker compose up --build
```

Access UI:

- `https://localhost/ui/` (or `https://127.0.0.1/ui/`)

## Smoke Validation Performed

Validation done for this integration included:

- `streamlit-ui`, `nginx`, `embedding-api`, and `go-prediction-api` up in compose.
- `/ui/` reachable through NGINX.
- Unauthorized call to `/api/v1/predict-go-from-sequences` returns `401`.
- Authorized sequence-to-GO request returns `200` with predictions.
- Streamlit static assets under `/ui/static/...` resolve with correct content types.

## Troubleshooting

- `Connection refused` from Streamlit to `127.0.0.1`:
  - in-container localhost is not NGINX; set `GATEWAY_BASE_URL=http://nginx` on the `streamlit-ui` service.
- Misconfigured UI error about missing gateway env:
  - ensure `GATEWAY_BASE_URL`, `GATEWAY_USER`, and `GATEWAY_USER_PASSWORD` are set for `streamlit-ui`.
- `502 Bad Gateway` for `/ui/*`:
  - can happen with stale upstream resolution if upstream is hardcoded;
  - current config uses runtime DNS with variable upstream.
- Blank white page:
  - usually static asset path/proxy mismatch;
  - current rewrite/proxy config is set for `/ui/` reverse proxy path.
