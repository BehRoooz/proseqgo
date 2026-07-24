"""Tests for API request schema edge cases (Pydantic)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_create_job_request_valid(embedding_schemas) -> None:
    req = embedding_schemas.CreateJobRequest(
        sequences=[{"id": "p1", "sequence": "MKTAY"}],
    )
    assert req.backend == "esm2"
    assert req.batch_size == 8
    assert len(req.sequences) == 1


def test_create_job_request_rejects_empty_sequences(embedding_schemas) -> None:
    with pytest.raises(ValidationError):
        embedding_schemas.CreateJobRequest(sequences=[])


def test_create_job_request_rejects_empty_sequence_id(embedding_schemas) -> None:
    with pytest.raises(ValidationError):
        embedding_schemas.CreateJobRequest(
            sequences=[{"id": "", "sequence": "ACDE"}],
        )


def test_create_job_request_batch_size_bounds(embedding_schemas) -> None:
    with pytest.raises(ValidationError):
        embedding_schemas.CreateJobRequest(
            batch_size=0,
            sequences=[{"id": "p1", "sequence": "ACDE"}],
        )
    with pytest.raises(ValidationError):
        embedding_schemas.CreateJobRequest(
            batch_size=129,
            sequences=[{"id": "p1", "sequence": "ACDE"}],
        )


def test_predict_go_from_sequences_timeout_bounds(embedding_schemas) -> None:
    with pytest.raises(ValidationError):
        embedding_schemas.PredictGoFromSequencesRequest(
            sequences=[{"id": "p1", "sequence": "ACDE"}],
            timeout_seconds=1,
        )


def test_job_status_literal(embedding_schemas) -> None:
    ok = embedding_schemas.JobStatusResponse(
        job_id="j1",
        status="queued",
        stage="test",
        backend="esm2",
        progress={"embedded_sequences": 0, "total_sequences": 1, "percent": 0.0},
    )
    assert ok.status == "queued"
    with pytest.raises(ValidationError):
        embedding_schemas.JobStatusResponse(
            job_id="j1",
            status="cancelled",
            stage="test",
            backend="esm2",
            progress={},
        )


def test_go_predict_request_top_k(go_schemas) -> None:
    req = go_schemas.PredictRequest(embedding=[0.1] * 4, top_k=5)
    assert req.top_k == 5
    with pytest.raises(ValidationError):
        go_schemas.PredictRequest(embedding=[0.1], top_k=0)
