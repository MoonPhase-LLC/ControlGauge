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
