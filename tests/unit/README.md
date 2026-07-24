# Unit tests

Fast tests that run without Docker, GPU, or network.

```bash
# from a venv with pytest, pydantic, pyyaml, numpy:
make test
# or: pytest tests/unit -q
```

| Module | What it protects |
|--------|------------------|
| `test_sequences.py` | Shared AA normalize / FASTA id / tokenizer formatting |
| `test_config.py` | YAML config load + embedding-dim validation |
| `test_api_schemas.py` | Embedding/GO request schema bounds |
| `test_embedding_validation.py` | GO API embedding vector shape/dim checks |
| `test_ui_validation.py` | Public UI sequence/FASTA/auth gates |

Smoke / acceptance checks against a live Compose stack live in `tests/smoke/` and are **not** run here.
