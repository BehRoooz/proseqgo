from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Postgres-backed durable job history for the embedding API."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._init_db()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _init_db(self) -> None:
        # API and worker may start concurrently; ignore duplicate-create races.
        for attempt in range(5):
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS embedding_jobs (
                            job_id TEXT PRIMARY KEY,
                            status TEXT NOT NULL,
                            request_json TEXT NOT NULL,
                            progress_json TEXT NOT NULL,
                            error_json TEXT,
                            created_at TEXT NOT NULL,
                            started_at TEXT,
                            finished_at TEXT
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS embedding_artifacts (
                            id BIGSERIAL PRIMARY KEY,
                            job_id TEXT NOT NULL,
                            name TEXT NOT NULL,
                            path TEXT NOT NULL,
                            dtype TEXT NOT NULL,
                            shape_json TEXT NOT NULL,
                            size_bytes INTEGER NOT NULL
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_embedding_jobs_status_created
                        ON embedding_jobs (status, created_at)
                        """
                    )
                    conn.commit()
                return
            except psycopg.errors.UniqueViolation:
                if attempt == 4:
                    raise
                continue

    def create_job(self, job_id: str, request_json: dict[str, Any]) -> None:
        progress = {"embedded_sequences": 0, "total_sequences": 0, "percent": 0.0}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO embedding_jobs (
                    job_id, status, request_json, progress_json, error_json,
                    created_at, started_at, finished_at
                )
                VALUES (%s, 'queued', %s, %s, NULL, %s, NULL, NULL)
                """,
                (job_id, json.dumps(request_json), json.dumps(progress), utc_now()),
            )
            conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM embedding_jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def count_jobs_by_status(self, status: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM embedding_jobs WHERE status = %s",
                (status,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def mark_running(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE embedding_jobs
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
                UPDATE embedding_jobs
                SET status = 'queued', started_at = NULL, finished_at = NULL
                WHERE job_id = %s AND status = 'running'
                """,
                (job_id,),
            )
            conn.commit()

    def update_progress(self, job_id: str, embedded: int, total: int) -> None:
        percent = 0.0 if total == 0 else (100.0 * embedded / total)
        progress = {
            "embedded_sequences": int(embedded),
            "total_sequences": int(total),
            "percent": round(percent, 2),
        }
        with self._connect() as conn:
            conn.execute(
                "UPDATE embedding_jobs SET progress_json = %s WHERE job_id = %s",
                (json.dumps(progress), job_id),
            )
            conn.commit()

    def mark_succeeded(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE embedding_jobs
                SET status = 'succeeded', finished_at = %s, error_json = NULL
                WHERE job_id = %s
                """,
                (utc_now(), job_id),
            )
            conn.commit()

    def mark_failed(self, job_id: str, error_json: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE embedding_jobs
                SET status = 'failed', error_json = %s, finished_at = %s
                WHERE job_id = %s
                """,
                (json.dumps(error_json), utc_now(), job_id),
            )
            conn.commit()

    def insert_artifact(
        self,
        job_id: str,
        name: str,
        path: str,
        dtype: str,
        shape: list[int],
        size_bytes: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO embedding_artifacts (job_id, name, path, dtype, shape_json, size_bytes)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (job_id, name, path, dtype, json.dumps(shape), size_bytes),
            )
            conn.commit()

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, name, path, dtype, shape_json, size_bytes
                FROM embedding_artifacts
                WHERE job_id = %s
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "name": row["name"],
                "path": row["path"],
                "dtype": row["dtype"],
                "shape": json.loads(row["shape_json"]),
                "size_bytes": row["size_bytes"],
            }
            for row in rows
        ]

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "progress": json.loads(row["progress_json"]),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
