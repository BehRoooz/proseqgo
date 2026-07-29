# Data

This document defines data sources, layout, versioning, preprocessing, and reproducibility requirements for ProSeqGO.

## Data sources

### Primary training dataset (Kaggle)


| Field   | Value                                                                                           |
| ------- | ----------------------------------------------------------------------------------------------- |
| Dataset | [cafa-5-6-train-dataset](https://www.kaggle.com/datasets/behrouzmirabdi/cafa-5-6-train-dataset) |
| Owner   | `behrouzmirabdi`                                                                                |
| Access  | Kaggle API (`~/.kaggle/kaggle.json`) or browser download                                        |
| License | See Kaggle dataset page                                                                         |


**Required files** (paths relative to repo root, matching `configs/config.yaml`):

```text
data/cafa-5-cafa-6-protein-function-prediction/
└── Train/
    ├── train_sequences.fasta
    └── train_terms.tsv
```



### Integrity checksums

Verify files after download:

```bash
sha256sum data/cafa-5-cafa-6-protein-function-prediction/Train/train_sequences.fasta \
          data/cafa-5-cafa-6-protein-function-prediction/Train/train_terms.tsv
```


| File                    | Expected sha256                                                    |
| ----------------------- | ------------------------------------------------------------------ |
| `train_sequences.fasta` | `434addef94c14eb8fb263ad2f5801a73a43fcb69d10955e5463d20c6b8aaac82` |
| `train_terms.tsv`       | `c9489b802b8955d3cb14c23cc465674de86e08ad23107296260c8a8040361535` |




### External model weights (Hugging Face)

Embedding backends download pretrained weights on first use:


| Backend key | HF model                       |
| ----------- | ------------------------------ |
| `esm2`      | `facebook/esm2_t33_650M_UR50D` |
| `protbert`  | `Rostlab/prot_bert`            |
| `t5`        | `Rostlab/prot_t5_xl_uniref50`  |


Cache directory: `data/hf_cache/` (mounted in embedding containers).

## Data layout

```text
data/
├── cafa-5-cafa-6-protein-function-prediction/   # Raw Kaggle data (gitignored)
│   └── Train/
│       ├── train_sequences.fasta
│       └── train_terms.tsv
├── embeddings/                                   # Generated .npy embeddings
│   └── hf_<backend>_<pooling>/                   # e.g. hf_esm2_mean/
└── hf_cache/                                     # Hugging Face model cache

outputs/
├── splits/                                       # train/holdout ID arrays
│   ├── train_ids.npy
│   └── holdout_ids.npy
├── labels/                                       # Binary label matrix artifacts
├── checkpoints/                                  # Training checkpoints
├── service_artifacts/                            # Embedding API job outputs
└── training_api/                                 # Training API job outputs
```



### Raw vs processed vs derived


| Stage                          | Location           | Regenerable                      | Git-tracked |
| ------------------------------ | ------------------ | -------------------------------- | ----------- |
| Raw FASTA + terms              | `data/.../Train/`  | Re-download from Kaggle          | No          |
| Label matrix                   | `outputs/labels/`  | `scripts/preprocess.py`          | No          |
| Splits                         | `outputs/splits/`  | `scripts/split_train_holdout.py` | No          |
| Embeddings                     | `data/embeddings/` | `scripts/embed_sequences.py`     | No          |
| HF cache                       | `data/hf_cache/`   | Auto on first embed              | No          |
| Checkpoints / MLflow artifacts | `outputs/`, MinIO  | Training pipeline                | No          |


Serving (`make up`) does **not** require training data. Preprocess, embed, train, and holdout evaluation do.

## Versioning strategy

1. **Dataset version:** Pin the Kaggle dataset version or record the download date and checksums in MLflow run tags (training script logs file hashes when available).
2. **Config version:** All pipeline scripts accept `--config configs/config.yaml`; treat config changes as data/model contract changes.
3. **Embedding backend alignment:** `data.embeddings_source`, `embedding.backend`, and served model input dimension must match. Mismatch causes inference validation failures.

Record in every training run:

- `train_sequences.fasta` sha256
- `train_terms.tsv` sha256
- `embedding.backend` and `embedding.pooling`
- split seed / holdout fraction from config



## Ingestion workflow



### Download via Kaggle CLI

```bash
mkdir -p data/cafa-5-cafa-6-protein-function-prediction/Train
kaggle datasets download -d behrouzmirabdi/cafa-5-6-train-dataset \
  -p data/cafa-5-cafa-6-protein-function-prediction/Train --unzip
```



### Validation checks

After download:

1. Confirm both files exist under `Train/`.
2. Run sha256 verification (table above).
3. Spot-check FASTA record count and terms file column structure.



## Preprocessing pipeline



### 1. Label matrix

```bash
python scripts/preprocess.py --config configs/config.yaml
```

Builds a binary multi-label matrix from `train_terms.tsv`. Output paths are defined in `src/preprocess/preprocessing.py` and written under `outputs/`.

Key config (`configs/config.yaml`):

- `data.num_labels`: top-N GO terms (default 500)
- `data.train_val_split`: train/validation fraction within labeled set



### 2. Train/holdout split

```bash
python scripts/split_train_holdout.py --config configs/config.yaml
```

Produces deterministic `train_ids.npy` and `holdout_ids.npy` in `outputs/splits/` using `data.holdout_fraction` (default 0.1) and `training.seed` (default 42).

### 3. Embedding generation

```bash
python scripts/embed_sequences.py --config configs/config.yaml \
  --ids-npy outputs/splits/train_ids.npy --split train

python scripts/embed_sequences.py --config configs/config.yaml \
  --ids-npy outputs/splits/holdout_ids.npy --split holdout
```

Outputs `.npy` arrays compatible with `ProteinSequenceDataset` under `data/embeddings/`.

## Sequence normalization

Applied at embedding time (`normalize_sequence` in `scripts/embed_sequences.py`):

- Uppercase, whitespace stripped
- Canonical amino acids retained
- `X`, `U`, `O`, `B`, `Z`, `J`, and unknown symbols → `X`

API endpoints (`/api/v1/predict-go-from-sequences`, FASTA upload) use the same normalization.

## Data contracts



### Training / inference inputs


| Field            | Requirement                                         |
| ---------------- | --------------------------------------------------- |
| Protein ID       | Non-empty string; matches FASTA header or JSON `id` |
| Sequence         | Amino acid string; normalized as above              |
| Embedding vector | Length must match model input dim for GO predictor  |
| GO labels        | `GO:#######` format in terms file                   |




### Config-driven paths

All paths are relative to repo root and defined in `configs/config.yaml`:

```yaml
data:
  data_dir: "data/cafa-5-cafa-6-protein-function-prediction"
  train_fasta: "data/cafa-5-cafa-6-protein-function-prediction/Train/train_sequences.fasta"
  embeddings_dir: "data/embeddings"
  splits_dir: "outputs/splits"
```

Do not hardcode machine-specific absolute paths in scripts or configs committed to git.

## Storage and retention


| Environment        | Raw data                      | Embeddings           | Artifacts                       |
| ------------------ | ----------------------------- | -------------------- | ------------------------------- |
| Local dev          | `./data/` bind mount          | `./data/embeddings/` | `./outputs/`                    |
| Compose services   | `./data`, `./outputs` volumes | `./data/hf_cache`    | `./outputs/service_artifacts/`  |
| MLflow (prod-like) | N/A                           | N/A                  | MinIO `mlflow-artifacts` bucket |


**Regenerable without data loss:** embeddings, splits, label matrix, local checkpoints.

**Must preserve for audit:** MLflow runs in Postgres, model versions in registry, promoted champion metadata.

## Reproducibility checklist

To rerun training on the same data:

- [ ] Download dataset and verify sha256
- [ ] Use unchanged `configs/config.yaml` (or document diffs)
- [ ] Set `training.seed: 42`
- [ ] Run preprocess → split → embed (same backend/pooling) → train
- [ ] Point `MLFLOW_TRACKING_URI` at the same tracking server
- [ ] Log dataset checksums from `train_run_summary.json`



## Privacy and compliance

- Training data is public competition data; confirm Kaggle license before redistribution.
- Do not commit raw data, credentials, or user-submitted sequences from production inference to git.
- Service artifacts under `outputs/service_artifacts/` may contain user sequences; treat as sensitive in shared environments.



## Related documentation

- [training.md](training.md) — how processed data feeds the training pipeline
- [architecture.md](architecture.md) — data flow through services
- [deployment.md](deployment.md) — volume mounts and data paths in Compose

