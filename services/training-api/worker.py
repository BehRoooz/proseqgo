from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from config import JOBS_DATABASE_URL, MLFLOW_EXTERNAL_UI_BASE, MLFLOW_TRACKING_URI
from exceptions import PermanentJobError, TransientJobError
from job_store import JobStore
from prometheus_client import Counter, Gauge, Histogram

APP_ROOT = Path(__file__).resolve().parents[2]
TRAINING_JOBS_TOTAL = Counter(
    "training_jobs_total",
    "Total training jobs partitioned by terminal status and mode.",
    labelnames=("status", "mode"),
)
TRAINING_QUEUE_JOBS = Gauge(
    "training_queue_jobs",
    "Training jobs currently in each lifecycle state.",
    labelnames=("status",),
)
TRAINING_JOB_DURATION_SECONDS = Histogram(
    "training_job_duration_seconds",
    "Training job duration in seconds by terminal status and mode.",
    labelnames=("status", "mode"),
)
TRAINING_SUBPROCESS_FAILURES_TOTAL = Counter(
    "training_subprocess_failures_total",
    "Training subprocess failures partitioned by failure reason.",
    labelnames=("reason",),
)


def _store() -> JobStore:
    return JobStore(JOBS_DATABASE_URL)


def sync_queue_gauges(store: JobStore | None = None) -> None:
    store = store or _store()
    for status in ("queued", "running", "succeeded", "failed"):
        TRAINING_QUEUE_JOBS.labels(status=status).set(store.count_jobs_by_status(status))


def _build_mlflow_links(train_run_id: str | None, summary: dict[str, Any]) -> dict[str, Any] | None:
    if not train_run_id:
        return None
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", MLFLOW_TRACKING_URI)
    external_base = (os.environ.get("MLFLOW_EXTERNAL_UI_BASE") or MLFLOW_EXTERNAL_UI_BASE).rstrip("/")
    registered_name = summary.get("registered_model_name")
    registered_ver = summary.get("registered_model_version")
    out: dict[str, Any] = {
        "tracking_uri": tracking_uri,
        "train_run_id": train_run_id,
        "experiment_id": None,
        "run_ui_url": None,
        "registered_model_name": registered_name,
        "registered_model_version": registered_ver,
        "model_registry_ui_url": None,
    }
    try:
        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=tracking_uri)
        run = client.get_run(train_run_id)
        exp_id = run.info.experiment_id
        out["experiment_id"] = str(exp_id)
        out["run_ui_url"] = f"{external_base}/#/experiments/{exp_id}/runs/{train_run_id}"
        if registered_name and registered_ver:
            out["model_registry_ui_url"] = (
                f"{external_base}/#/models/{registered_name}/versions/{registered_ver}"
            )
    except Exception:
        pass
    return out


def _run_training_subprocess(request: dict[str, Any]) -> tuple[int, str, str]:
    config_path = request.get("config") or "configs/config.yaml"
    mode = request.get("mode") or "train"
    if mode == "retrain":
        cmd = [
            os.environ.get("PYTHON_EXECUTABLE", "python"),
            str(APP_ROOT / "scripts" / "retrain_pipeline.py"),
            "--config",
            config_path,
        ]
    else:
        cmd = [
            os.environ.get("PYTHON_EXECUTABLE", "python"),
            str(APP_ROOT / "scripts" / "train.py"),
            "--config",
            config_path,
        ]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(APP_ROOT))
    proc = subprocess.run(
        cmd,
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=None,
    )
    return proc.returncode, proc.stdout, proc.stderr


def handle_job_failure(job, connection, typ, value, traceback) -> None:  # noqa: ANN001, ARG001
    """RQ failure callback after retries are exhausted."""
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
    """RQ entrypoint: run train/retrain subprocess and persist durable status/result.

    Permanent failures mark Postgres failed and return (no RQ retry).
    Transient failures raise TransientJobError so RQ Retry can requeue.
    """
    store = _store()
    job = store.get_job(job_id)
    if job is None:
        raise PermanentJobError(f"JOB_NOT_FOUND: {job_id}")

    req = job["request"]
    mode = str(req.get("mode", "train"))
    store.mark_running(job_id)
    store.update_progress(job_id, percent=None, message="running")
    sync_queue_gauges(store)
    started = time.perf_counter()

    try:
        store.update_progress(job_id, percent=1.0, message="starting training subprocess")
        try:
            code, stdout, stderr = _run_training_subprocess(req)
        except Exception as exc:
            raise TransientJobError(str(exc)) from exc

        summary_path = APP_ROOT / "outputs" / "train_run_summary.json"
        if code != 0:
            err_text = (stderr or "").strip() or (stdout or "").strip() or f"exit code {code}"
            err_excerpt = err_text if len(err_text) <= 8000 else (
                err_text[:3500] + "\n\n...[truncated middle]...\n\n" + err_text[-3500:]
            )
            signal_hint = None
            if code < 0:
                signal_hint = f"terminated_by_signal_{abs(code)}"
            store.mark_failed(
                job_id,
                {
                    "code": "TRAINING_SUBPROCESS_FAILED",
                    "message": err_excerpt,
                    "exit_code": code,
                    "signal_hint": signal_hint,
                    "stderr_len": len(stderr or ""),
                    "stdout_len": len(stdout or ""),
                },
            )
            TRAINING_SUBPROCESS_FAILURES_TOTAL.labels(reason="training_subprocess_failed").inc()
            TRAINING_JOBS_TOTAL.labels(status="failed", mode=mode).inc()
            TRAINING_JOB_DURATION_SECONDS.labels(status="failed", mode=mode).observe(
                time.perf_counter() - started
            )
            sync_queue_gauges(store)
            return

        if not summary_path.exists():
            store.mark_failed(
                job_id,
                {
                    "code": "TRAIN_SUMMARY_MISSING",
                    "message": "train.py reported success but outputs/train_run_summary.json not found",
                },
            )
            TRAINING_SUBPROCESS_FAILURES_TOTAL.labels(reason="train_summary_missing").inc()
            TRAINING_JOBS_TOTAL.labels(status="failed", mode=mode).inc()
            TRAINING_JOB_DURATION_SECONDS.labels(status="failed", mode=mode).observe(
                time.perf_counter() - started
            )
            sync_queue_gauges(store)
            return

        try:
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError as exc:
            store.mark_failed(
                job_id,
                {"code": "TRAIN_SUMMARY_INVALID", "message": str(exc)},
            )
            TRAINING_SUBPROCESS_FAILURES_TOTAL.labels(reason="train_summary_invalid").inc()
            TRAINING_JOBS_TOTAL.labels(status="failed", mode=mode).inc()
            TRAINING_JOB_DURATION_SECONDS.labels(status="failed", mode=mode).observe(
                time.perf_counter() - started
            )
            sync_queue_gauges(store)
            return

        train_run_id = summary.get("train_run_id")
        store.update_progress(job_id, percent=100.0, message="completed")
        mlflow_payload = _build_mlflow_links(str(train_run_id) if train_run_id else None, summary)
        result: dict[str, Any] = {
            "train_run_id": train_run_id,
            "registered_model_name": summary.get("registered_model_name"),
            "registered_model_version": summary.get("registered_model_version"),
            "model_uri": summary.get("model_uri"),
            "mlflow": mlflow_payload,
        }
        store.mark_succeeded(job_id, result)
        TRAINING_JOBS_TOTAL.labels(status="succeeded", mode=mode).inc()
        TRAINING_JOB_DURATION_SECONDS.labels(status="succeeded", mode=mode).observe(
            time.perf_counter() - started
        )
        sync_queue_gauges(store)
    except TransientJobError:
        sync_queue_gauges(store)
        raise
    except PermanentJobError:
        raise
    except Exception as exc:
        store.mark_failed(
            job_id,
            {"code": "TRAINING_RUNTIME_FAILURE", "message": str(exc)},
        )
        TRAINING_SUBPROCESS_FAILURES_TOTAL.labels(reason="training_runtime_failure").inc()
        TRAINING_JOBS_TOTAL.labels(status="failed", mode=mode).inc()
        TRAINING_JOB_DURATION_SECONDS.labels(status="failed", mode=mode).observe(
            time.perf_counter() - started
        )
        sync_queue_gauges(store)
        return
