from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_LENGTH,
    MAX_SEQUENCES_PER_REQUEST,
    SYNC_PREDICT_POLL_INTERVAL_SEC,
    SYNC_PREDICT_TIMEOUT_SEC,
)


class SequenceItem(BaseModel):
    id: str = Field(min_length=1)
    sequence: str = Field(min_length=1)


class CreateJobRequest(BaseModel):
    stage: Literal["test"] = "test"
    backend: Literal["esm2", "protbert", "t5"] = "esm2"
    pooling: Literal["mean", "cls"] = "mean"
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=128)
    max_length: int = Field(default=DEFAULT_MAX_LENGTH, ge=8, le=8192)
    sequences: list[SequenceItem] = Field(min_length=1, max_length=MAX_SEQUENCES_PER_REQUEST)


class Progress(BaseModel):
    embedded_sequences: int = 0
    total_sequences: int = 0
    percent: float = 0.0


class ArtifactEntry(BaseModel):
    name: str
    path: str
    dtype: str
    shape: list[int]
    size_bytes: int


class CreateJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    poll_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    stage: str
    backend: str
    progress: Progress
    error: dict[str, Any] | None = None
    artifacts_manifest: list[ArtifactEntry] | None = None


class PredictGoRequest(BaseModel):
    top_k: int = Field(default=10, ge=1, le=500)
    indices: list[int] | None = None
    fail_fast: bool = True


class GoPredictionItem(BaseModel):
    go_term: str
    score: float


class GoPredictionResult(BaseModel):
    index: int
    sequence_id: str
    predictions: list[GoPredictionItem]


class PredictGoResponse(BaseModel):
    job_id: str
    status: Literal["succeeded"]
    model_version: str | None = None
    top_k: int
    results: list[GoPredictionResult]
    failures: list[dict[str, Any]] = Field(default_factory=list)


class PredictGoFromSequencesRequest(BaseModel):
    backend: Literal["esm2", "protbert", "t5"] = "esm2"
    pooling: Literal["mean", "cls"] = "mean"
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=128)
    max_length: int = Field(default=DEFAULT_MAX_LENGTH, ge=8, le=8192)
    sequences: list[SequenceItem] = Field(min_length=1, max_length=MAX_SEQUENCES_PER_REQUEST)
    top_k: int = Field(default=10, ge=1, le=500)
    indices: list[int] | None = None
    fail_fast: bool = True
    timeout_seconds: int = Field(
        default=SYNC_PREDICT_TIMEOUT_SEC,
        ge=5,
        le=SYNC_PREDICT_TIMEOUT_SEC,
    )
    poll_interval_seconds: float = Field(
        default=SYNC_PREDICT_POLL_INTERVAL_SEC,
        gt=0.1,
        le=5.0,
    )
