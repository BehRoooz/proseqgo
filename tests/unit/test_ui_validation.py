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


def test_validate_sequence_too_long(ui_validation) -> None:
    too_long = "A" * (ui_validation.MAX_SEQUENCE_LENGTH_AA + 1)
    ok, msg = ui_validation.validate_sequence(too_long)
    assert ok is False
    assert "exceeds" in msg.lower()


def test_validate_fasta_too_many_sequences(ui_validation) -> None:
    limit = ui_validation.MAX_SEQUENCES_PER_REQUEST
    records = "\n".join(f">p{i}\nACDE" for i in range(limit + 1)).encode()
    ok, msg = ui_validation.validate_fasta_upload(records, "many.fasta")
    assert ok is False
    assert "max allowed" in msg.lower()


def test_load_gateway_config_ok(ui_validation) -> None:
    cfg, err = ui_validation.load_gateway_config(
        base_url="http://nginx/",
        username="user",
        password="secret",
        verify_tls="false",
    )
    assert err is None
    assert cfg is not None
    assert cfg.base_url == "http://nginx"
    assert cfg.username == "user"
    assert cfg.password == "secret"
    assert cfg.verify_tls is False


def test_load_gateway_config_defaults_tls_from_https(ui_validation) -> None:
    cfg, err = ui_validation.load_gateway_config(
        base_url="https://nginx",
        username="user",
        password="secret",
    )
    assert err is None
    assert cfg is not None
    assert cfg.verify_tls is True


def test_load_gateway_config_missing_values(ui_validation) -> None:
    _, err = ui_validation.load_gateway_config(base_url=" ", username="u", password="p")
    assert err is not None
    _, err = ui_validation.load_gateway_config(base_url="http://gw", username="", password="p")
    assert err is not None
    _, err = ui_validation.load_gateway_config(base_url="http://gw", username="u", password="")
    assert err is not None


def test_load_gateway_config_invalid_verify_tls(ui_validation) -> None:
    _, err = ui_validation.load_gateway_config(
        base_url="http://gw",
        username="u",
        password="p",
        verify_tls="maybe",
    )
    assert err is not None
    assert "GATEWAY_VERIFY_TLS" in err
