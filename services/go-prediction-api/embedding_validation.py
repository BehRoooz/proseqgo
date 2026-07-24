"""Pure embedding input checks for the GO prediction API (no torch)."""

from __future__ import annotations

import numpy as np


def validate_embedding(embedding: list[float] | np.ndarray, expected_dim: int) -> np.ndarray:
    arr = np.asarray(embedding, dtype=np.float32)

    if arr.ndim != 1:
        raise ValueError("embedding must be a 1-dimensional list")

    if arr.shape[0] != expected_dim:
        raise ValueError(f"embedding must have length {expected_dim}")

    return arr
