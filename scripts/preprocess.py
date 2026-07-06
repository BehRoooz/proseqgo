#!/usr/bin/env python
"""CLI: Generate binary label matrix from raw train_terms.tsv.

Usage:
    python scripts/preprocess.py --config configs/config.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from src.config import load_config
from src.preprocess.preprocessing import build_label_matrix, save_label_matrix
from src.utils import setup_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess Dataset labels")
    parser.add_argument(
        "--config",
        type=str,
        default="configs/config.yaml",
        help="Path to the YAML config file",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logger("cafa5", log_dir=config.output_dir)

    label_matrix, protein_ids, term_names = build_label_matrix(config)
    save_label_matrix(config, label_matrix, protein_ids, term_names)


if __name__ == "__main__":
    main()
