"""Tests for YAML config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import Config, EMBEDDING_DIMS, load_config


def test_load_config_from_repo_yaml() -> None:
    cfg = load_config("configs/config.yaml")
    assert cfg.embedding_dim == EMBEDDING_DIMS["esm2"]
    assert cfg.seed == 42
    assert cfg.num_labels == 500
    assert cfg.epochs == 60


def test_config_unknown_embeddings_source() -> None:
    with pytest.raises(ValueError, match="Unknown embeddings_source"):
        Config(data={"embeddings_source": "not-a-backend"})


def test_config_defaults_and_accessors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    raw = {
        "data": {"embeddings_source": "protbert", "num_labels": 10},
        "training": {},
        "output": {"output_dir": "out"},
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    cfg = load_config(path)
    assert cfg.embedding_dim == 1024
    assert cfg.batch_size == 128
    assert cfg.learning_rate == 1e-3
    assert cfg.output_dir == tmp_path / "out"


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")
