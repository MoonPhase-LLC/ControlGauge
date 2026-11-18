"""Deterministic validation of model output against a JSON schema
(S5-02; ``AI_PIPELINE.md`` §8's schema-conformance step).

Sampler-constrained generation (grammar/``response_format``) *reduces*
off-schema output but is never trusted: before anything is treated as
usable data, the parsed JSON object is validated here with the standard
``jsonschema`` library -- deterministic, no LLM judgment involved. Any
violation raises ``ModelRuntimeError("schema_validation_failed")`` with
the fixed message; the specific violation (and any model-output content)
is deliberately not attached -- callers log the outcome code only (T-10).

Sprint 5 writes no analysis tables at all; this module validates and
returns -- persistence is the pipeline's concern, never the provider's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import jsonschema

from .errors import ModelRuntimeError


def validate_structured_output(data: object, schema: Mapping[str, Any]) -> None:
    """Raises ``schema_validation_failed`` unless ``data`` validates
    against ``schema`` (JSON Schema draft 2020-12, chosen explicitly so
    behavior never depends on a ``$schema`` key the model could
    influence)."""
    validator = jsonschema.Draft202012Validator(schema)
    if list(validator.iter_errors(data)):
        raise ModelRuntimeError("schema_validation_failed")
