from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Postgres-backed durable job history for the training API."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._init_db()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _init_db(self) -> None:
        for attempt in range(5):
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS training_jobs (
                            job_id TEXT PRIMARY KEY,
                            status TEXT NOT NULL,
                            request_json TEXT NOT NULL,
                            progress_json TEXT NOT NULL,
                            error_json TEXT,
                            result_json TEXT,
                            created_at TEXT NOT NULL,
                            started_at TEXT,
                            finished_at TEXT
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_training_jobs_status_created
                        ON training_jobs (status, created_at)
                        """
                    )
                    conn.commit()
                return
            except psycopg.errors.UniqueViolation:
                if attempt == 4:
                    raise
                continue

    def create_job(self, job_id: str, request_json: dict[str, Any]) -> None:
        progress = {"percent": 0.0, "message": "queued"}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO training_jobs (
                    job_id, status, request_json, progress_json, error_json, result_json,
                    created_at, started_at, finished_at
                )
                VALUES (%s, 'queued', %s, %s, NULL, NULL, %s, NULL, NULL)
                """,
                (job_id, json.dumps(request_json), json.dumps(progress), utc_now()),
            )
            conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM training_jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def count_jobs_by_status(self, status: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM training_jobs WHERE status = %s",
                (status,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def mark_running(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE training_jobs
                SET status = 'running', started_at = %s, finished_at = NULL, error_json = NULL
                WHERE job_id = %s
                """,
                (utc_now(), job_id),
            )
            conn.commit()

    def reset_to_queued(self, job_id: str) -> None:
        """Return an orphaned running job to queued after worker crash recovery."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE training_jobs
                SET status = 'queued', started_at = NULL, finished_at = NULL
                WHERE job_id = %s AND status = 'running'
                """,
                (job_id,),
            )
            conn.commit()

    def update_progress(self, job_id: str, *, percent: float | None, message: str) -> None:
        progress: dict[str, Any] = {"message": message}
        if percent is not None:
            progress["percent"] = round(float(percent), 2)
        else:
            progress["percent"] = None
        with self._connect() as conn:
            conn.execute(
                "UPDATE training_jobs SET progress_json = %s WHERE job_id = %s",
                (json.dumps(progress), job_id),
            )
            conn.commit()

    def mark_succeeded(self, job_id: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE training_jobs
                SET status = 'succeeded', finished_at = %s, error_json = NULL, result_json = %s
                WHERE job_id = %s
                """,
                (utc_now(), json.dumps(result), job_id),
            )
            conn.commit()

    def mark_failed(self, job_id: str, error_json: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE training_jobs
                SET status = 'failed', error_json = %s, finished_at = %s
                WHERE job_id = %s
                """,
                (json.dumps(error_json), utc_now(), job_id),
            )
            conn.commit()

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "progress": json.loads(row["progress_json"]),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
