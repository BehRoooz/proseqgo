"""Load a trained model checkpoint and generate CAFA-5 submission predictions."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from src.config import Config
from src.preprocess.dataset import ProteinSequenceDataset
from src.models import build_model
from src.utils import get_device

logger = logging.getLogger("cafa5")


def load_checkpoint(config: Config, checkpoint_path: str | Path) -> torch.nn.Module:
    """Instantiate the model from *config* and load weights from *checkpoint_path*.

    Returns:
        The model in eval mode on the best available device.
    """
    device = get_device()
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)

    model = build_model(config)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(device)
    model.eval()
    logger.info("Loaded checkpoint from %s (epoch %d, val_f1=%.4f)",
                checkpoint_path, checkpoint["epoch"], checkpoint["val_f1"])
    return model


def predict(config: Config, model: torch.nn.Module) -> pd.DataFrame:
    """Run inference on the test set and return a CAFA-5 submission DataFrame.

    Columns: ``Id``, ``GO term``, ``Confidence``.
    """
    device = get_device()
    datatype = config.prediction["datatype"]
    if datatype not in ("test", "holdout"):
        raise ValueError(f"datatype must be 'test' or 'holdout', got '{datatype}'")
    logger.info("Running inference on %s set ...", datatype)    
    test_dataset = ProteinSequenceDataset(config, datatype=datatype)
    test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False)

    label_matrix_dir = config.output_dir / f"label_matrix_top{config.num_labels}"
    term_names = np.load(label_matrix_dir / "term_names.npy", allow_pickle=True)

    num_labels = config.num_labels
    n_test = len(test_dataset)

    ids_ = np.empty(n_test * num_labels, dtype=object)
    go_terms_ = np.empty(n_test * num_labels, dtype=object)
    confs_ = np.empty(n_test * num_labels, dtype=np.float32)

    logger.info("Running inference on %d test samples ...", n_test)
    with torch.no_grad():
        for i, (embed, prot_id) in tqdm(enumerate(test_loader), total=n_test, desc="Predicting"):
            embed = embed.to(device)
            probs = torch.sigmoid(model(embed)).squeeze().cpu().numpy()
            start = i * num_labels
            end = start + num_labels
            confs_[start:end] = probs
            ids_[start:end] = prot_id[0] if isinstance(prot_id, (list, tuple)) else prot_id
            go_terms_[start:end] = term_names

    submission_df = pd.DataFrame({"Id": ids_, "GO term": go_terms_, "Confidence": confs_})
    logger.info("Inference complete — %d rows", len(submission_df))
    return submission_df


def save_submission(
    submission_df: pd.DataFrame,
    output_dir: str | Path,
    filename: str = "submission.tsv",
) -> Path:
    """Write submission DataFrame to TSV."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    submission_df.to_csv(path, sep="\t", index=False)
    logger.info("Submission saved → %s", path)
    return path
