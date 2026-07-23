from __future__ import annotations

from redis import Redis
from rq import Queue, Retry

from config import JOB_TIMEOUT_SEC, REDIS_URL, RQ_QUEUE_NAME, RQ_RETRY_INTERVALS, RQ_RETRY_MAX
from worker import handle_job_failure, process_job


def get_redis() -> Redis:
    return Redis.from_url(REDIS_URL)


def get_queue() -> Queue:
    return Queue(RQ_QUEUE_NAME, connection=get_redis())


def enqueue_training_job(job_id: str):
    """Enqueue durable job_id for an RQ worker."""
    queue = get_queue()
    return queue.enqueue(
        process_job,
        job_id,
        job_timeout=JOB_TIMEOUT_SEC,
        retry=Retry(max=RQ_RETRY_MAX, interval=RQ_RETRY_INTERVALS),
        on_failure=handle_job_failure,
        meta={"job_id": job_id},
        failure_ttl=86400,
        result_ttl=86400,
    )
