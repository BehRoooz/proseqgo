#!/usr/bin/env python3
"""RQ worker entrypoint for embedding jobs.

Starts a Prometheus metrics HTTP server, requeues orphaned started jobs
(from a previous SIGKILL), then runs an RQ worker with graceful SIGTERM.
"""
from __future__ import annotations

import logging
import sys

from prometheus_client import start_http_server
from redis import Redis
from rq import Queue, Worker
from rq.job import Job
from rq.registry import StartedJobRegistry

from config import JOBS_DATABASE_URL, REDIS_URL, RQ_QUEUE_NAME, WORKER_METRICS_PORT
from job_store import JobStore
from queueing import enqueue_embedding_job
from worker import sync_queue_gauges

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("embedding-rq-worker")


def requeue_orphaned_started_jobs(queue: Queue) -> int:
    """Recover jobs left in StartedJobRegistry after a hard worker kill.

    RQ's Job.requeue() only works for FailedJobRegistry entries, so we delete the
    orphaned RQ job and enqueue a fresh one for the same durable Postgres job_id.
    """
    registry = StartedJobRegistry(queue=queue)
    requeued = 0
    store = JobStore(JOBS_DATABASE_URL)
    seen_durable: set[str] = set()

    for rq_job_id in list(registry.get_job_ids()):
        try:
            job = Job.fetch(rq_job_id, connection=queue.connection)
        except Exception as exc:
            logger.warning("Could not fetch started job %s: %s", rq_job_id, exc)
            try:
                registry.remove(rq_job_id)
            except Exception:
                pass
            continue

        durable_id = None
        if job.args:
            durable_id = str(job.args[0])
        elif isinstance(job.meta, dict) and job.meta.get("job_id"):
            durable_id = str(job.meta["job_id"])

        logger.warning(
            "Recovering orphaned started RQ job %s (durable_id=%s)",
            rq_job_id,
            durable_id,
        )
        try:
            registry.remove(job)
        except Exception:
            pass
        try:
            job.delete()
        except Exception:
            pass

        if not durable_id or durable_id in seen_durable:
            continue
        seen_durable.add(durable_id)

        current = store.get_job(durable_id)
        if current is None:
            continue
        if current["status"] in ("succeeded", "failed"):
            continue

        store.reset_to_queued(durable_id)
        enqueue_embedding_job(durable_id)
        requeued += 1

    if requeued:
        sync_queue_gauges(store)
    return requeued


def main() -> int:
    start_http_server(WORKER_METRICS_PORT)
    logger.info("Worker metrics listening on :%s", WORKER_METRICS_PORT)

    JobStore(JOBS_DATABASE_URL)
    sync_queue_gauges()

    redis = Redis.from_url(REDIS_URL)
    queue = Queue(RQ_QUEUE_NAME, connection=redis)
    n = requeue_orphaned_started_jobs(queue)
    logger.info("Requeued %s orphaned started job(s) on %s", n, RQ_QUEUE_NAME)

    worker = Worker([queue], connection=redis, name="embedding-worker")
    worker.work(with_scheduler=True, logging_level="INFO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
