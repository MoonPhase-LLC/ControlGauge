"""Minimal API error contract (S2-03).

Every data-route error body has one shape:

    {"error": {"code": "<fixed_code>", "message": "<fixed text>",
               "fields": [{"field": "name", "code": "too_long", "message": "..."}]}}

`code` values come from a small fixed set; `message` is fixed text chosen
by code. Neither ever contains request content: FastAPI's default 422 body
echoes the submitted input back (`input`), which this replaces. `fields` is
present only for validation errors.

Authentication failures are unchanged (`auth.py`: 401 `{"detail":
"Unauthorized"}`, decided before routing).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

_FIELD_MESSAGES = {
    "required": "This field is required.",
    "blank": "This field cannot be blank.",
    "too_long": "This value is too long.",
    "invalid_characters": "This value contains characters that are not allowed.",
    "invalid_type": "This value has the wrong type.",
    "unknown_field": "This field is not recognized.",
    "invalid": "This value is not valid.",
}

#: Pydantic error types mapped to the public field codes above. Custom
#: validators (`routes/assessments.py`) raise the public codes directly.
_PYDANTIC_TYPE_TO_CODE = {
    "missing": "required",
    "string_type": "invalid_type",
    "int_type": "invalid_type",
    "int_parsing": "invalid_type",
    "extra_forbidden": "unknown_field",
    "string_too_long": "too_long",
    "too_long": "too_long",
    "greater_than_equal": "invalid",
    "less_than_equal": "invalid",
    "model_type": "invalid_type",
    "dict_type": "invalid_type",
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        fields: Sequence[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = list(fields) if fields else None


def error_body(code: str, message: str, fields: list[dict[str, str]] | None = None) -> Any:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return {"error": error}


def _field_name(loc: Sequence[Any]) -> str:
    # loc is e.g. ("body", "name") or ("query", "limit"); drop the source.
    parts = [str(p) for p in loc[1:]] if len(loc) > 1 else [str(p) for p in loc]
    return ".".join(parts) or "body"


def _sanitize(errors: Sequence[Any]) -> tuple[str, list[dict[str, str]]]:
    fields: list[dict[str, str]] = []
    top_code = "validation_failed"
    for err in errors:
        err_type = str(err.get("type", ""))
        if err_type == "json_invalid":
            top_code = "malformed_body"
            continue
        if err_type in _FIELD_MESSAGES:
            code = err_type
        else:
            code = _PYDANTIC_TYPE_TO_CODE.get(err_type, "invalid")
        # An unknown field's name is client-supplied text: never echo it.
        field = "body" if code == "unknown_field" else _field_name(err.get("loc", ()))
        fields.append(
            {
                "field": field,
                "code": code,
                "message": _FIELD_MESSAGES[code],
            }
        )
    return top_code, fields


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.fields), status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        code, fields = _sanitize(exc.errors())
        if code == "malformed_body":
            return JSONResponse(
                error_body(code, "The request body is not valid JSON."), status_code=400
            )
        return JSONResponse(error_body(code, "The request is not valid.", fields), status_code=422)
