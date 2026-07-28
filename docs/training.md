# Training

This document describes the ML lifecycle in ProSeqGO: from preprocessing through experiment tracking, model registration, evaluation, and champion promotion.

## Training objective

**Task:** Multi-label classification — predict GO terms from protein sequence embeddings.

**Primary promotion metric:** `holdout_f1_micro` on the holdout split (evaluated by `scripts/evaluate_holdout.py`).

**Default promotion threshold:** `0.35` (`PROMOTION_THRESHOLD` in `.env`).

**Registered model name:** `cafa-go-model` (`REGISTERED_MODEL_NAME`).

Serving loads `models:/cafa-go-model@champion` unless `MODEL_URI` is overridden.

## Pipeline stages

```text
preprocess → split → embed → train → evaluate_holdout → promote_model
```

| Stage | Script | Output |
|-------|--------|--------|
| Labels | `scripts/preprocess.py` | Binary label matrix under `outputs/` |
| Split | `scripts/split_train_holdout.py` | `outputs/splits/{train,holdout}_ids.npy` |
| Embeddings | `scripts/embed_sequences.py` | `data/embeddings/hf_*/*.npy` |
| Train | `scripts/train.py` | Checkpoint, MLflow run, registered model version |
| Evaluate | `scripts/evaluate_holdout.py` | Holdout metrics, eval MLflow run |
| Promote | `scripts/promote_model.py` | `champion` alias if metric ≥ threshold |

### One-shot retrain pipeline

```bash
python scripts/retrain_pipeline.py --config configs/config.yaml \
  --promotion-threshold 0.35 \
  --model-name cafa-go-model
```

Runs train → evaluate → promote in sequence. Requires `MLFLOW_TRACKING_URI` and S3/MinIO env vars when using the Compose MLflow stack.

## Configuration

Global config: [`configs/config.yaml`](../configs/config.yaml).

### Data

```yaml
data:
  num_labels: 500
  holdout_fraction: 0.1
  embeddings_source: "ESM2"
```

### Embedding

```yaml
embedding:
  backend: "esm2"       # esm2 | protbert | t5
  pooling: "mean"       # mean | cls
  max_length: 1280
  batch_size: 8
  fp16: true
```

**Critical:** `embedding.backend` must match the embeddings used during training and the dimension expected by the GO predictor at inference time.

### Model

```yaml
model:
  type: "cnn1d"         # mlp | cnn1d
  cnn_out_channels: [3, 8]
  cnn_kernel_size: 3
```

### Training

```yaml
training:
  epochs: 60
  batch_size: 256
  learning_rate: 0.001
  scheduler_factor: 0.1
  scheduler_patience: 3
  seed: 42
```

### Environment variables

| Variable | Purpose |
|----------|---------|
| `MLFLOW_TRACKING_URI` | Tracking server (default `file:./mlruns` for local CLI) |
| `MLFLOW_S3_ENDPOINT_URL` | MinIO endpoint for artifact I/O |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 credentials for MLflow artifacts |
| `REGISTERED_MODEL_NAME` | Registry name (default `cafa-go-model`) |
| `PROMOTION_THRESHOLD` | Minimum `holdout_f1_micro` for promotion |
| `CAFA_DEVICE` | `auto`, `cpu`, or `cuda` for PyTorch |
| `TRAIN_RUN_ID` | Set by retrain pipeline for evaluate step |

## Experiment tracking

Training uses MLflow experiment **`cafa-train`**.

Logged per run:

- Hyperparameters from config
- Training metrics (loss, validation metrics)
- Model artifact (PyTorch)
- Dataset file checksums when available
- `train_run_summary.json` in `outputs/` with `train_run_id`

Evaluation uses a separate MLflow run linked via `train_run_id` tag; metrics include `holdout_f1_micro`.

**Tracking URI in Compose:** `http://mlflow:5000` (internal). External UI: `https://localhost/mlflow/` via gateway.

## Model registry workflow

1. `scripts/train.py` registers a new model version under `REGISTERED_MODEL_NAME`.
2. `scripts/evaluate_holdout.py` logs holdout metrics on a dedicated eval run.
3. `scripts/promote_model.py`:
   - Reads `holdout_f1_micro` from the eval run
   - Resolves model version from `train_run_id`
   - Tags version with promotion metadata
   - Sets `champion` alias if `metric ≥ threshold`

```bash
python scripts/promote_model.py \
  --eval-run-id <EVAL_RUN_ID> \
  --train-run-id <TRAIN_RUN_ID> \
  --model-name cafa-go-model \
  --threshold 0.35
```

Tags written on the model version: `promotion_metric`, `promotion_value`, `promotion_threshold`, `train_run_id`, `eval_run_id`.

### Rollback

To revert serving to a previous version:

```python
from mlflow.tracking import MlflowClient
client = MlflowClient("http://mlflow:5000")
client.set_registered_model_alias("cafa-go-model", "champion", "<version>")
```

Restart or reload `go-prediction-api` if it caches the model in memory.

## Retraining options

### Option A: CLI (research iteration)

```bash
python scripts/retrain_pipeline.py --config configs/config.yaml
```

Best for local experimentation with full control over each stage.

### Option B: Training API (ops automation)

```bash
make training-up
curl -sk -u ADMIN:ADMIN_PASS -X POST https://localhost/api/train/train \
  -H "Content-Type: application/json" \
  -d '{"config":"configs/config.yaml","mode":"retrain"}'
```

Poll `GET /api/train/jobs/{job_id}` for status and MLflow links.

### Option C: Hybrid

- Generate embeddings via Embedding API (`/api/v1/jobs`) for ad-hoc or online data
- Train/evaluate via CLI for flexibility
- Promote via `scripts/promote_model.py` or retrain pipeline

## Compute requirements

| Workload | CPU | GPU recommended |
|----------|-----|-----------------|
| Preprocess / split | Yes | No |
| Embedding generation | Yes (slow) | Yes |
| Training | Yes (slow) | Yes |
| Holdout evaluation | Yes | Optional |
| Serving (inference) | Yes | Yes for throughput |

Compose defaults: `CAFA_DEVICE=auto` (GPU if available via `docker-compose.gpu.yml`).

**Training job timeout:** `TRAINING_JOB_TIMEOUT_SEC` (default 86400 s).

## Reproducibility

- Set `training.seed: 42` in config; `set_seed()` called in training script
- Use fixed holdout split from `split_train_holdout.py`
- Pin embedding backend and record checksums of input FASTA/terms in MLflow
- Pin dependencies via `requirements.txt` / Docker images (`ghcr.io/behroooz/proseqgo-*`)

### Rerun a past experiment

1. Find `train_run_id` in MLflow UI or `outputs/train_run_summary.json`
2. Restore config used for that run (MLflow params or git commit)
3. Re-run with same data checksums and seed

## Evaluation protocol

- **Holdout split:** 10% of labeled proteins (`holdout_fraction: 0.1`), deterministic
- **No test-set tuning:** holdout is for final gate only; use train/val split inside training for early stopping
- **Promotion gate:** `holdout_f1_micro ≥ PROMOTION_THRESHOLD`
- **Metric name override:** `--metric-name` on `promote_model.py` (default `holdout_f1_micro`)

## Known limitations

- **Class imbalance:** top-500 GO terms still span wide frequency; threshold sensitivity affects rare terms
- **Embedding alignment:** switching backend without retraining breaks inference validation
- **Label coverage:** model only predicts terms present in the training label matrix (`num_labels`)
- **Multi-label bias:** high-frequency GO terms may dominate micro-F1

## Related documentation

- [data.md](data.md) — dataset and preprocessing
- [architecture.md](architecture.md) — training API and worker architecture
- [deployment.md](deployment.md) — training profile and volumes
- [monitoring.md](monitoring.md) — training queue metrics when profile is active
