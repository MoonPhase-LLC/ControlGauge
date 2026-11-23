"""Fixed error taxonomy for the model runtime (S5-02).

Every failure the runtime surfaces carries one ``code`` from
``MODEL_RUNTIME_ERROR_CODES`` and a **fixed message chosen by code** --
never exception text, a model path, a port, or model-server response
content (T-10 discipline: the model path and the credential must never
travel in errors or logs; response content is untrusted model output).
Each code maps 1:1 to the documented error union in the Sprint 5 spec;
callers branch on ``exc.code``.
"""

from __future__ import annotations

#: Sprint 6 (S6-04) download-surface codes. ``download_cancelled`` is a
#: terminal STATE in a poll response, deliberately not an error code here.
DOWNLOAD_ERROR_CODES = (
    "download_active",
    "download_not_found",
    "download_checksum_mismatch",
    "download_size_mismatch",
    "download_network_error",
    "download_http_error",
    "download_url_unsupported",
    "disk_write_failed",
    "disk_space_insufficient",
    "expected_checksum_mismatch",
    "expected_checksum_required",
)

#: Sprint 6 (S6-03/S6-04) curated-catalog codes.
CATALOG_ERROR_CODES = (
    "catalog_entry_unknown",
    "catalog_unavailable",
)

#: Sprint 6 (S6-05) user-managed registry + external-endpoint codes.
REGISTRY_ERROR_CODES = (
    "registry_endpoint_invalid",
    "registry_endpoint_not_loopback",
    "registry_endpoint_unreachable",
    "registry_duplicate",
    "endpoint_not_found",
    "endpoint_attached",
)

#: Every Sprint 6 code (the batch-by-batch union; S6-05 adds the registry
#: surface).
SPRINT6_ERROR_CODES = DOWNLOAD_ERROR_CODES + CATALOG_ERROR_CODES + REGISTRY_ERROR_CODES

MODEL_RUNTIME_ERROR_CODES = (
    "runtime_binary_missing",
    "model_file_missing",
    "start_failed",
    "start_timeout",
    "child_exited",
    "request_timeout",
    "transport_error",
    "auth_rejected",
    "invalid_request",
    "server_error",
    "invalid_output",
    "schema_validation_failed",
    "embedding_dimension_mismatch",
    "non_finite_embedding",
    "input_too_long",
    "not_loaded",
    # S5-05 import-path codes: untrusted GGUF handling. All carry fixed
    # messages -- never the path, the filename, or any file content (T-10).
    "model_path_invalid",
    "model_file_not_found",
    "model_file_not_regular",
    "unsupported_file_type",
    "model_file_too_small",
    "model_file_too_large",
    "invalid_gguf_magic",
    "invalid_gguf_version",
    # Sprint 6: catalog + download surfaces (S6-03/S6-04). All carry fixed
    # messages and never a path, URL, or filename (T-10).
    *SPRINT6_ERROR_CODES,
)

_MESSAGES: dict[str, str] = {
    "runtime_binary_missing": "The model runtime binary is not configured or does not exist.",
    "model_file_missing": "The model file does not exist.",
    "start_failed": "The model server failed to start.",
    "start_timeout": "The model server did not become ready in time.",
    "child_exited": "The model server process has exited.",
    "request_timeout": "The model server did not respond in time.",
    "transport_error": "The model server could not be reached.",
    "auth_rejected": "The model server rejected the credential.",
    "invalid_request": "The model server rejected the request.",
    "server_error": "The model server reported an internal error.",
    "invalid_output": "The model response could not be parsed.",
    "schema_validation_failed": "The model response does not match the required schema.",
    "embedding_dimension_mismatch": "The embedding dimension does not match the expected value.",
    "non_finite_embedding": "The embedding contains non-finite values.",
    "input_too_long": "The input exceeds the maximum supported length.",
    "not_loaded": "The model runtime is not loaded.",
    "model_path_invalid": "The model file path is not valid.",
    "model_file_not_found": "The model file was not found.",
    "model_file_not_regular": "The model path is not a regular file.",
    "unsupported_file_type": "Only GGUF model files can be imported.",
    "model_file_too_small": "The file is too small to be a model file.",
    "model_file_too_large": "The file is larger than the supported model size limit.",
    "invalid_gguf_magic": "The file does not look like a GGUF model file.",
    "invalid_gguf_version": "The GGUF file version is not supported.",
    # Sprint 6 catalog + download surfaces (S6-03/S6-04).
    "catalog_unavailable": "The model catalog is not available.",
    "catalog_entry_unknown": "The catalog entry was not found.",
    "download_active": "A download is already in progress.",
    "download_not_found": "The download was not found.",
    "download_checksum_mismatch": "The downloaded file failed checksum verification.",
    "download_size_mismatch": "The downloaded file size does not match the expected size.",
    "download_network_error": "The download failed due to a network error.",
    "download_http_error": "The download server reported an error.",
    "download_url_unsupported": "The download URL is not supported.",
    "disk_write_failed": "Writing the downloaded file failed.",
    "disk_space_insufficient": "There is not enough disk space for the download.",
    "expected_checksum_mismatch": "The file's checksum does not match the expected value.",
    "expected_checksum_required": ("A checksum or explicit unverified acknowledgment is required."),
    # Sprint 6 registry + external-endpoint surfaces (S6-05).
    "registry_endpoint_invalid": "The endpoint address is not valid.",
    "registry_endpoint_not_loopback": "Only loopback endpoints can be registered.",
    "registry_endpoint_unreachable": "The endpoint did not answer its health probe.",
    "registry_duplicate": "An identical endpoint is already registered.",
    "endpoint_not_found": "The registered endpoint was not found.",
    "endpoint_attached": "The endpoint is attached to a running model role.",
}


class ModelRuntimeError(Exception):
    """One runtime failure. ``code`` is always a member of
    ``MODEL_RUNTIME_ERROR_CODES`` and ``str(exc)`` is the fixed message
    for that code -- nothing else is ever attached."""

    def __init__(self, code: str) -> None:
        if code not in _MESSAGES:
            raise ValueError(f"unknown model runtime error code: {code!r}")
        self.code = code
        super().__init__(_MESSAGES[code])
