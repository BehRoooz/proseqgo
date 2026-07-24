"""Tests for shared protein sequence helpers."""

from __future__ import annotations

import pytest

from src.preprocess.sequences import (
    extract_protein_id,
    format_for_tokenizer,
    normalize_sequence,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mktay", "MKTAY"),
        ("  MK TA\nY  ", "MKTAY"),
        ("ACDEFG", "ACDEFG"),
        ("ACDEUB", "ACDEXX"),  # U,B remapped
        ("ACDE1Z", "ACDEXX"),  # digit + Z remapped
    ],
)
def test_normalize_sequence(raw: str, expected: str) -> None:
    assert normalize_sequence(raw) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (">sp|P12345|GENE_HUMAN description", "P12345"),
        (">tr|A0A000|NAME", "A0A000"),
        (">SIMPLE_ID rest of header", "SIMPLE_ID"),
        ("P99999 no_gt", "P99999"),
    ],
)
def test_extract_protein_id(header: str, expected: str) -> None:
    assert extract_protein_id(header) == expected


def test_format_for_tokenizer_space_separated() -> None:
    assert format_for_tokenizer("ACDE", "space_separated") == "A C D E"


def test_format_for_tokenizer_passthrough() -> None:
    assert format_for_tokenizer("ACDE", "esm") == "ACDE"
