"""Training loop with BCEWithLogitsLoss, proper eval mode, and checkpointing."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split
from torchmetrics.classification import MultilabelF1Score
from tqdm import tqdm

from src.config import Config
from src.utils import get_device, set_seed

logger = logging.getLogger("cafa5")


class Trainer:
    """Encapsulates the full training / validation cycle.

    Fixes applied relative to the original notebook:
      1. ``BCEWithLogitsLoss`` instead of ``CrossEntropyLoss``.
      2. ``model.eval()`` + ``torch.no_grad()`` in the validation phase.
      3. Sigmoid + threshold before ``MultilabelF1Score``.
      4. Seeded ``random_split`` for reproducibility.
      5. Checkpoint saving (best val F1).

    Args:
        config: Project configuration.
        model: The neural network to train.
        dataset: The full training ``Dataset``.
    """

    def __init__(
        self,
        config: Config,
        model: nn.Module,
        dataset: torch.utils.data.Dataset,
    ) -> None:
        self.config = config
        self.device = get_device()
        self.model = model.to(self.device)

        set_seed(config.seed)
        train_size = int(len(dataset) * config.data.get("train_val_split", 0.9))
        val_size = len(dataset) - train_size
        generator = torch.Generator().manual_seed(config.seed)
        self.train_set, self.val_set = random_split(
            dataset, [train_size, val_size], generator=generator
        )

        self.train_loader = DataLoader(
            self.train_set, batch_size=config.batch_size, shuffle=True
        )
        self.val_loader = DataLoader(
            self.val_set, batch_size=config.batch_size, shuffle=False
        )

        self.criterion = nn.BCEWithLogitsLoss()
        self.optimizer = torch.optim.Adam(
            self.model.parameters(), lr=config.learning_rate
        )
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer,
            factor=config.training.get("scheduler_factor", 0.1),
            patience=config.training.get("scheduler_patience", 1),
        )
        self.f1_metric = MultilabelF1Score(
            num_labels=config.num_labels, threshold=0.5
        ).to(self.device)

        self.checkpoint_dir = config.output_dir / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def train(self) -> dict[str, Any]:
        """Run the full training loop.

        Returns:
            Dictionary with ``train_loss``, ``val_loss``,
            ``train_f1``, ``val_f1`` histories (lists of per-epoch values).
        """
        history: dict[str, list[float]] = {
            "train_loss": [],
            "val_loss": [],
            "train_f1": [],
            "val_f1": [],
        }
        best_val_f1 = -1.0

        for epoch in range(1, self.config.epochs + 1):
            train_loss, train_f1 = self._train_epoch(epoch)
            val_loss, val_f1 = self._validate_epoch(epoch)

            self.scheduler.step(val_loss)

            history["train_loss"].append(train_loss)
            history["val_loss"].append(val_loss)
            history["train_f1"].append(train_f1)
            history["val_f1"].append(val_f1)

            logger.info(
                "Epoch %d/%d — train_loss=%.4f  train_f1=%.4f  val_loss=%.4f  val_f1=%.4f",
                epoch,
                self.config.epochs,
                train_loss,
                train_f1,
                val_loss,
                val_f1,
            )

            if val_f1 > best_val_f1:
                best_val_f1 = val_f1
                self._save_checkpoint(epoch, val_f1)

        logger.info("Training complete. Best val F1: %.4f", best_val_f1)
        return history

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _train_epoch(self, epoch: int) -> tuple[float, float]:
        self.model.train()
        losses: list[float] = []
        scores: list[float] = []

        for embeds, targets in tqdm(
            self.train_loader, desc=f"Epoch {epoch} [train]", leave=False
        ):
            embeds, targets = embeds.to(self.device), targets.to(self.device)
            self.optimizer.zero_grad()

            logits = self.model(embeds)
            loss = self.criterion(logits, targets)

            preds = torch.sigmoid(logits)
            score = self.f1_metric(preds, targets.int())

            loss.backward()
            self.optimizer.step()

            losses.append(loss.item())
            scores.append(score.item())

        return float(np.mean(losses)), float(np.mean(scores))

    @torch.no_grad()
    def _validate_epoch(self, epoch: int) -> tuple[float, float]:
        self.model.eval()
        losses: list[float] = []
        scores: list[float] = []

        for embeds, targets in tqdm(
            self.val_loader, desc=f"Epoch {epoch} [val]", leave=False
        ):
            embeds, targets = embeds.to(self.device), targets.to(self.device)

            logits = self.model(embeds)
            loss = self.criterion(logits, targets)

            preds = torch.sigmoid(logits)
            score = self.f1_metric(preds, targets.int())

            losses.append(loss.item())
            scores.append(score.item())

        return float(np.mean(losses)), float(np.mean(scores))

    def _save_checkpoint(self, epoch: int, val_f1: float) -> None:
        path = self.checkpoint_dir / "best_model.pt"
        torch.save(
            {
                "epoch": epoch,
                "model_state_dict": self.model.state_dict(),
                "optimizer_state_dict": self.optimizer.state_dict(),
                "val_f1": val_f1,
                "config": {
                    "model": self.config.model,
                    "data": self.config.data,
                    "training": self.config.training,
                },
            },
            path,
        )
        logger.info("Checkpoint saved → %s (epoch %d, val_f1=%.4f)", path, epoch, val_f1)
