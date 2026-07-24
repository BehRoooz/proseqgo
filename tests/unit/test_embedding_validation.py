"""Tests for GO prediction embedding vector validation."""

from __future__ import annotations

import numpy as np
import pytest


def test_validate_embedding_accepts_list(embedding_validation) -> None:
    out = embedding_validation.validate_embedding([0.1, 0.2, 0.3], expected_dim=3)
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32
    assert out.shape == (3,)


def test_validate_embedding_wrong_dim(embedding_validation) -> None:
    with pytest.raises(ValueError, match="length 4"):
        embedding_validation.validate_embedding([1.0, 2.0], expected_dim=4)


def test_validate_embedding_rejects_2d(embedding_validation) -> None:
    with pytest.raises(ValueError, match="1-dimensional"):
        embedding_validation.validate_embedding([[1.0, 2.0]], expected_dim=2)
