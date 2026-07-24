from __future__ import annotations

from typing import Any

import numpy as np
import torch

from embedding_validation import validate_embedding


def predict_top_k(
    model: torch.nn.Module,
    embedding: list[float] | np.ndarray,
    term_names: np.ndarray,
    top_k: int = 10,
    apply_sigmoid: bool = True,
    device: str = "cpu",
    expected_dim: int = 1280,
) -> dict[str, Any]:
    emb = validate_embedding(embedding, expected_dim=expected_dim)
    x = torch.from_numpy(emb.reshape(1, -1)).to(device)

    with torch.no_grad():
        logits = model(x)
        scores = torch.sigmoid(logits) if apply_sigmoid else logits

    scores_np = scores.detach().cpu().numpy().reshape(-1)

    if top_k <= 0:
        raise ValueError("top_k must be > 0")

    top_k = min(top_k, len(scores_np))
    top_idx = np.argsort(scores_np)[::-1][:top_k]

    predictions = [
        {"go_term": str(term_names[i]), "score": float(scores_np[i])}
        for i in top_idx
    ]

    return {
        "top_k": top_k,
        "predictions": predictions,
    }
