"""Unit-test helpers: load service modules without installing the full package tree."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(module_name: str, relative_path: str):
    """Load a .py file as a uniquely named module (avoids schemas.py collisions)."""
    path = REPO_ROOT / relative_path
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Service packages often import sibling modules by bare name.
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(path.parent):
            sys.path.pop(0)
    return module


@pytest.fixture(scope="session")
def embedding_schemas():
    return load_module(
        "proseqgo_embedding_schemas",
        "services/embedding-api/schemas.py",
    )


@pytest.fixture(scope="session")
def go_schemas():
    return load_module(
        "proseqgo_go_schemas",
        "services/go-prediction-api/schemas.py",
    )


@pytest.fixture(scope="session")
def embedding_validation():
    return load_module(
        "proseqgo_embedding_validation",
        "services/go-prediction-api/embedding_validation.py",
    )


@pytest.fixture(scope="session")
def ui_validation():
    return load_module(
        "proseqgo_ui_validation",
        "services/streamlit-ui/validation.py",
    )
