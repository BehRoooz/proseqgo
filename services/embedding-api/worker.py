from __future__ import annotations

import time
from typing import Any

from artifacts import save_test_artifacts
from config import JOBS_DATABASE_URL
from embedder import embed_sequence_batch
from exceptions import PermanentJobError, TransientJobError
from job_store import JobStore
from prometheus_client import Counter, Gauge, Histogram

EMBEDDING_JOBS_TOTAL = Counter(
    "cafa5_embedding_jobs_total",
    "Total embedding jobs partitioned by terminal status and backend.",
    labelnames=("status", "backend"),
)
EMBEDDING_QUEUE_JOBS = Gauge(
    "cafa5_embedding_queue_jobs",
    "Embedding jobs currently in each lifecycle state.",
    labelnames=("status",),
)
EMBEDDING_JOB_DURATION_SECONDS = Histogram(
    "cafa5_embedding_job_duration_seconds",
    "Embedding job duration in seconds by terminal status and backend.",
    labelnames=("status", "backend"),
)
EMBEDDING_SEQUENCES_PROCESSED_TOTAL = Counter(
    "cafa5_embedding_sequences_processed_total",
    "Number of embedded sequences processed by backend.",
    labelnames=("backend",),
)
EMBEDDING_ARTIFACT_BYTES = Histogram(
    "cafa5_embedding_artifact_bytes",
    "Embedding artifact output size in bytes partitioned by artifact name.",
    labelnames=("artifact_name",),
    buckets=(1024, 10 * 1024, 100 * 1024, 1024**2, 5 * 1024**2, 10 * 1024**2, float("inf")),
)


def parse_fasta_text(fasta_text: str) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    sequences: list[str] = []
    current_id: str | None = None
    current_seq: list[str] = []

    for raw_line in fasta_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id is not None:
                ids.append(current_id)
                sequences.append("".join(current_seq))
            current_id = line[1:].split()[0]
            current_seq = []
        else:
            current_seq.append(line)

    if current_id is not None:
        ids.append(current_id)
        sequences.append("".join(current_seq))

    if not ids:
        raise ValueError("No FASTA records found in input.")
    return ids, sequences


def _extract_ids_sequences(request: dict[str, Any]) -> tuple[list[str], list[str]]:
    if "sequences" in request and request["sequences"]:
        ids = [item["id"] for item in request["sequences"]]
        sequences = [item["sequence"] for item in request["sequences"]]
        return ids, sequences

    if "fasta_text" in request and request["fasta_text"]:
        return parse_fasta_text(request["fasta_text"])

    raise ValueError("Request must include either `sequences` or `fasta_text`.")


def _store() -> JobStore:
    return JobStore(JOBS_DATABASE_URL)


def sync_queue_gauges(store: JobStore | None = None) -> None:
    store = store or _store()
    for status in ("queued", "running", "succeeded", "failed"):
        EMBEDDING_QUEUE_JOBS.labels(status=status).set(store.count_jobs_by_status(status))


def handle_job_failure(job, connection, typ, value, traceback) -> None:  # noqa: ANN001, ARG001
    """RQ failure callback after retries are exhausted (or non-retryable hard failure)."""
    job_id = None
    if job is not None:
        if job.args:
            job_id = job.args[0]
        elif isinstance(job.meta, dict):
            job_id = job.meta.get("job_id")
    if not job_id:
        return

    store = _store()
    current = store.get_job(str(job_id))
    if current is None:
        return
    if current["status"] in ("succeeded", "failed"):
        return

    store.mark_failed(
        str(job_id),
        {
            "code": "RQ_JOB_FAILED",
            "message": str(value) if value is not None else "RQ job failed after retries",
            "exception_type": getattr(typ, "__name__", str(typ)),
        },
    )
    sync_queue_gauges(store)


def process_job(job_id: str) -> None:
    """RQ entrypoint: load job from Postgres, embed, write artifacts + durable status.

    Permanent failures mark Postgres failed and return (no RQ retry).
    Transient failures raise TransientJobError so RQ Retry can requeue.
    """
    store = _store()
    job = store.get_job(job_id)
    if job is None:
        # Nothing to persist; fail permanently for RQ bookkeeping.
        raise PermanentJobError(f"JOB_NOT_FOUND: {job_id}")

    backend = str(job["request"].get("backend", "esm2"))
    store.mark_running(job_id)
    sync_queue_gauges(store)
    started = time.perf_counter()

    # Optional delay for crash-recovery tests (leave unset in production).
    delay = float(__import__("os").environ.get("EMBEDDING_JOB_START_DELAY_SEC", "0") or 0)
    if delay > 0:
        time.sleep(delay)

    try:
        req = job["request"]
        try:
            ids, sequences = _extract_ids_sequences(req)
        except ValueError as exc:
            store.mark_failed(
                job_id,
                {"code": "EMBEDDING_INVALID_REQUEST", "message": str(exc)},
            )
            EMBEDDING_JOBS_TOTAL.labels(status="failed", backend=backend).inc()
            EMBEDDING_JOB_DURATION_SECONDS.labels(status="failed", backend=backend).observe(
                time.perf_counter() - started
            )
            sync_queue_gauges(store)
            return

        total = len(ids)
        store.update_progress(job_id, embedded=0, total=total)

        try:
            ids_out, embeds = embed_sequence_batch(
                ids,
                sequences,
                backend=req.get("backend", "esm2"),
                pooling=req.get("pooling", "mean"),
                max_length=int(req.get("max_length", 1280)),
                batch_size=int(req.get("batch_size", 8)),
            )
        except Exception as exc:
            raise TransientJobError(str(exc)) from exc

        store.update_progress(job_id, embedded=total, total=total)
        EMBEDDING_SEQUENCES_PROCESSED_TOTAL.labels(backend=backend).inc(total)

        artifacts = save_test_artifacts(job_id, ids_out, embeds)
        for art in artifacts:
            store.insert_artifact(
                job_id=job_id,
                name=art["name"],
                path=art["path"],
                dtype=art["dtype"],
                shape=art["shape"],
                size_bytes=art["size_bytes"],
            )
            EMBEDDING_ARTIFACT_BYTES.labels(artifact_name=art["name"]).observe(float(art["size_bytes"]))

        store.mark_succeeded(job_id)
        duration = time.perf_counter() - started
        EMBEDDING_JOBS_TOTAL.labels(status="succeeded", backend=backend).inc()
        EMBEDDING_JOB_DURATION_SECONDS.labels(status="succeeded", backend=backend).observe(duration)
        sync_queue_gauges(store)
    except TransientJobError:
        sync_queue_gauges(store)
        raise
    except PermanentJobError:
        raise
    except Exception as exc:
        store.mark_failed(
            job_id,
            {"code": "EMBEDDING_RUNTIME_FAILURE", "message": str(exc)},
        )
        duration = time.perf_counter() - started
        EMBEDDING_JOBS_TOTAL.labels(status="failed", backend=backend).inc()
        EMBEDDING_JOB_DURATION_SECONDS.labels(status="failed", backend=backend).observe(duration)
        sync_queue_gauges(store)
        return
