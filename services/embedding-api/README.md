# Embedding API (GPU-first via CAFA_DEVICE, async jobs)

Minimal FastAPI service to generate protein language model embeddings and persist them as `.npy` artifacts.

## What this service does

- Accepts protein inputs as either:
  - JSON `sequence_list` (list of `{id, sequence}`), or
  - FASTA upload
- Runs embedding asynchronously via a background worker thread
- Writes outputs to disk as `.npy` under:
  - `outputs/service_artifacts/{job_id}/`
- Uses in-process model caching (models are loaded once and reused while the service runs)

Current scope (Milestone 2, v1):

- `stage` is effectively `test` only (embeds provided input; no labels)

## Requirements

- Python 3.10+
- Dependencies: `fastapi`, `uvicorn`, `python-multipart`, `torch`, `transformers`, `numpy`

## Run with Docker (Compose)

From the **repository root**, use the project-wide Compose file and smoke test:

```bash
docker compose up --build
# other terminal:
chmod +x scripts/smoke_embedding_api.sh && ./scripts/smoke_embedding_api.sh
```

See also [../../README.md](../../README.md) → **How to run with Docker** for volumes, output shapes, and manual `curl` examples.

## Run the service (local)

From repo root (`CAFA-5-MLOps-solution/`):

```bash
uvicorn main:app --app-dir services/embedding-api --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/api/v1/health
# Example: {"status":"ok","device":"cuda","cuda_available":true,"cafa_device":"auto",...}
```

Set `CAFA_DEVICE=auto` (default), `cuda`, or `cpu` to control the embedding device.

## Endpoints

### 1) Create a job (JSON sequence list)

`POST /api/v1/jobs`

Request body:

```json
{
  "stage": "test",
  "backend": "esm2",
  "pooling": "mean",
  "batch_size": 2,
  "max_length": 1280,
  "sequences": [
    { "id": "P1", "sequence": "MKTAYIAKQRQISFVKSHFSRQ" },
    { "id": "P2", "sequence": "GAVLIPFYWSTCMNQDEKRH" }
  ]
}
```

Response (`202 Accepted`):

- `job_id`
- `status` (queued)
- `poll_url` (where to check progress)

### 2) Create a job (FASTA upload)

`POST /api/v1/jobs/fasta` (multipart form)

Form fields:

- `fasta_file` (required)
- `backend` (default `esm2`)
- `pooling` (default `mean`)
- `batch_size` (default `8`)
- `max_length` (default `1280`)

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs/fasta \
  -F "fasta_file=@/path/to/sample.fasta" \
  -F "backend=esm2" \
  -F "pooling=mean" \
  -F "batch_size=4" \
  -F "max_length=1280"
```

### 3) Poll job status

`GET /api/v1/jobs/{job_id}`

Returns:

- `status`: `queued | running | succeeded | failed`
- `progress`: `{embedded_sequences, total_sequences, percent}`
- `artifacts_manifest` (once artifacts exist)
- `error` (if failed)

### 4) Download artifacts

`GET /api/v1/jobs/{job_id}/artifacts/{name}`

Only available when the job is in `succeeded`.

Currently produced artifacts for `test` stage:

- `test_ids.npy`
- `test_embeddings.npy`

Example:

```bash
curl -o test_ids.npy \
  http://127.0.0.1:8000/api/v1/jobs/<JOB_ID>/artifacts/test_ids.npy
```

```bash
curl -o test_embeddings.npy \
  http://127.0.0.1:8000/api/v1/jobs/<JOB_ID>/artifacts/test_embeddings.npy
```

### 5) Predict GO from sequences (sync)

`POST /api/v1/predict-go-from-sequences` (JSON)

Convenience wrapper: creates an embedding job, waits for completion, then calls the GO prediction API for each sequence. Returns the same `PredictGoResponse` shape as the FASTA variant below.

See the [root README](../../README.md) → **Sequence -> GO in one call** for the full request contract and gateway `curl` example.

### 6) Predict GO from FASTA (sync)

`POST /api/v1/predict-go-from-fasta` (`multipart/form-data`)

Same orchestration as **predict-go-from-sequences** (embed → wait → predict GO), but accepts a FASTA upload instead of a JSON sequence list. `sequence_id` in the response is the first token after `>` in each FASTA header (same parsing as `/jobs/fasta`).

Form fields:

- `fasta_file` (required) — UTF-8 FASTA file
- `backend` (default `esm2`) — `esm2 | protbert | t5`
- `pooling` (default `mean`) — `mean | cls`
- `batch_size` (default `8`) — `1..128`
- `max_length` (default `1280`) — `8..8192`
- `top_k` (default `10`) — `1..500`
- `fail_fast` (default `true`)
- `timeout_seconds` (default `1800`) — `5..7200` (embedding wait + sequential GO calls)
- `poll_interval_seconds` (default `1.0`) — `0.1..5.0`

Example (local, no gateway auth):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/predict-go-from-fasta \
  -F "fasta_file=@examples/small_sequences.fasta" \
  -F "backend=esm2" \
  -F "pooling=mean" \
  -F "batch_size=2" \
  -F "max_length=1280" \
  -F "top_k=10" \
  -F "fail_fast=true"
```

Response (`200 OK`, simplified):

```json
{
  "job_id": "uuid",
  "status": "succeeded",
  "model_version": "12",
  "top_k": 10,
  "results": [
    {
      "index": 0,
      "sequence_id": "Q9CQV8",
      "predictions": [{"go_term": "GO:0000000", "score": 0.82}]
    }
  ],
  "failures": []
}
```

**Residue normalization:** Sequences are normalized at embedding time: uppercase, whitespace stripped, canonical amino acids kept, and `X`/`U`/`O`/`B`/`Z`/`J` plus any other unknown symbols mapped to `X` (see `normalize_sequence` in `scripts/embed_sequences.py`). No extra validation is applied in this endpoint beyond FASTA parsing.

**Operational limits:**

- Max FASTA upload: **5 MB** (API enforces after read; NGINX gateway route uses `client_max_body_size 5m`). Uploads larger than the cap return `413` with `FASTA_FILE_TOO_LARGE`.
- No separate sequence-count cap — only file size is limited. A dense FASTA under 5 MB can still contain many records; GO inference runs sequentially and may hit `504 EMBEDDING_JOB_TIMEOUT` or the overall request timeout.
- Default sync timeout: **1800 s** (embedding + polling + per-sequence GO calls).
- For proteome-scale or long-running jobs, use the async path: `POST /api/v1/jobs/fasta` → poll `GET /api/v1/jobs/{job_id}` → `POST /api/v1/jobs/{job_id}/predict-go`.

**Subset prediction:** This endpoint always predicts all parsed FASTA records. To predict a subset by index, use the async job path with `indices` on `POST /api/v1/jobs/{job_id}/predict-go`.

## Storage layout

- Job DB:
  - `outputs/service_artifacts/jobs.db`
- Artifacts:
  - `outputs/service_artifacts/{job_id}/test_ids.npy`
  - `outputs/service_artifacts/{job_id}/test_embeddings.npy`

## Model behavior (important)

- Backend options supported by the code: `esm2`, `protbert`, `t5`
- Device is resolved via `CAFA_DEVICE` (`auto` uses CUDA when available, else CPU). FP16 autocast is enabled on CUDA.
- Embeddings are saved as `float32`.
- Output shape is `(N, D)`, where `D` depends on backend:
  - `esm2`: `D=1280`
  - `protbert`: `D=1024`
  - `t5`: `D=1024`

## Troubleshooting

- First request may be slow: Hugging Face model/tokenizer downloads can take time.
- Ensure `outputs/service_artifacts/` is writable.
- If you get `Failed` jobs, check the API logs for the detailed exception message.

