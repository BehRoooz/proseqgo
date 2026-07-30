from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest, CollectorRegistry

from config import (
    API_PREFIX,
    ARTIFACT_ROOT,
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_LENGTH,
    GO_PREDICTION_API_URL,
    JOBS_DATABASE_URL,
    MAX_FASTA_UPLOAD_BYTES,
    MAX_FASTA_UPLOAD_MB,
    MAX_SEQUENCE_LENGTH_AA,
    MAX_SEQUENCES_PER_REQUEST,
    SYNC_PREDICT_POLL_INTERVAL_SEC,
    SYNC_PREDICT_TIMEOUT_SEC,
)
from job_store import JobStore
from queueing import enqueue_embedding_job, get_queue
from schemas import (
    CreateJobRequest,
    CreateJobResponse,
    JobStatusResponse,
    PredictGoFromSequencesRequest,
    PredictGoRequest,
    PredictGoResponse,
)
from src.utils import get_device_info
from worker import parse_fasta_text, sync_queue_gauges

app = FastAPI(title="Embedding API", version="0.1.0")

registry = CollectorRegistry()

SERVICE_NAME = "embedding-api"

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total number of HTTP requests.",
    labelnames=("service", "route", "method", "status_code"),
    registry=registry,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=("service", "route", "method", "status_code"),
    registry=registry,
)
HTTP_IN_FLIGHT_REQUESTS = Gauge(
    "http_in_flight_requests",
    "Number of in-flight HTTP requests.",
    labelnames=("service",),
    registry=registry,
)
EMBEDDING_SEQUENCE_LENGTH = Histogram(
    "embedding_sequence_length",
    "Observed amino-acid sequence lengths partitioned by embedding backend.",
    labelnames=("backend",),
    buckets=(16, 32, 64, 128, 256, 512, 1024, 1280, 2048, 4096, 8192, float("inf")),
    registry=registry,
)
EMBEDDING_DIMENSION_MISMATCHES_TOTAL = Counter(
    "embedding_dimension_mismatch_total",
    "Total number of embedding dimension mismatches detected before GO inference.",
    registry=registry,
)
# Durable Postgres-backed queue depth (also updated on the worker process registry).
EMBEDDING_QUEUE_JOBS = Gauge(
    "embedding_queue_jobs",
    "Embedding jobs currently in each lifecycle state.",
    labelnames=("status",),
    registry=registry,
)
RQ_QUEUE_LENGTH = Gauge(
    "rq_queue_length",
    "Redis/RQ queue length for embedding jobs.",
    labelnames=("queue",),
    registry=registry,
)

store = JobStore(JOBS_DATABASE_URL)


def _observe_sequence_lengths(backend: str, sequences: list[str]) -> None:
    for sequence in sequences:
        EMBEDDING_SEQUENCE_LENGTH.labels(backend=backend).observe(len(sequence))


def _route_label(path: str) -> str:
    if path == "/metrics":
        return "/metrics"
    if path.startswith(API_PREFIX):
        return path[len(API_PREFIX) :] or "/"
    return path


def _refresh_job_metrics() -> None:
    for status in ("queued", "running", "succeeded", "failed"):
        EMBEDDING_QUEUE_JOBS.labels(status=status).set(store.count_jobs_by_status(status))
    try:
        queue = get_queue()
        RQ_QUEUE_LENGTH.labels(queue=queue.name).set(float(queue.count))
    except Exception:
        pass


def _create_and_enqueue(job_id: str, payload: dict) -> None:
    store.create_job(job_id, payload)
    enqueue_embedding_job(job_id)
    _refresh_job_metrics()


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


@app.on_event("startup")
def startup_event() -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    # Ensure schema exists; RQ worker is a separate container.
    JobStore(JOBS_DATABASE_URL)
    _refresh_job_metrics()
    sync_queue_gauges(store)


@app.get(API_PREFIX + "/health")
def health() -> dict[str, str | bool]:
    return {"status": "ok", **get_device_info()}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    _refresh_job_metrics()
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


def _enforce_sequence_envelope(sequences: list[str]) -> None:
    """Reject requests that exceed MVP count / AA-length caps from env."""
    if len(sequences) > MAX_SEQUENCES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"TOO_MANY_SEQUENCES: max {MAX_SEQUENCES_PER_REQUEST}, "
                f"got {len(sequences)}"
            ),
        )
    for index, sequence in enumerate(sequences):
        aa_len = len("".join(sequence.split()))
        if aa_len > MAX_SEQUENCE_LENGTH_AA:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"SEQUENCE_TOO_LONG: index={index} length={aa_len} "
                    f"max={MAX_SEQUENCE_LENGTH_AA}"
                ),
            )


@app.post(API_PREFIX + "/jobs", response_model=CreateJobResponse, status_code=202)
def create_job(request: CreateJobRequest) -> CreateJobResponse:
    sequences = [seq.sequence for seq in request.sequences]
    _enforce_sequence_envelope(sequences)
    _observe_sequence_lengths(backend=request.backend, sequences=sequences)
    job_id = str(uuid.uuid4())
    _create_and_enqueue(job_id, request.model_dump())
    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        poll_url=f"{API_PREFIX}/jobs/{job_id}",
    )


@app.post(API_PREFIX + "/jobs/fasta", response_model=CreateJobResponse, status_code=202)
async def create_fasta_job(
    fasta_file: UploadFile = File(...),
    backend: Literal["esm2", "protbert", "t5"] = Form(default="esm2"),
    pooling: Literal["mean", "cls"] = Form(default="mean"),
    batch_size: int = Form(default=DEFAULT_BATCH_SIZE),
    max_length: int = Form(default=DEFAULT_MAX_LENGTH),
) -> CreateJobResponse:
    fasta_text = await _read_fasta_upload(fasta_file)
    try:
        _, sequences = parse_fasta_text(fasta_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _enforce_sequence_envelope(sequences)
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
    _create_and_enqueue(job_id, payload)
    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        poll_url=f"{API_PREFIX}/jobs/{job_id}",
    )


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


def _post_go_predict(embedding: list[float], top_k: int) -> dict:
    endpoint = f"{GO_PREDICTION_API_URL.rstrip('/')}/predict"
    payload = json.dumps({"embedding": embedding, "top_k": top_k}).encode("utf-8")
    req = urlrequest.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=502,
            detail=f"GO_API_HTTP_{exc.code}: {err_body}",
        ) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"GO_API_UNREACHABLE: {exc.reason}",
        ) from exc


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
    _enforce_sequence_envelope(sequences)
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
    if not 5 <= timeout_seconds <= SYNC_PREDICT_TIMEOUT_SEC:
        raise HTTPException(
            status_code=400,
            detail=(
                f"timeout_seconds must be between 5 and {SYNC_PREDICT_TIMEOUT_SEC}"
            ),
        )
    if not 0.1 < poll_interval_seconds <= 5.0:
        raise HTTPException(
            status_code=400,
            detail="poll_interval_seconds must be greater than 0.1 and at most 5.0",
        )


async def _read_fasta_upload(fasta_file: UploadFile) -> str:
    raw = await fasta_file.read(MAX_FASTA_UPLOAD_BYTES + 1)
    if len(raw) > MAX_FASTA_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"FASTA_FILE_TOO_LARGE: max {MAX_FASTA_UPLOAD_MB} MB",
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
    _create_and_enqueue(job_id, job_payload)
    _wait_for_job_completion(
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    return _predict_go_for_job(
        job_id=job_id,
        request=PredictGoRequest(top_k=top_k, indices=indices, fail_fast=fail_fast),
    )


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


@app.post(API_PREFIX + "/predict-go-from-sequences", response_model=PredictGoResponse)
def predict_go_from_sequences(request: PredictGoFromSequencesRequest) -> PredictGoResponse:
    sequences = [seq.sequence for seq in request.sequences]
    _enforce_sequence_envelope(sequences)
    if request.timeout_seconds > SYNC_PREDICT_TIMEOUT_SEC:
        raise HTTPException(
            status_code=400,
            detail=f"timeout_seconds must be <= {SYNC_PREDICT_TIMEOUT_SEC}",
        )
    _observe_sequence_lengths(backend=request.backend, sequences=sequences)
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
    batch_size: int = Form(default=DEFAULT_BATCH_SIZE),
    max_length: int = Form(default=DEFAULT_MAX_LENGTH),
    top_k: int = Form(default=10),
    fail_fast: bool = Form(default=True),
    timeout_seconds: int = Form(default=SYNC_PREDICT_TIMEOUT_SEC),
    poll_interval_seconds: float = Form(default=SYNC_PREDICT_POLL_INTERVAL_SEC),
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
