#!/usr/bin/env python
"""Convert protein FASTA or a raw amino-acid sequence into embedding vectors.

This script supports Hugging Face protein language models:
 - facebook/esm2_t33_650M_UR50D (default)
 - Rostlab/prot_bert
 - Rostlab/prot_t5_xl_uniref50

Outputs are `.npy` arrays compatible with the existing `ProteinSequenceDataset`.

Examples:
    python scripts/embed_sequences.py --config configs/config.yaml \
        --ids-npy outputs/splits/train_ids.npy \
        --split train

    python scripts/embed_sequences.py --config configs/config.yaml \
        --ids-npy outputs/splits/holdout_ids.npy \
        --split holdout

    python scripts/embed_sequences.py --config configs/config.yaml \
        --sequence "MKTAYIAKQRQISFVKSHFSRQ"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config, load_config
from src.preprocess.dataset import EMBED_FILE_MAP
from src.utils import get_device, setup_logger

logger = logging.getLogger("cafa5")


HF_MODEL_REGISTRY: dict[str, dict[str, str]] = {
    # dataset embeddings_source keys
    "esm2": {
        "hf_model_name": "facebook/esm2_t33_650M_UR50D",
        "tokenizer_mode": "esm",
    },
    "protbert": {
        "hf_model_name": "Rostlab/prot_bert",
        "tokenizer_mode": "space_separated",
    },
    "t5": {
        "hf_model_name": "Rostlab/prot_t5_xl_uniref50",
        "tokenizer_mode": "space_separated",
    },
}

from src.preprocess.sequences import (  # noqa: E402
    extract_protein_id,
    format_for_tokenizer,
    normalize_sequence,
)


def residue_mean_pool(
    last_hidden_state: torch.Tensor, attention_mask: torch.Tensor, input_ids: torch.Tensor, tokenizer
) -> torch.Tensor:
    """Mean-pool over *non-special* tokens only."""
    residue_mask = attention_mask.bool()
    # Exclude all special tokens so pooling is over residues.
    for special_id in getattr(tokenizer, "all_special_ids", []):
        if special_id is None:
            continue
        residue_mask &= input_ids != special_id

    mask = residue_mask.float().unsqueeze(-1)  # (B, L, 1)
    summed = (last_hidden_state * mask).sum(dim=1)  # (B, H)
    denom = mask.sum(dim=1).clamp(min=1e-9)  # (B, 1)
    return summed / denom


@torch.no_grad()
def embed_sequences(
    model,
    tokenizer,
    sequences: list[str],
    device: torch.device,
    *,
    max_length: int,
    fp16: bool,
    pooling: str,
    tokenizer_mode: str,
) -> np.ndarray:
    """Embed a list of sequences into `(N, D)` float32 vectors."""
    formatted = [format_for_tokenizer(s, tokenizer_mode) for s in sequences]

    toks = tokenizer(
        formatted,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=max_length,
    )
    toks = {k: v.to(device) for k, v in toks.items()}

    use_autocast = fp16 and device.type == "cuda"
    if use_autocast:
        with torch.autocast(device_type="cuda", enabled=True):
            out = model(**toks)
            hidden = out.last_hidden_state  # (B, L, H)
    else:
        out = model(**toks)
        hidden = out.last_hidden_state  # (B, L, H)

    if pooling == "mean":
        emb = residue_mean_pool(hidden, toks["attention_mask"], toks["input_ids"], tokenizer)
    elif pooling == "cls":
        emb = hidden[:, 0, :]
    else:
        raise ValueError(f"Unknown pooling '{pooling}'. Use 'mean' or 'cls'.")

    return emb.detach().float().cpu().numpy()


def iter_fasta_records(fasta_path: Path):
    """Yield (id, sequence) from a FASTA file."""
    header = None
    seq_chunks: list[str] = []
    with fasta_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    yield extract_protein_id(header), "".join(seq_chunks)
                header = line
                seq_chunks = []
            else:
                seq_chunks.append(line)
        if header is not None:
            yield extract_protein_id(header), "".join(seq_chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Protein embedding generation (HF transformers)")
    parser.add_argument(
        "--config",
        type=str,
        default="configs/config.yaml",
        help="Path to YAML config",
    )
    parser.add_argument("--fasta", type=str, default=None, help="FASTA file with sequences")
    parser.add_argument("--ids-npy", type=str, default=None, help="Optional .npy with EntryIDs to embed")
    parser.add_argument(
        "--split",
        type=str,
        default="train",
        help="Output split: train | holdout | test | all",
    )
    parser.add_argument(
        "--sequence",
        type=str,
        default=None,
        help="Raw amino-acid sequence (no FASTA required)",
    )
    parser.add_argument("--id", type=str, default="user_sequence", help="ID used when --sequence is provided")
    args = parser.parse_args()

    config: Config = load_config(args.config)
    logger = setup_logger("cafa5", log_dir=config.output_dir)
    device = get_device()

    backend_from_cfg = str(config.embedding.get("backend", "")).lower().strip()
    if backend_from_cfg:
        backend = backend_from_cfg
    else:
        backend = str(config.data.get("embeddings_source", "ESM2")).lower().strip()

    # Normalize backend names to dataset keys.
    if backend in {"esm2", "esm"}:
        dataset_key = "esm2"
    elif backend in {"protbert", "prot_bert"}:
        dataset_key = "protbert"
    elif backend in {"t5", "prot_t5", "prot-t5", "prott5"}:
        dataset_key = "t5"
    else:
        raise ValueError(
            f"Unknown embedding backend '{backend}'. Expected esm2 | prot_bert | prot_t5."
        )

    train_source = str(config.data.get("embeddings_source", "ESM2")).lower().strip()
    train_source_key = (
        "esm2" if train_source in {"esm2", "esm"} else "protbert" if train_source in {"protbert", "prot_bert"} else "t5"
    )
    if train_source_key != dataset_key:
        logger.warning(
            "Config mismatch: embedding backend '%s' writes to '%s', but training uses embeddings_source='%s' (resolved '%s'). "
            "Update configs so they match, otherwise training will load different embeddings.",
            backend,
            dataset_key,
            train_source,
            train_source_key,
        )

    if args.sequence is None and args.fasta is None:
        fasta_default = config.data.get("train_fasta")
        if not fasta_default:
            raise ValueError("Provide --fasta or set data.train_fasta in configs/config.yaml")
        args.fasta = str(fasta_default)

    split = args.split.lower().strip()
    if split not in {"train", "holdout", "test", "all"}:
        raise ValueError(f"Unknown split '{split}'. Use train | holdout | test | all")

    hf_id = HF_MODEL_REGISTRY[dataset_key]["hf_model_name"]
    tokenizer_mode = HF_MODEL_REGISTRY[dataset_key]["tokenizer_mode"]

    # Late import so users can at least inspect the script without transformers installed.
    from transformers import AutoModel, AutoTokenizer

    logger.info("Loading HF tokenizer/model: %s", hf_id)
    tokenizer = AutoTokenizer.from_pretrained(hf_id)
    model = AutoModel.from_pretrained(hf_id)
    model.to(device)
    model.eval()

    pooling = str(config.embedding.get("pooling", "mean")).lower()
    max_length = int(config.embedding.get("max_length", 1024))
    batch_size = int(config.embedding.get("batch_size", 8))
    fp16 = bool(config.embedding.get("fp16", True))

    embeds_dir = Path(config.data.get("embeddings_dir", "./data/embeddings"))
    embed_subdir = embeds_dir / EMBED_FILE_MAP[dataset_key]["dir"]
    embed_subdir.mkdir(parents=True, exist_ok=True)

    if split == "train":
        embed_file = "train_embeddings.npy"
        ids_file = "train_ids.npy"
    elif split == "holdout":
        embed_file = "holdout_embeddings.npy"
        ids_file = "holdout_ids.npy"
    elif split == "test":
        embed_file = "test_embeddings.npy"
        ids_file = "test_ids.npy"
    else:
        embed_file = "all_embeddings.npy"
        ids_file = "all_ids.npy"

    created_at = datetime.now(timezone.utc).isoformat()

    # -------------------- Raw sequence mode --------------------
    if args.sequence is not None:
        seq = normalize_sequence(args.sequence)
        ids = np.asarray([args.id], dtype=object)
        sequences = [seq]

        emb = embed_sequences(
            model,
            tokenizer,
            sequences,
            device,
            max_length=max_length,
            fp16=fp16,
            pooling=pooling,
            tokenizer_mode=tokenizer_mode,
        )

        np.save(embed_subdir / embed_file, emb.astype(np.float32))
        np.save(embed_subdir / ids_file, ids)

        meta = {
            "backend_dataset_key": dataset_key,
            "hf_model_name": hf_id,
            "pooling": pooling,
            "max_length": max_length,
            "created_at": created_at,
            "mode": "raw_sequence",
            "n_sequences": 1,
        }
        (embed_subdir / f"embed_meta_{split}.json").write_text(json.dumps(meta, indent=2))
        logger.info("Saved raw embedding → %s/%s", embed_subdir, embed_file)
        return

    # -------------------- FASTA mode --------------------
    fasta_path = Path(args.fasta)
    if not fasta_path.exists():
        raise FileNotFoundError(f"FASTA not found: {fasta_path}")

    ids_filter: np.ndarray | None = None
    ids_set: set[str] | None = None
    if args.ids_npy is not None:
        ids_filter = np.load(args.ids_npy, allow_pickle=True)
        ids_filter = np.asarray(ids_filter)
        ids_set = set(ids_filter.tolist())

    n_total = 0
    id_to_seq: dict[str, str] = {}
    if ids_set is None:
        # Embed all FASTA records (stream directly).
        sequences_all: list[str] = []
        ids_all: list[str] = []
        for pid, seq in iter_fasta_records(fasta_path):
            seq_norm = normalize_sequence(seq)
            sequences_all.append(seq_norm)
            ids_all.append(pid)
            n_total += 1
        ids = np.asarray(ids_all, dtype=object)
        sequences = sequences_all
    else:
        # Embed only filtered IDs (store only matching sequences).
        for pid, seq in iter_fasta_records(fasta_path):
            if pid in ids_set:
                if pid not in id_to_seq:
                    id_to_seq[pid] = normalize_sequence(seq)
                    n_total += 1
        missing = [pid for pid in ids_filter.tolist() if pid not in id_to_seq]
        if missing:
            logger.warning("Missing %d IDs from FASTA (will skip): %s", len(missing), missing[:5])
        present_ids = [pid for pid in ids_filter.tolist() if pid in id_to_seq]
        ids = np.asarray(present_ids, dtype=object)
        sequences = [id_to_seq[pid] for pid in present_ids]

    logger.info("Embedding %d sequences (split=%s) using %s", len(sequences), split, hf_id)

    embeddings: list[np.ndarray] = []
    for i in range(0, len(sequences), batch_size):
        batch = sequences[i : i + batch_size]
        emb_batch = embed_sequences(
            model,
            tokenizer,
            batch,
            device,
            max_length=max_length,
            fp16=fp16,
            pooling=pooling,
            tokenizer_mode=tokenizer_mode,
        )
        embeddings.append(emb_batch)
        if (i // batch_size) % 20 == 0:
            logger.info("Embedded %d/%d", min(i + batch_size, len(sequences)), len(sequences))

    embeddings_arr = np.concatenate(embeddings, axis=0).astype(np.float32)
    if embeddings_arr.ndim != 2 or embeddings_arr.shape[0] != len(ids):
        raise RuntimeError(
            f"Embedding shape mismatch: embeddings={embeddings_arr.shape}, ids={len(ids)}"
        )

    np.save(embed_subdir / embed_file, embeddings_arr)
    np.save(embed_subdir / ids_file, ids)

    meta = {
        "backend_dataset_key": dataset_key,
        "hf_model_name": hf_id,
        "pooling": pooling,
        "max_length": max_length,
        "batch_size": batch_size,
        "fp16": fp16,
        "created_at": created_at,
        "mode": "fasta",
        "fasta_path": str(fasta_path),
        "ids_npy": str(args.ids_npy) if args.ids_npy else None,
        "n_total_fasta_records": n_total,
        "n_embedded": int(len(ids)),
        "embedding_dim": int(embeddings_arr.shape[1]),
    }
    (embed_subdir / f"embed_meta_{split}.json").write_text(json.dumps(meta, indent=2))
    logger.info("Saved %s (%s) → %s", split, embeddings_arr.shape, embed_subdir / embed_file)


if __name__ == "__main__":
    main()

