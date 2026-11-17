"""Representative structured-output schemas for the Sprint 5 tests (S5-03).

**Test data, not model data**: these constants mirror ``AI_PIPELINE.md``
§7 *shape only* -- no compliance fields, no persistence path ever reads
them. They exercise the three JSON-Schema features the V0.1 pipeline
needs from schema-constrained sampling: a flat object, a nested object
(object with an array of objects), and the V0.1 ``relationship_type``
enum with its confidence range (§8). The real per-call schemas arrive
from the pipeline in later sprints; these exist so the sampler-
enforcement evidence is captured against stable, readable shapes.
"""

from __future__ import annotations

from typing import Any

SIMPLE_FLAT_SCHEMA: dict[str, Any] = {
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

#: The V0.1 ``relationship_type`` enum shape (AI_PIPELINE §7 / §8).
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

#: ``ENUM_SCHEMA`` minus the numeric range -- the subset llama.cpp b12480's
#: ``json_schema`` grammar can fully enforce (observed live, 2026-11-17:
#: the grammar constrains the *shape* of a number but not its
#: minimum/maximum, so a free-invented ``0.95…E1000000000000000`` parses to
#: ``inf`` and passes the grammar while violating the range). Range
#: conformance is deterministic validation's job (B2), which catches
#: exactly that case with ``schema_validation_failed``. Used by the N=5
#: sampler-enforcement test so its "always conformant" claim is one the
#: grammar can actually guarantee.
ENUM_SHAPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["relationship_type", "confidence"],
    "properties": {
        "relationship_type": {
            "type": "string",
            "enum": ["SUPPORTS", "PARTIALLY_SUPPORTS", "REFERENCES", "CONFLICTS_WITH"],
        },
        "confidence": {"type": "number"},
    },
    "additionalProperties": False,
}
