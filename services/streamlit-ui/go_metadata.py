from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import streamlit as st  # type: ignore

    _cache_data = st.cache_data
except Exception:  # pragma: no cover - Streamlit not required for helpers
    _cache_data = None


REPO_ROOT = Path(__file__).resolve().parents[2]

# Per-step requirement: this exact CSV is the source of truth.
DEFAULT_GO_TERM_METADATA_CSV = (
    REPO_ROOT
    / "services"
    / "streamlit-ui"
    / "data"
    / "go_term_metadata.csv"
)


def _left_join_predictions_with_metadata(
    predictions: list[dict[str, Any]],
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    if not predictions:
        # Return empty frame with stable columns to simplify UI code later.
        return pd.DataFrame(columns=["go_term", "score", "name", "namespace", "def"])

    pred_df = pd.DataFrame(predictions).copy()
    if "go_term" not in pred_df.columns:
        raise KeyError("Predictions payload must contain 'go_term' column.")
    if "score" not in pred_df.columns:
        raise KeyError("Predictions payload must contain 'score' column.")

    # Preserve row order for display and deterministic CSV export.
    pred_df["__row_id"] = range(len(pred_df))

    merged = pred_df.merge(metadata, on="go_term", how="left", validate="m:1")
    merged = merged.sort_values("__row_id").drop(columns=["__row_id"])
    return merged[["go_term", "score", "name", "namespace", "def"]]


def load_go_term_metadata(
    metadata_csv_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Load GO term metadata (go_term, name, namespace) for enrichment joins.

    The CSV is expected to be produced by `scripts/build_go_term_metadata.py`.
    """
    path = (
        Path(metadata_csv_path)
        if metadata_csv_path is not None
        else Path(os.getenv("GO_TERM_METADATA_CSV", str(DEFAULT_GO_TERM_METADATA_CSV)))
    )

    if not path.exists():
        raise FileNotFoundError(
            f"GO term metadata CSV not found: {path}\n"
            "Generate it with `python scripts/build_go_term_metadata.py` "
            "or mount it into the Streamlit container and set GO_TERM_METADATA_CSV."
        )

    df = pd.read_csv(path)

    # Normalize column names from build_go_term_metadata.py.
    if "GO_term" in df.columns and "go_term" not in df.columns:
        df = df.rename(columns={"GO_term": "go_term"})

    required = {"go_term", "name", "namespace", "def"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"GO term metadata CSV missing required columns {sorted(missing)}: {path}"
        )

    # Keep only needed columns and ensure consistent dtypes for merge.
    df = df.loc[:, ["go_term", "name", "namespace", "def"]].copy()
    df["go_term"] = df["go_term"].astype(str).str.strip()
    df["name"] = df["name"].astype(str)
    df["namespace"] = df["namespace"].astype(str)
    df["def"] = df["def"].astype(str)
    return df


# Optional caching wrapper to avoid re-reading the CSV on every rerun.
if _cache_data is not None:
    load_go_term_metadata = _cache_data(load_go_term_metadata)  # type: ignore[method-assign]
else:
    load_go_term_metadata = lru_cache(maxsize=1)(load_go_term_metadata)  # type: ignore[method-assign]


def enrich_prediction_rows(
    predictions: list[dict[str, Any]],
    *,
    metadata: pd.DataFrame | None = None,
    metadata_csv_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Enrich `{go_term, score}` prediction rows with `name`, `namespace`, and `def`.
    """
    meta = metadata if metadata is not None else load_go_term_metadata(metadata_csv_path)
    return _left_join_predictions_with_metadata(predictions=predictions, metadata=meta)


def enrich_predict_go_response(
    payload: dict[str, Any],
    *,
    metadata: pd.DataFrame | None = None,
    metadata_csv_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Enrich an embedding-api prediction response in-place (returns a shallow copy).

    Expected shape:
      {
        "results": [
          {"sequence_id": "...", "predictions": [{"go_term": "...", "score": ...}, ...]},
          ...
        ],
        "failures": [...]
      }
    """
    meta = metadata if metadata is not None else load_go_term_metadata(metadata_csv_path)
    out: dict[str, Any] = dict(payload)
    results = payload.get("results", [])
    enriched_results: list[dict[str, Any]] = []

    for result in results:
        predictions = result.get("predictions", []) or []
        enriched_df = _left_join_predictions_with_metadata(predictions, meta)
        enriched_predictions = enriched_df.to_dict(orient="records")

        enriched_results.append(
            {
                **result,
                "predictions": enriched_predictions,
            }
        )

    out["results"] = enriched_results
    return out

