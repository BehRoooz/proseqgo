from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
import streamlit as st
from go_metadata import enrich_prediction_rows, load_go_term_metadata
from requests.auth import HTTPBasicAuth
from requests.exceptions import RequestException

from validation import (
    MAX_FASTA_UPLOAD_MB,
    MAX_SEQUENCE_LENGTH_AA,
    MAX_SEQUENCES_PER_REQUEST,
    SYNC_PREDICT_TIMEOUT_SEC,
    GatewayConfig,
    load_gateway_config,
    normalize_sequence,
    validate_fasta_upload,
    validate_sequence,
)


PROJECT_TITLE = "ProSeqGO"
PROJECT_DESCRIPTION = (
    "ProSeqGO enables state-of-the-art prediction of Gene Ontology (GO) terms "
    "for protein sequences using transformer-based sequence embeddings and "
    "advanced machine learning models. By integrating recent advances in protein language models, "
    "ProSeqGO facilitates large-scale, automated functional annotation directly from sequence input, "
    "empowering researchers to infer protein function, explore biological mechanisms, "
    "and accelerate discovery in genomics and proteomics."
)
PREDICT_SEQUENCES_ENDPOINT = "/api/v1/predict-go-from-sequences"
PREDICT_FASTA_ENDPOINT = "/api/v1/predict-go-from-fasta"
MAX_TOP_K = 500
PREDICTION_MODE_SEQUENCE = "Prediction with sequence"
PREDICTION_MODE_FASTA = "Prediction with FASTA"


def parse_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code} returned empty body."
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail is not None:
            return str(detail)
    return str(payload)


def build_request_payload(sequence: str, top_k: int) -> dict[str, Any]:
    return {
        "backend": "esm2",
        "pooling": "mean",
        "batch_size": 1,
        "max_length": MAX_SEQUENCE_LENGTH_AA,
        "top_k": top_k,
        "timeout_seconds": SYNC_PREDICT_TIMEOUT_SEC,
        "sequences": [{"id": "input_1", "sequence": sequence}],
    }


def call_prediction_api(
    gateway: GatewayConfig,
    sequence: str,
    top_k: int,
    timeout_seconds: int,
) -> tuple[bool, dict[str, Any] | str]:
    payload = build_request_payload(sequence=sequence, top_k=top_k)
    endpoint = f"{gateway.base_url}{PREDICT_SEQUENCES_ENDPOINT}"

    try:
        response = requests.post(
            endpoint,
            json=payload,
            auth=HTTPBasicAuth(gateway.username, gateway.password),
            verify=gateway.verify_tls,
            timeout=timeout_seconds,
        )
    except RequestException as exc:
        return False, f"Request failed before receiving a response: {exc}"

    if response.status_code != 200:
        return False, f"API returned HTTP {response.status_code}: {parse_error_message(response)}"

    try:
        return True, response.json()
    except ValueError as exc:
        return False, f"API returned non-JSON success response: {exc}"


def call_fasta_prediction_api(
    gateway: GatewayConfig,
    file_bytes: bytes,
    filename: str,
    top_k: int,
    timeout_seconds: int,
) -> tuple[bool, dict[str, Any] | str]:
    endpoint = f"{gateway.base_url}{PREDICT_FASTA_ENDPOINT}"
    files = {"fasta_file": (filename or "upload.fasta", file_bytes, "application/octet-stream")}
    data = {
        "backend": "esm2",
        "pooling": "mean",
        "batch_size": "8",
        "max_length": str(MAX_SEQUENCE_LENGTH_AA),
        "top_k": str(top_k),
        "fail_fast": "true",
        "timeout_seconds": str(timeout_seconds),
        "poll_interval_seconds": "1.0",
    }

    try:
        response = requests.post(
            endpoint,
            files=files,
            data=data,
            auth=HTTPBasicAuth(gateway.username, gateway.password),
            verify=gateway.verify_tls,
            timeout=timeout_seconds,
        )
    except RequestException as exc:
        return False, f"Request failed before receiving a response: {exc}"

    if response.status_code != 200:
        return False, f"API returned HTTP {response.status_code}: {parse_error_message(response)}"

    try:
        return True, response.json()
    except ValueError as exc:
        return False, f"API returned non-JSON success response: {exc}"


def render_predictions(payload: dict[str, Any]) -> None:
    st.success("Prediction request completed successfully.")
    model_version = payload.get("model_version") or "unknown"
    st.caption(f"Model version: `{model_version}`")

    metadata: pd.DataFrame | None = None
    metadata_error: str | None = None
    try:
        metadata = load_go_term_metadata()
    except (FileNotFoundError, ValueError) as exc:
        metadata_error = str(exc)

    results = payload.get("results", [])
    if not results:
        st.warning("No prediction rows were returned.")
        return

    if metadata_error:
        st.warning(
            "GO term metadata is unavailable. Showing GO IDs only.\n\n"
            f"{metadata_error}"
        )

    for result in results:
        sequence_id = result.get("sequence_id", "unknown")
        st.markdown(f"#### Sequence `{sequence_id}`")
        predictions = result.get("predictions", [])
        if not predictions:
            st.info("No GO terms returned for this sequence.")
            continue
        if metadata is not None:
            table = enrich_prediction_rows(predictions=predictions, metadata=metadata)
        else:
            table = pd.DataFrame(predictions)
        table = table.rename(
            columns={
                "go_term": "GO Term",
                "score": "Score",
                "name": "GO Name",
                "namespace": "Namespace",
                "def": "Definition",
            }
        )
        st.dataframe(table, use_container_width=True, hide_index=True)

    failures = payload.get("failures", [])
    if failures:
        st.warning("Some sequence predictions failed.")
        st.json(failures)


def main() -> None:
    st.set_page_config(page_title="ProSeqGO", page_icon="🧬", layout="wide")
    st.title("🧬 ProSeqGO: Protein Sequence to Gene Ontology Prediction")
    st.write(PROJECT_DESCRIPTION)

    gateway, config_error = load_gateway_config(
        base_url=os.getenv("GATEWAY_BASE_URL"),
        username=os.getenv("GATEWAY_USER"),
        password=os.getenv("GATEWAY_USER_PASSWORD"),
        verify_tls=os.getenv("GATEWAY_VERIFY_TLS"),
    )
    if config_error or gateway is None:
        st.error(
            f"Streamlit UI is misconfigured: {config_error} "
            "Set GATEWAY_BASE_URL, GATEWAY_USER, and GATEWAY_USER_PASSWORD "
            "(optional GATEWAY_VERIFY_TLS) in the service environment."
        )
        return

    prediction_mode = st.radio(
        "Input mode",
        options=[PREDICTION_MODE_SEQUENCE, PREDICTION_MODE_FASTA],
        horizontal=True,
    )

    if prediction_mode == PREDICTION_MODE_SEQUENCE:
        with st.form("predict_sequence_form"):
            sequence_text = st.text_area(
                "Protein sequence",
                height=180,
                placeholder="Paste a single protein sequence (FASTA header excluded).",
            )
            top_k = st.number_input("top_k", min_value=1, max_value=MAX_TOP_K, value=10, step=1)
            submit = st.form_submit_button("Run prediction")

        if not submit:
            return

        cleaned = normalize_sequence(sequence_text)
        is_valid, message = validate_sequence(cleaned)
        if not is_valid:
            st.error(message)
            return

        with st.spinner(f"Submitting request to {PREDICT_SEQUENCES_ENDPOINT} ..."):
            ok, result = call_prediction_api(
                gateway=gateway,
                sequence=cleaned,
                top_k=int(top_k),
                timeout_seconds=SYNC_PREDICT_TIMEOUT_SEC,
            )
    else:
        with st.form("predict_fasta_form"):
            fasta_file = st.file_uploader(
                "Protein FASTA file",
                type=["fasta", "fa", "txt"],
                help=(
                    f"Upload a UTF-8 FASTA file (max {MAX_FASTA_UPLOAD_MB} MB, "
                    f"up to {MAX_SEQUENCES_PER_REQUEST} sequences, "
                    f"each <= {MAX_SEQUENCE_LENGTH_AA} aa). "
                    "Non-canonical residues are normalized by the API at embedding time."
                ),
            )
            top_k = st.number_input("top_k", min_value=1, max_value=MAX_TOP_K, value=10, step=1)
            submit = st.form_submit_button("Run prediction")

        if not submit:
            return

        if fasta_file is None:
            st.error("Select a FASTA file before running prediction.")
            return

        file_bytes = fasta_file.getvalue()
        is_valid, message = validate_fasta_upload(file_bytes, fasta_file.name)
        if not is_valid:
            st.error(message)
            return

        record_count = sum(
            1 for line in file_bytes.decode("utf-8").splitlines() if line.startswith(">")
        )
        if record_count > 1:
            st.info(
                f"This FASTA contains {record_count} sequences "
                f"(limit {MAX_SEQUENCES_PER_REQUEST}). "
                "Larger files may take several minutes to complete."
            )

        with st.spinner(f"Submitting request to {PREDICT_FASTA_ENDPOINT} ..."):
            ok, result = call_fasta_prediction_api(
                gateway=gateway,
                file_bytes=file_bytes,
                filename=fasta_file.name,
                top_k=int(top_k),
                timeout_seconds=SYNC_PREDICT_TIMEOUT_SEC,
            )

    if not ok:
        st.error(str(result))
        return
    render_predictions(result if isinstance(result, dict) else {})


if __name__ == "__main__":
    main()
