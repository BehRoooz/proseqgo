#!/usr/bin/env python
"""Smoke test: log a dummy MLflow run via the tracking server."""

from __future__ import annotations

import os
import sys
import tempfile

import mlflow


def main() -> int:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("smoke-test")

    with mlflow.start_run(run_name="smoke-postgres-minio") as run:
        mlflow.log_param("test", "ok")
        mlflow.log_metric("dummy", 1.0)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("artifact smoke test\n")
            artifact_path = handle.name
        mlflow.log_artifact(artifact_path)

    print(f"smoke test succeeded: run_id={run.info.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
