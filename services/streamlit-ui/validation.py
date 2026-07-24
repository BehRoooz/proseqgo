"""UI input validation helpers (no Streamlit dependency)."""

from __future__ import annotations

import re

AA_PATTERN = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$")
MAX_FASTA_UPLOAD_BYTES = 5 * 1024 * 1024  # must match embedding-api config + nginx route


def normalize_sequence(raw_sequence: str) -> str:
    compact = re.sub(r"\s+", "", raw_sequence or "")
    return compact.upper()


def validate_sequence(sequence: str) -> tuple[bool, str]:
    if not sequence:
        return False, "Sequence is empty after whitespace cleanup."
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
        max_mb = MAX_FASTA_UPLOAD_BYTES // (1024 * 1024)
        return False, f"FASTA file exceeds the {max_mb} MB upload limit."
    try:
        fasta_text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False, "FASTA file must be valid UTF-8 text."
    if not fasta_text.strip():
        return False, "Uploaded FASTA file contains no sequence data."
    record_count = sum(1 for line in fasta_text.splitlines() if line.startswith(">"))
    if record_count == 0:
        return False, "FASTA file has no records (lines starting with '>')."
    return True, ""


def validate_gateway_auth(gateway_base_url: str, username: str, password: str) -> str | None:
    if not gateway_base_url.strip():
        return "Gateway base URL is required."
    if not username.strip() or not password:
        return "Both API username and password are required."
    return None
