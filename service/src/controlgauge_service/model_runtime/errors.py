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
    # Sprint 6 (S6-03): the curated catalog manifest failed to load
    # (missing, oversized, malformed, or failing strict validation) -- the
    # catalog is unavailable, never partially loaded. The remaining
    # Sprint 6 codes assemble into DOWNLOAD_ERROR_CODES / CATALOG_ERROR_CODES
    # / REGISTRY_ERROR_CODES as S6-04/S6-05 build their surfaces.
    "catalog_unavailable",
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
    "catalog_unavailable": "The model catalog is not available.",
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
