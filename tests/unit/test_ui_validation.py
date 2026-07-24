"""Tests for Streamlit UI input validation (product gate before API calls)."""

from __future__ import annotations


def test_ui_normalize_sequence(ui_validation) -> None:
    assert ui_validation.normalize_sequence(" mk\nta ") == "MKTA"


def test_validate_sequence_ok(ui_validation) -> None:
    ok, msg = ui_validation.validate_sequence("ACDEFG")
    assert ok is True
    assert msg == ""


def test_validate_sequence_empty(ui_validation) -> None:
    ok, msg = ui_validation.validate_sequence("")
    assert ok is False
    assert "empty" in msg.lower()


def test_validate_sequence_rejects_noncanonical(ui_validation) -> None:
    # UI is stricter than embedding normalize (no U/O remap at the gate).
    ok, msg = ui_validation.validate_sequence("ACDEU")
    assert ok is False
    assert "invalid" in msg.lower()


def test_validate_fasta_upload_ok(ui_validation) -> None:
    fasta = b">p1\nMKTAY\n>p2\nACDE\n"
    ok, msg = ui_validation.validate_fasta_upload(fasta, "tiny.fasta")
    assert ok is True
    assert msg == ""


def test_validate_fasta_upload_no_records(ui_validation) -> None:
    ok, msg = ui_validation.validate_fasta_upload(b"MKTAY\n", "bad.fasta")
    assert ok is False
    assert "no records" in msg.lower()


def test_validate_fasta_upload_empty(ui_validation) -> None:
    ok, msg = ui_validation.validate_fasta_upload(b"", "empty.fasta")
    assert ok is False
    assert "empty" in msg.lower()


def test_validate_fasta_upload_too_large(ui_validation) -> None:
    huge = b">p1\n" + (b"A" * (ui_validation.MAX_FASTA_UPLOAD_BYTES + 1))
    ok, msg = ui_validation.validate_fasta_upload(huge, "big.fasta")
    assert ok is False
    assert "exceeds" in msg.lower()


def test_validate_gateway_auth(ui_validation) -> None:
    assert ui_validation.validate_gateway_auth("http://gw", "u", "p") is None
    assert ui_validation.validate_gateway_auth("  ", "u", "p") is not None
    assert ui_validation.validate_gateway_auth("http://gw", "", "p") is not None
    assert ui_validation.validate_gateway_auth("http://gw", "u", "") is not None
