from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

# Make repo root importable when running uvicorn with --app-dir services/embedding-api
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.embed_sequences import HF_MODEL_REGISTRY, embed_sequences  # noqa: E402
from src.preprocess.sequences import normalize_sequence  # noqa: E402
from src.utils import get_device  # noqa: E402


_MODEL_CACHE: dict[str, tuple[object, object]] = {}


def get_model_and_tokenizer(backend: str):
    backend = backend.lower().strip()
    if backend in _MODEL_CACHE:
        return _MODEL_CACHE[backend]

    from transformers import AutoModel, AutoTokenizer

    if backend not in HF_MODEL_REGISTRY:
        raise ValueError(f"Unsupported backend: {backend}")

    hf_model_name = HF_MODEL_REGISTRY[backend]["hf_model_name"]
    tokenizer = AutoTokenizer.from_pretrained(hf_model_name)
    model = AutoModel.from_pretrained(hf_model_name)
    model.eval()
    _MODEL_CACHE[backend] = (tokenizer, model)
    return tokenizer, model


def embed_sequence_batch(
    ids: list[str],
    sequences: list[str],
    *,
    backend: str,
    pooling: str,
    max_length: int,
    batch_size: int,
) -> tuple[list[str], np.ndarray]:
    if len(ids) != len(sequences):
        raise ValueError("ids and sequences length mismatch")

    device = get_device()
    tokenizer, model = get_model_and_tokenizer(backend)
    model.to(device)
    tokenizer_mode = HF_MODEL_REGISTRY[backend]["tokenizer_mode"]

    normalized = [normalize_sequence(seq) for seq in sequences]
    embeddings_chunks: list[np.ndarray] = []

    for i in range(0, len(normalized), batch_size):
        batch = normalized[i : i + batch_size]
        embeds = embed_sequences(
            model,
            tokenizer,
            batch,
            device,
            max_length=max_length,
            fp16=(device.type == "cuda"),
            pooling=pooling,
            tokenizer_mode=tokenizer_mode,
        )
        embeddings_chunks.append(embeds)

    embeddings = np.concatenate(embeddings_chunks, axis=0).astype(np.float32)
    return ids, embeddings

