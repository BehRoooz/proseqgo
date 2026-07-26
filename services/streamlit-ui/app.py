from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
import streamlit as st
from requests.auth import HTTPBasicAuth
from requests.exceptions import RequestException

from validation import (
    MAX_FASTA_UPLOAD_BYTES,
    normalize_sequence,
    validate_fasta_upload,
    validate_gateway_auth,
    validate_sequence,
)


PROJECT_TITLE = "ProSeqGO"
PROJECT_DESCRIPTION = (
    "Interactive sequence-to-GO inference UI backed by the embedding and GO "
    "prediction APIs through the NGINX gateway."
)
DEFAULT_GATEWAY_URL = os.getenv("GATEWAY_BASE_URL", "http://localhost")
# Public predict-route defaults (must match nginx/.htpasswd-user from make gateway-auth).
DEFAULT_API_USERNAME = os.getenv("GATEWAY_USER", "")
DEFAULT_API_PASSWORD = os.getenv("GATEWAY_USER_PASSWORD", "")
PREDICT_SEQUENCES_ENDPOINT = "/api/v1/predict-go-from-sequences"
PREDICT_FASTA_ENDPOINT = "/api/v1/predict-go-from-fasta"
MAX_TOP_K = 500
SEQUENCE_TIMEOUT_SECONDS = 600
FASTA_TIMEOUT_SECONDS = 1800
PREDICTION_MODE_SEQUENCE = "Prediction with sequence"
PREDICTION_MODE_FASTA = "Prediction with FASTA"
WORKFLOW_DOT = """
digraph cafa5 {
    rankdir=LR;
    node [shape=box, style=rounded];
    user [label="User Browser"];
    ui [label="Streamlit UI (/ui/)"];
    gw [label="NGINX Gateway"];
    api [label="Embedding API"];
    pred [label="GO Prediction API"];

    user -> ui;
    ui -> gw [label="Basic Auth"];
    gw -> api [label="predict-go-from-sequences\\nor predict-go-from-fasta"];
    api -> pred [label="predict()"];
    pred -> api;
    api -> ui;
    ui -> user;
}
""".strip()


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
        "max_length": 1280,
        "top_k": top_k,
        "sequences": [{"id": "input_1", "sequence": sequence}],
    }


def call_prediction_api(
    gateway_base_url: str,
    username: str,
    password: str,
    sequence: str,
    top_k: int,
    verify_tls: bool,
    timeout_seconds: int,
) -> tuple[bool, dict[str, Any] | str]:
    payload = build_request_payload(sequence=sequence, top_k=top_k)
    endpoint = f"{gateway_base_url.rstrip('/')}{PREDICT_SEQUENCES_ENDPOINT}"

    try:
        response = requests.post(
            endpoint,
            json=payload,
            auth=HTTPBasicAuth(username, password),
            verify=verify_tls,
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
    gateway_base_url: str,
    username: str,
    password: str,
    file_bytes: bytes,
    filename: str,
    top_k: int,
    verify_tls: bool,
    timeout_seconds: int,
) -> tuple[bool, dict[str, Any] | str]:
    endpoint = f"{gateway_base_url.rstrip('/')}{PREDICT_FASTA_ENDPOINT}"
    files = {"fasta_file": (filename or "upload.fasta", file_bytes, "application/octet-stream")}
    data = {
        "backend": "esm2",
        "pooling": "mean",
        "batch_size": "8",
        "max_length": "1280",
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
            auth=HTTPBasicAuth(username, password),
            verify=verify_tls,
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

    results = payload.get("results", [])
    if not results:
        st.warning("No prediction rows were returned.")
        return

    for result in results:
        sequence_id = result.get("sequence_id", "unknown")
        st.markdown(f"#### Sequence `{sequence_id}`")
        predictions = result.get("predictions", [])
        if not predictions:
            st.info("No GO terms returned for this sequence.")
            continue
        table = pd.DataFrame(predictions)
        table = table.rename(columns={"go_term": "GO Term", "score": "Score"})
        st.dataframe(table, use_container_width=True, hide_index=True)

    failures = payload.get("failures", [])
    if failures:
        st.warning("Some sequence predictions failed.")
        st.json(failures)


def _render_shared_connection_fields(
    gateway_base_url: str,
    verify_tls_default: bool,
) -> tuple[str, str, str, bool, int]:
    top_k = st.number_input("top_k", min_value=1, max_value=MAX_TOP_K, value=10, step=1)
    gateway_base_url_input = st.text_input("Gateway base URL", value=gateway_base_url)
    username = st.text_input("API username", value=DEFAULT_API_USERNAME)
    password = st.text_input("API password", type="password", value=DEFAULT_API_PASSWORD)
    verify_tls = st.checkbox("Verify TLS", value=verify_tls_default)
    return gateway_base_url_input, username, password, verify_tls, int(top_k)


def main() -> None:
    st.set_page_config(page_title="ProSeqGO", page_icon="🧬", layout="wide")
    st.title("🧬 ProSeqGO: Protein Sequence to GO Prediction")
    st.write(PROJECT_DESCRIPTION)

    st.subheader("Platform Links")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("- [MLflow](http://localhost/mlflow/)")
    with c2:
        st.markdown("- [Prometheus](http://localhost:9090)")
    with c3:
        st.markdown("- [Grafana](http://localhost:3000)")

    st.subheader("Workflow")
    st.graphviz_chart(WORKFLOW_DOT, use_container_width=True)

    st.subheader("Predict GO Terms")
    prediction_mode = st.radio(
        "Input mode",
        options=[PREDICTION_MODE_SEQUENCE, PREDICTION_MODE_FASTA],
        horizontal=True,
    )
    verify_tls_default = DEFAULT_GATEWAY_URL.startswith("https://")

    if prediction_mode == PREDICTION_MODE_SEQUENCE:
        with st.form("predict_sequence_form"):
            sequence_text = st.text_area(
                "Protein sequence",
                height=180,
                placeholder="Paste a single protein sequence (FASTA header excluded).",
            )
            gateway_base_url, username, password, verify_tls, top_k = _render_shared_connection_fields(
                DEFAULT_GATEWAY_URL,
                verify_tls_default,
            )
            submit = st.form_submit_button("Run prediction")

        if not submit:
            return

        cleaned = normalize_sequence(sequence_text)
        is_valid, message = validate_sequence(cleaned)
        if not is_valid:
            st.error(message)
            return
        auth_error = validate_gateway_auth(gateway_base_url, username, password)
        if auth_error:
            st.error(auth_error)
            return

        with st.spinner(f"Submitting request to {PREDICT_SEQUENCES_ENDPOINT} ..."):
            ok, result = call_prediction_api(
                gateway_base_url=gateway_base_url.strip(),
                username=username.strip(),
                password=password,
                sequence=cleaned,
                top_k=top_k,
                verify_tls=verify_tls,
                timeout_seconds=SEQUENCE_TIMEOUT_SECONDS,
            )
    else:
        with st.form("predict_fasta_form"):
            fasta_file = st.file_uploader(
                "Protein FASTA file",
                type=["fasta", "fa", "txt"],
                help=(
                    f"Upload a UTF-8 FASTA file (max {MAX_FASTA_UPLOAD_BYTES // (1024 * 1024)} MB). "
                    "All records in the file are embedded and predicted. "
                    "Non-canonical residues are normalized by the API at embedding time."
                ),
            )
            gateway_base_url, username, password, verify_tls, top_k = _render_shared_connection_fields(
                DEFAULT_GATEWAY_URL,
                verify_tls_default,
            )
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
        auth_error = validate_gateway_auth(gateway_base_url, username, password)
        if auth_error:
            st.error(auth_error)
            return

        record_count = sum(
            1 for line in file_bytes.decode("utf-8").splitlines() if line.startswith(">")
        )
        if record_count > 1:
            st.info(
                f"This FASTA contains {record_count} sequences. "
                "Large files may take several minutes to complete."
            )

        with st.spinner(f"Submitting request to {PREDICT_FASTA_ENDPOINT} ..."):
            ok, result = call_fasta_prediction_api(
                gateway_base_url=gateway_base_url.strip(),
                username=username.strip(),
                password=password,
                file_bytes=file_bytes,
                filename=fasta_file.name,
                top_k=top_k,
                verify_tls=verify_tls,
                timeout_seconds=FASTA_TIMEOUT_SECONDS,
            )

    if not ok:
        st.error(str(result))
        return
    render_predictions(result if isinstance(result, dict) else {})


if __name__ == "__main__":
    main()
