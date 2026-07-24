from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest, CollectorRegistry

from mlflow_logger import log_inference
from model_loader import load_model, load_model_from_registry, load_term_names
from predictor_service import predict_top_k
from schemas import HealthResponse, PredictRequest, PredictResponse
from src.utils import get_device_info, get_device_name

# root path for the app
APP_ROOT = Path(__file__).resolve().parents[2]

CHECKPOINT_PATH = APP_ROOT / "outputs" / "checkpoints" / "best_model.pt"
# model URI for the model
MODEL_URI = os.getenv("MODEL_URI", "models:/cafa-go-model@champion")
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "/tmp/mlflow-cache")
TERM_NAMES_PATH = APP_ROOT / "outputs" / "label_matrix_top500" / "term_names.npy"
META_PATH = APP_ROOT / "outputs" / "splits" / "model_meta.json"

app = FastAPI(title="ProSeqGO Inference API")

# Prometheus metrics for the GO prediction API
registry = CollectorRegistry() # to store metrics

# service name for metrics
SERVICE_NAME = "go-prediction-api"

# metrics for HTTP requests
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
INFERENCE_REQUESTS_TOTAL = Counter(
    "inference_requests_total",
    "Total inference requests partitioned by model version and status code.",
    labelnames=("model_version", "status_code"),
    registry=registry,
)
INFERENCE_DURATION_SECONDS = Histogram(
    "inference_duration_seconds",
    "Inference runtime in seconds partitioned by model version.",
    labelnames=("model_version",),
    registry=registry,
)
INFERENCE_INPUT_VALIDATION_FAILURES_TOTAL = Counter(
    "inference_input_validation_failures_total",
    "Total inference input validation failures partitioned by reason.",
    labelnames=("reason",),
    registry=registry,
)
INFERENCE_TOP_K_REQUESTS_TOTAL = Counter(
    "inference_top_k_requests_total",
    "Distribution of requested top_k values.",
    labelnames=("top_k",),
    registry=registry,
)

MODEL = None
TERM_NAMES = None
MODEL_META = None
INFERENCE_DEVICE = get_device_name()


def _validation_reason(message: str) -> str:
    if "length" in message:
        return "embedding_dim_mismatch"
    if "1-dimensional" in message:
        return "embedding_shape_invalid"
    if "top_k" in message:
        return "top_k_invalid"
    return "invalid_input"


@app.middleware("http")
async def prometheus_http_middleware(request: Request, call_next):
    route = request.url.path
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
    global MODEL, TERM_NAMES, MODEL_META
    try:
        if MODEL_URI:
            MODEL, TERM_NAMES, MODEL_META = load_model_from_registry(
                MODEL_URI,
                device=INFERENCE_DEVICE,
                cache_dir=MODEL_CACHE_DIR,
            )
        else:
            MODEL, MODEL_META = load_model(CHECKPOINT_PATH, META_PATH, device=INFERENCE_DEVICE)
            TERM_NAMES = load_term_names(TERM_NAMES_PATH)
    except Exception:
        MODEL = None
        TERM_NAMES = None
        MODEL_META = None


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    device_info = get_device_info()
    return HealthResponse(
        status="ok",
        model_loaded=MODEL is not None and TERM_NAMES is not None and MODEL_META is not None,
        model_version=MODEL_META.get("model_version") if MODEL_META else None,
        device=str(device_info.get("device")),
        cuda_available=bool(device_info.get("cuda_available")),
        cafa_device=str(device_info.get("cafa_device")),
        cuda_device_name=(
            str(device_info["cuda_device_name"]) if "cuda_device_name" in device_info else None
        ),
    )


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest, raw_request: Request) -> PredictResponse:
    if MODEL is None or TERM_NAMES is None or MODEL_META is None:
        raise HTTPException(status_code=500, detail="model artifacts could not be loaded")

    model_version = str(MODEL_META.get("model_version", "unknown"))
    INFERENCE_TOP_K_REQUESTS_TOTAL.labels(top_k=str(request.top_k)).inc()
    status_code = "500"
    start = time.perf_counter()

    try:
        result = predict_top_k(
            model=MODEL,
            embedding=request.embedding,
            term_names=TERM_NAMES,
            top_k=request.top_k,
            apply_sigmoid=True,
            device=INFERENCE_DEVICE,
            expected_dim=int(MODEL_META.get("embedding_dim", 1280)),
        )
        status_code = "200"
    except ValueError as e:
        status_code = "400"
        INFERENCE_INPUT_VALIDATION_FAILURES_TOTAL.labels(reason=_validation_reason(str(e))).inc()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        status_code = "500"
        raise HTTPException(status_code=500, detail="inference failure") from e
    finally:
        duration = time.perf_counter() - start
        INFERENCE_REQUESTS_TOTAL.labels(model_version=model_version, status_code=status_code).inc()
        INFERENCE_DURATION_SECONDS.labels(model_version=model_version).observe(duration)

    runtime_ms = (time.perf_counter() - start) * 1000.0
    request_id = raw_request.headers.get("X-Request-ID")

    log_inference(
        model_version=model_version,
        top_k=result["top_k"],
        runtime_ms=runtime_ms,
        prediction_count=len(result["predictions"]),
        request_id=request_id,
    )

    return PredictResponse(
        model_version=model_version,
        top_k=result["top_k"],
        predictions=result["predictions"],
    )
