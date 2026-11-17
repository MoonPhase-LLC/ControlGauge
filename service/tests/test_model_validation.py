"""S5-02: the deterministic-validation schema matrix.

These tests draw the line the Sprint 5 evidence depends on:
**sampler-enforced conformance** (the grammar/``response_format`` makes
the model emit schema-conforming JSON -- re-proven for real in S5-03's
integration tests) is distinct from **deterministic validation** (this
file: the parsed output is checked against the schema with the standard
library-grade validator before anything is treated as usable).

The representative schemas mirror ``AI_PIPELINE.md`` §7 *shape only* --
no compliance fields, no persistence.
"""

from __future__ import annotations

from typing import Any

import pytest

from controlgauge_service.model_runtime.errors import ModelRuntimeError
from controlgauge_service.model_runtime.validation import validate_structured_output

FLAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["control_id", "confidence"],
    "properties": {
        "control_id": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "additionalProperties": False,
}

NESTED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["assessment"],
    "properties": {
        "assessment": {
            "type": "object",
            "required": ["control_id", "observations"],
            "properties": {
                "control_id": {"type": "string"},
                "observations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["note"],
                        "properties": {"note": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}

#: The V0.1 `relationship_type` enum shape (AI_PIPELINE §7 / §8).
ENUM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["relationship_type", "confidence"],
    "properties": {
        "relationship_type": {
            "type": "string",
            "enum": ["SUPPORTS", "PARTIALLY_SUPPORTS", "REFERENCES", "CONFLICTS_WITH"],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "additionalProperties": False,
}


def test_schema_matrix_nested_object_accepts() -> None:
    validate_structured_output(
        {
            "assessment": {
                "control_id": "AC-2",
                "observations": [{"note": "a"}, {"note": "b"}],
            }
        },
        NESTED_SCHEMA,
    )


def test_schema_matrix_missing_required_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        validate_structured_output({"confidence": 0.5}, FLAT_SCHEMA)

    assert excinfo.value.code == "schema_validation_failed"


def test_schema_matrix_enum_violation_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        validate_structured_output(
            {"relationship_type": "MOSTLY_SUPPORTS", "confidence": 0.5}, ENUM_SCHEMA
        )

    assert excinfo.value.code == "schema_validation_failed"


def test_schema_matrix_wrong_type_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        validate_structured_output({"control_id": 42, "confidence": 0.5}, FLAT_SCHEMA)

    assert excinfo.value.code == "schema_validation_failed"


def test_schema_matrix_additional_properties_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        validate_structured_output(
            {"control_id": "AC-2", "confidence": 0.5, "extra": True}, FLAT_SCHEMA
        )

    assert excinfo.value.code == "schema_validation_failed"


def test_schema_matrix_wrong_container_rejected() -> None:
    """A JSON array (not an object) is not schema-conforming output."""
    with pytest.raises(ModelRuntimeError) as excinfo:
        validate_structured_output([{"control_id": "AC-2"}], FLAT_SCHEMA)

    assert excinfo.value.code == "schema_validation_failed"


def test_confidence_range_violation_rejected() -> None:
    """`confidence` outside [0.0, 1.0] violates the AI_PIPELINE §8 shape."""
    for confidence in (-0.1, 1.5):
        with pytest.raises(ModelRuntimeError) as excinfo:
            validate_structured_output(
                {"relationship_type": "SUPPORTS", "confidence": confidence}, ENUM_SCHEMA
            )

        assert excinfo.value.code == "schema_validation_failed"


def test_confidence_boundaries_accepted() -> None:
    for confidence in (0.0, 1.0):
        validate_structured_output(
            {"relationship_type": "REFERENCES", "confidence": confidence}, ENUM_SCHEMA
        )


def test_valid_output_passes() -> None:
    validate_structured_output({"relationship_type": "SUPPORTS", "confidence": 0.91}, ENUM_SCHEMA)
    validate_structured_output({"control_id": "AC-2", "confidence": 0.5}, FLAT_SCHEMA)


def test_validation_never_mutates_the_input() -> None:
    data = {"control_id": "AC-2", "confidence": 0.5}
    snapshot = dict(data)

    validate_structured_output(data, FLAT_SCHEMA)

    assert data == snapshot
