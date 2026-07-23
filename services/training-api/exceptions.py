from __future__ import annotations


class PermanentJobError(Exception):
    """Non-retryable job failure (bad input, subprocess exit, etc.)."""


class TransientJobError(Exception):
    """Retryable failure (infra blip, transient GPU/runtime error)."""
