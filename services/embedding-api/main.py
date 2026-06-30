from __future__ import annotations

import threading
import uuid
import json
import time
from pathlib import Path
from typing import Literal
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest, CollectorRegistry

from config import API_PREFIX, ARTIFACT_ROOT, DB_PATH, GO_PREDICTION_API_URL, MAX_FASTA_UPLOAD_BYTES
from job_store import JobStore
from schemas import (
    CreateJobRequest,
    CreateJobResponse,
    JobStatusResponse,
    PredictGoFromSequencesRequest,
    PredictGoRequest,
    PredictGoResponse,
)
from src.utils import get_device_info
from worker import parse_fasta_text, worker_loop

app = FastAPI(title="Embedding API", version="0.1.0")

# Prometheus metrics for the embedding API

registry = CollectorRegistry() # to store metrics


SERVICE_NAME = "embedding-api"

HTTP_REQUESTS_TOTAL = Counter(
    "cafa5_http_requests_total",
    "Total number of HTTP requests.",
    labelnames=("service", "route", "method", "status_code"),
    registry=registry,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "cafa5_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=("service", "route", "method", "status_code"),
    registry=registry,
)
HTTP_IN_FLIGHT_REQUESTS = Gauge(
    "cafa5_http_in_flight_requests",
    "Number of in-flight HTTP requests.",
    labelnames=("service",),
    registry=registry,
)
EMBEDDING_SEQUENCE_LENGTH = Histogram(
    "cafa5_embedding_sequence_length",
    "Observed amino-acid sequence lengths partitioned by embedding backend.",
    labelnames=("backend",),
    buckets=(16, 32, 64, 128, 256, 512, 1024, 1280, 2048, 4096, 8192, float("inf")),
    registry=registry,
)
EMBEDDING_DIMENSION_MISMATCHES_TOTAL = Counter(
    "cafa5_embedding_dimension_mismatch_total",
    "Total number of embedding dimension mismatches detected before GO inference.",
    registry=registry,
)

store = JobStore(DB_PATH)
stop_event = threading.Event()
worker_thread: threading.Thread | None = None

# observe the sequence lengths for the embedding backend
def _observe_sequence_lengths(backend: str, sequences: list[str]) -> None:
    for sequence in sequences:
        EMBEDDING_SEQUENCE_LENGTH.labels(backend=backend).observe(len(sequence))

# normalize the route labels for the metrics
def _route_label(path: str) -> str:
    if path == "/metrics":
        return "/metrics"
    if path.startswith(API_PREFIX):
        return path[len(API_PREFIX) :] or "/"
    return path

# middleware to collect the metrics for the HTTP requests
@app.middleware("http")
async def prometheus_http_middleware(request, call_next):
    route = _route_label(request.url.path)
    method = request.method

    HTTP_IN_FLIGHT_REQUESTS.labels(service=SERVICE_NAME).inc()
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - start
        if route != "/metrics":
            labels = {
                "service": SERVICE_NAME,
                "route": route,
                "method": method,
                "status_code": str(status_code),
            }
            HTTP_REQUESTS_TOTAL.labels(**labels).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(**labels).observe(duration)
        HTTP_IN_FLIGHT_REQUESTS.labels(service=SERVICE_NAME).dec()


# startup event to start the worker thread
@app.on_event("startup")
def startup_event() -> None:
    global worker_thread
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    worker_thread = threading.Thread(target=worker_loop, args=(store, stop_event), daemon=True)
    worker_thread.start()

# shutdown event to stop the worker thread
@app.on_event("shutdown")
def shutdown_event() -> None:
    stop_event.set()
    if worker_thread is not None:
        worker_thread.join(timeout=2)

# health endpoint to check the status of the API
@app.get(API_PREFIX + "/health")
def health() -> dict[str, str | bool]:
    return {"status": "ok", **get_device_info()}

# metrics endpoint to get the metrics for the API
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

# create job endpoint to create a new job
@app.post(API_PREFIX + "/jobs", response_model=CreateJobResponse, status_code=202)
def create_job(request: CreateJobRequest) -> CreateJobResponse:
    _observe_sequence_lengths(
        backend=request.backend,
        sequences=[seq.sequence for seq in request.sequences],
    )
    job_id = str(uuid.uuid4())
    store.create_job(job_id, request.model_dump())
    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        poll_url=f"{API_PREFIX}/jobs/{job_id}",
    )

# create fasta job endpoint to create a new job from a FASTA file
@app.post(API_PREFIX + "/jobs/fasta", response_model=CreateJobResponse, status_code=202)
async def create_fasta_job(
    fasta_file: UploadFile = File(...),
    backend: Literal["esm2", "protbert", "t5"] = Form(default="esm2"),
    pooling: Literal["mean", "cls"] = Form(default="mean"),
    batch_size: int = Form(default=8),
    max_length: int = Form(default=1280),
) -> CreateJobResponse:
    fasta_text = (await fasta_file.read()).decode("utf-8", errors="replace")
    if not fasta_text.strip():
        raise HTTPException(status_code=400, detail="Uploaded FASTA is empty.")
    try:
        _, sequences = parse_fasta_text(fasta_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _observe_sequence_lengths(backend=backend, sequences=sequences)

    job_id = str(uuid.uuid4())
    payload = {
        "stage": "test",
        "backend": backend,
        "pooling": pooling,
        "batch_size": batch_size,
        "max_length": max_length,
        "fasta_text": fasta_text,
    }
    store.create_job(job_id, payload)
    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        poll_url=f"{API_PREFIX}/jobs/{job_id}",
    )

# get job endpoint to get the status of a job
@app.get(API_PREFIX + "/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")

    artifacts = store.list_artifacts(job_id)
    req = job["request"]
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        stage=req.get("stage", "test"),
        backend=req.get("backend", "esm2"),
        progress=job["progress"],
        error=job["error"],
        artifacts_manifest=artifacts if artifacts else None,
    )

# get artifact endpoint to get an artifact for a job
@app.get(API_PREFIX + "/jobs/{job_id}/artifacts/{name}")
def get_artifact(job_id: str, name: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="JOB_NOT_READY")

    artifacts = store.list_artifacts(job_id)
    match = next((a for a in artifacts if a["name"] == name), None)
    if match is None:
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")

    path = Path(match["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")
    return FileResponse(path=str(path), filename=name, media_type="application/octet-stream")

# post go predict endpoint to predict the GO terms for an embedding
def _post_go_predict(embedding: list[float], top_k: int) -> dict:
    endpoint = f"{GO_PREDICTION_API_URL.rstrip('/')}/predict" # go prediction API endpoint
    payload = json.dumps({"embedding": embedding, "top_k": top_k}).encode("utf-8") # payload to send to the go prediction API
    req = urlrequest.Request( # request to the go prediction API
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try: # try to predict the GO terms for the embedding
        with urlrequest.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {} # return the response from the go prediction API
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace") # error body from the go prediction API
        raise HTTPException( # raise an HTTP exception if the go prediction API is not reachable
            status_code=502,
            detail=f"GO_API_HTTP_{exc.code}: {err_body}",
        ) from exc
    except URLError as exc:
        raise HTTPException( # raise an HTTP exception if the go prediction API is not reachable
            status_code=502,
            detail=f"GO_API_UNREACHABLE: {exc.reason}",
        ) from exc

# predict go for job endpoint to predict the GO terms for a job
def _predict_go_for_job(job_id: str, request: PredictGoRequest) -> PredictGoResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="JOB_NOT_READY")

    artifacts = store.list_artifacts(job_id)
    ids_entry = next((a for a in artifacts if a["name"] == "test_ids.npy"), None)
    emb_entry = next((a for a in artifacts if a["name"] == "test_embeddings.npy"), None)
    if ids_entry is None or emb_entry is None:
        raise HTTPException(status_code=404, detail="EMBEDDING_ARTIFACTS_NOT_FOUND")

    ids = np.load(ids_entry["path"], allow_pickle=True)
    embeddings = np.load(emb_entry["path"])
    if embeddings.ndim != 2:
        raise HTTPException(status_code=500, detail="INVALID_EMBEDDINGS_SHAPE")
    if len(ids) != embeddings.shape[0]:
        raise HTTPException(status_code=500, detail="IDS_EMBEDDINGS_LENGTH_MISMATCH")
    if embeddings.shape[1] != 1280:
        EMBEDDING_DIMENSION_MISMATCHES_TOTAL.inc()
        raise HTTPException(
            status_code=400,
            detail=(
                "GO API expects 1280-dim embeddings. "
                f"Received dimension {embeddings.shape[1]} from embedding job."
            ),
        )

    if request.indices is None:
        selected_indices = list(range(embeddings.shape[0]))
    else:
        if not request.indices:
            raise HTTPException(status_code=400, detail="indices must not be empty")
        selected_indices = request.indices
        bad = [i for i in selected_indices if i < 0 or i >= embeddings.shape[0]]
        if bad:
            raise HTTPException(status_code=400, detail=f"indices out of range: {bad}")

    results: list[dict] = []
    failures: list[dict] = []
    model_version: str | None = None
    for idx in selected_indices:
        try:
            response = _post_go_predict(embeddings[idx].astype(float).tolist(), request.top_k)
            if model_version is None:
                model_version = response.get("model_version")
            results.append(
                {
                    "index": idx,
                    "sequence_id": str(ids[idx]),
                    "predictions": response.get("predictions", []),
                }
            )
        except HTTPException as exc:
            failure = {"index": idx, "sequence_id": str(ids[idx]), "error": exc.detail}
            if request.fail_fast:
                raise HTTPException(status_code=502, detail=failure) from exc
            failures.append(failure)

    return PredictGoResponse(
        job_id=job_id,
        status="succeeded",
        model_version=model_version,
        top_k=request.top_k,
        results=results,
        failures=failures,
    )

# predict go for job endpoint to predict the GO terms for a job
@app.post(API_PREFIX + "/jobs/{job_id}/predict-go", response_model=PredictGoResponse)
def predict_go_for_job(job_id: str, request: PredictGoRequest) -> PredictGoResponse:
    return _predict_go_for_job(job_id, request)

def _parse_and_validate_fasta(fasta_text: str, backend: str) -> None:
    try:
        ids, sequences = parse_fasta_text(fasta_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    empty_ids = [seq_id for seq_id, seq in zip(ids, sequences) if not seq.strip()]
    if empty_ids:
        preview = ", ".join(empty_ids[:5])
        suffix = "..." if len(empty_ids) > 5 else ""
        raise HTTPException(
            status_code=400,
            detail=f"FASTA records with empty sequences: {preview}{suffix}",
        )
    _observe_sequence_lengths(backend=backend, sequences=sequences)


def _validate_predict_form_params(
    *,
    batch_size: int,
    max_length: int,
    top_k: int,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    if not 1 <= batch_size <= 128:
        raise HTTPException(status_code=400, detail="batch_size must be between 1 and 128")
    if not 8 <= max_length <= 8192:
        raise HTTPException(status_code=400, detail="max_length must be between 8 and 8192")
    if not 1 <= top_k <= 500:
        raise HTTPException(status_code=400, detail="top_k must be between 1 and 500")
    if not 5 <= timeout_seconds <= 7200:
        raise HTTPException(status_code=400, detail="timeout_seconds must be between 5 and 7200")
    if not 0.1 < poll_interval_seconds <= 5.0:
        raise HTTPException(
            status_code=400,
            detail="poll_interval_seconds must be greater than 0.1 and at most 5.0",
        )


async def _read_fasta_upload(fasta_file: UploadFile) -> str:
    raw = await fasta_file.read(MAX_FASTA_UPLOAD_BYTES + 1)
    if len(raw) > MAX_FASTA_UPLOAD_BYTES:
        max_mb = MAX_FASTA_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"FASTA_FILE_TOO_LARGE: max {max_mb} MB",
        )
    fasta_text = raw.decode("utf-8", errors="replace")
    if not fasta_text.strip():
        raise HTTPException(status_code=400, detail="Uploaded FASTA is empty.")
    return fasta_text


def _predict_go_from_job_payload(
    job_payload: dict,
    *,
    top_k: int,
    indices: list[int] | None,
    fail_fast: bool,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> PredictGoResponse:
    job_id = str(uuid.uuid4())
    store.create_job(job_id, job_payload)
    _wait_for_job_completion(
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return _predict_go_for_job(
        job_id=job_id,
        request=PredictGoRequest(top_k=top_k, indices=indices, fail_fast=fail_fast),
    )


# wait for job completion endpoint to wait for a job to complete
def _wait_for_job_completion(job_id: str, timeout_seconds: int, poll_interval_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while True:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
        if job["status"] == "succeeded":
            return
        if job["status"] == "failed":
            raise HTTPException(status_code=500, detail=job["error"] or "EMBEDDING_JOB_FAILED")
        if time.time() > deadline:
            raise HTTPException(status_code=504, detail="EMBEDDING_JOB_TIMEOUT")
        time.sleep(poll_interval_seconds)

# predict go from sequences endpoint to predict the GO terms for a list of sequences
@app.post(API_PREFIX + "/predict-go-from-sequences", response_model=PredictGoResponse)
def predict_go_from_sequences(request: PredictGoFromSequencesRequest) -> PredictGoResponse:
    _observe_sequence_lengths(
        backend=request.backend,
        sequences=[seq.sequence for seq in request.sequences],
    )
    job_payload = {
        "stage": "test",
        "backend": request.backend,
        "pooling": request.pooling,
        "batch_size": request.batch_size,
        "max_length": request.max_length,
        "sequences": [seq.model_dump() for seq in request.sequences],
    }
    return _predict_go_from_job_payload(
        job_payload,
        top_k=request.top_k,
        indices=request.indices,
        fail_fast=request.fail_fast,
        timeout_seconds=request.timeout_seconds,
        poll_interval_seconds=request.poll_interval_seconds,
    )


@app.post(API_PREFIX + "/predict-go-from-fasta", response_model=PredictGoResponse)
async def predict_go_from_fasta(
    fasta_file: UploadFile = File(...),
    backend: Literal["esm2", "protbert", "t5"] = Form(default="esm2"),
    pooling: Literal["mean", "cls"] = Form(default="mean"),
    batch_size: int = Form(default=8),
    max_length: int = Form(default=1280),
    top_k: int = Form(default=10),
    fail_fast: bool = Form(default=True),
    timeout_seconds: int = Form(default=1800),
    poll_interval_seconds: float = Form(default=1.0),
) -> PredictGoResponse:
    _validate_predict_form_params(
        batch_size=batch_size,
        max_length=max_length,
        top_k=top_k,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    fasta_text = await _read_fasta_upload(fasta_file)
    _parse_and_validate_fasta(fasta_text, backend)

    job_payload = {
        "stage": "test",
        "backend": backend,
        "pooling": pooling,
        "batch_size": batch_size,
        "max_length": max_length,
        "fasta_text": fasta_text,
    }
    return _predict_go_from_job_payload(
        job_payload,
        top_k=top_k,
        indices=None,
        fail_fast=fail_fast,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )