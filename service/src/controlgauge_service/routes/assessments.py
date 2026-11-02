"""Assessment create/list/get (S2-03).

Authenticated like every route (`auth.py` middleware; there is no opt-out).
An assessment must reference an existing framework. Explicit control
scoping (baseline/subset, `OPEN_QUESTIONS.md` P-1) and cloning (P-2) are
deferred by owner decision PD-006, so creation takes only a name, an
optional description, and the framework.

Input is validated strictly (`extra="forbid"`, strict strings) and errors
never echo it back (`routes/errors.py`).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import func, select

from ..db import Database
from ..db.models import (
    ASSESSMENT_DESCRIPTION_MAX,
    ASSESSMENT_NAME_MAX,
    Assessment,
    Framework,
)
from .deps import get_database
from .errors import ApiError

router = APIRouter()

LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 100

# C0/C1 control characters and Unicode line/paragraph separators. Tab and
# newline are allowed in the description only.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_DESCRIPTION_DISALLOWED = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


def _too_long() -> PydanticCustomError:
    return PydanticCustomError("too_long", "too long")


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr
    description: StrictStr | None = None
    framework_id: StrictStr

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("blank", "blank")
        if len(value) > ASSESSMENT_NAME_MAX:
            raise _too_long()
        if _CONTROL_CHARS.search(value):
            raise PydanticCustomError("invalid_characters", "invalid characters")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > ASSESSMENT_DESCRIPTION_MAX:
            raise _too_long()
        if _DESCRIPTION_DISALLOWED.search(value):
            raise PydanticCustomError("invalid_characters", "invalid characters")
        return value

    @field_validator("framework_id")
    @classmethod
    def _framework_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise PydanticCustomError("blank", "blank")
        if len(value) > 64:
            raise _too_long()
        return value


class FrameworkRef(BaseModel):
    id: str
    name: str
    version: str


class AssessmentOut(BaseModel):
    id: str
    name: str
    description: str | None
    framework: FrameworkRef
    created_at: datetime


class AssessmentList(BaseModel):
    assessments: list[AssessmentOut]
    total: int
    limit: int
    offset: int


def _to_out(assessment: Assessment, framework: Framework) -> AssessmentOut:
    return AssessmentOut(
        id=assessment.id,
        name=assessment.name,
        description=assessment.description,
        framework=FrameworkRef(id=framework.id, name=framework.name, version=framework.version),
        created_at=assessment.created_at,
    )


def _not_found() -> ApiError:
    return ApiError(404, "assessment_not_found", "The assessment was not found.")


DatabaseDep = Annotated[Database, Depends(get_database)]


@router.post("/assessments", status_code=201)
def create_assessment(body: AssessmentCreate, database: DatabaseDep) -> AssessmentOut:
    with database.session(write=True) as session:
        framework = session.get(Framework, body.framework_id)
        if framework is None:
            raise ApiError(
                422,
                "validation_failed",
                "The request is not valid.",
                [
                    {
                        "field": "framework_id",
                        "code": "framework_not_found",
                        "message": "The selected framework is not available.",
                    }
                ],
            )
        assessment = Assessment(
            id=str(uuid.uuid4()),
            framework_id=framework.id,
            name=body.name,
            description=body.description,
        )
        session.add(assessment)
        session.flush()
        return _to_out(assessment, framework)


@router.get("/assessments")
def list_assessments(
    database: DatabaseDep,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = LIST_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AssessmentList:
    with database.session() as session:
        total = session.scalar(select(func.count()).select_from(Assessment)) or 0
        rows: Any = session.execute(
            select(Assessment, Framework)
            .join(Framework, Assessment.framework_id == Framework.id)
            .order_by(Assessment.created_at.desc(), Assessment.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return AssessmentList(
            assessments=[_to_out(a, f) for a, f in rows],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: str, database: DatabaseDep) -> AssessmentOut:
    try:
        canonical = str(uuid.UUID(assessment_id))
    except ValueError:
        raise _not_found() from None
    if canonical != assessment_id:
        raise _not_found()
    with database.session() as session:
        row = session.execute(
            select(Assessment, Framework)
            .join(Framework, Assessment.framework_id == Framework.id)
            .where(Assessment.id == canonical)
        ).first()
        if row is None:
            raise _not_found()
        return _to_out(row[0], row[1])
