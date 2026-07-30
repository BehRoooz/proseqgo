"""UI input validation helpers (no Streamlit dependency)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

AA_PATTERN = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$")

MAX_SEQUENCES_PER_REQUEST = int(os.getenv("MAX_SEQUENCES_PER_REQUEST", "20"))
MAX_SEQUENCE_LENGTH_AA = int(os.getenv("MAX_SEQUENCE_LENGTH_AA", "1000"))
MAX_FASTA_UPLOAD_MB = int(os.getenv("MAX_FASTA_UPLOAD_MB", "2"))
MAX_FASTA_UPLOAD_BYTES = MAX_FASTA_UPLOAD_MB * 1024 * 1024
SYNC_PREDICT_TIMEOUT_SEC = int(os.getenv("SYNC_PREDICT_TIMEOUT_SEC", "600"))


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    username: str
    password: str
    verify_tls: bool


def normalize_sequence(raw_sequence: str) -> str:
    compact = re.sub(r"\s+", "", raw_sequence or "")
    return compact.upper()


def validate_sequence(sequence: str) -> tuple[bool, str]:
    if not sequence:
        return False, "Sequence is empty after whitespace cleanup."
    if len(sequence) > MAX_SEQUENCE_LENGTH_AA:
        return (
            False,
            f"Sequence length {len(sequence)} exceeds the max of {MAX_SEQUENCE_LENGTH_AA} amino acids.",
        )
    if not AA_PATTERN.fullmatch(sequence):
        return (
            False,
            "Sequence includes invalid symbols. Allowed amino acids: ACDEFGHIKLMNPQRSTVWY.",
        )
    return True, ""


def validate_fasta_upload(file_bytes: bytes, filename: str) -> tuple[bool, str]:
    if not file_bytes:
        return False, "Uploaded FASTA file is empty."
    if len(file_bytes) > MAX_FASTA_UPLOAD_BYTES:
        return False, f"FASTA file exceeds the {MAX_FASTA_UPLOAD_MB} MB upload limit."
    try:
        fasta_text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False, "FASTA file must be valid UTF-8 text."
    if not fasta_text.strip():
        return False, "Uploaded FASTA file contains no sequence data."
    record_count = sum(1 for line in fasta_text.splitlines() if line.startswith(">"))
    if record_count == 0:
        return False, "FASTA file has no records (lines starting with '>')."
    if record_count > MAX_SEQUENCES_PER_REQUEST:
        return (
            False,
            f"FASTA has {record_count} sequences; max allowed is {MAX_SEQUENCES_PER_REQUEST}.",
        )
    return True, ""


def parse_verify_tls(raw: str | None, *, base_url: str) -> bool:
    """Parse GATEWAY_VERIFY_TLS; default from URL scheme when unset/empty."""
    if raw is None or not str(raw).strip():
        return base_url.startswith("https://")
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(
        "GATEWAY_VERIFY_TLS must be true/false (or 1/0, yes/no, on/off)."
    )


def load_gateway_config(
    *,
    base_url: str | None,
    username: str | None,
    password: str | None,
    verify_tls: str | None = None,
) -> tuple[GatewayConfig | None, str | None]:
    """Build gateway config from service env values (not user form input)."""
    cleaned_url = (base_url or "").strip()
    cleaned_user = (username or "").strip()
    cleaned_password = password or ""

    if not cleaned_url:
        return None, "GATEWAY_BASE_URL is required."
    if not cleaned_user or not cleaned_password:
        return None, "GATEWAY_USER and GATEWAY_USER_PASSWORD are required."

    try:
        tls = parse_verify_tls(verify_tls, base_url=cleaned_url)
    except ValueError as exc:
        return None, str(exc)

    return (
        GatewayConfig(
            base_url=cleaned_url.rstrip("/"),
            username=cleaned_user,
            password=cleaned_password,
            verify_tls=tls,
        ),
        None,
    )
