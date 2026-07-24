"""Protein sequence helpers shared by CLI embedding and APIs."""

from __future__ import annotations

import logging

logger = logging.getLogger("cafa5")

AA_ALPHABET: set[str] = set("ACDEFGHIKLMNPQRSTVWY")
AA_REMAP: dict[str, str] = {
    # common non-canonical amino acids -> unknown
    "U": "X",  # selenocysteine
    "O": "X",  # pyrrolysine
    "B": "X",  # aspartic acid or asparagine
    "Z": "X",  # glutamic acid or glutamine
    "J": "X",  # leucine or isoleucine
    "X": "X",  # unknown
}


def extract_protein_id(header_line: str) -> str:
    """Extract CAFA/UniProt-like EntryID from FASTA headers."""
    h = header_line.strip().lstrip(">")
    if "|" in h:
        parts = h.split("|")
        # Common UniProt format: sp|ENTRY|...
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return h.split()[0]


def normalize_sequence(seq: str) -> str:
    """Uppercase + remap rare tokens to `X` and validate characters."""
    seq = seq.strip().upper().replace(" ", "").replace("\n", "").replace("\t", "")
    remapped: list[str] = []
    invalid_count = 0
    for aa in seq:
        if aa in AA_ALPHABET:
            remapped.append(aa)
        elif aa in AA_REMAP:
            remapped.append(AA_REMAP[aa])
        else:
            invalid_count += 1
            remapped.append("X")
    if invalid_count:
        logger.warning("Remapped %d invalid amino acids to X", invalid_count)
    return "".join(remapped)


def format_for_tokenizer(seq: str, tokenizer_mode: str) -> str:
    """Transform sequence for the tokenizer (some models expect spaces)."""
    if tokenizer_mode == "space_separated":
        return " ".join(seq)
    return seq
