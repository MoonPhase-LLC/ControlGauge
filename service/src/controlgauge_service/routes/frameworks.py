"""GET /frameworks (S2-03): frameworks an assessment can target.

Metadata only. `control_count` is the number of controls loaded for the
framework -- 0 until the catalog is imported (Sprint 3) -- so clients can
say so plainly instead of implying a populated catalog.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from ..db import Database
from ..db.models import Control, Framework
from .deps import get_database

router = APIRouter()


class FrameworkOut(BaseModel):
    id: str
    name: str
    version: str
    source_reference: str | None
    control_count: int


class FrameworkList(BaseModel):
    frameworks: list[FrameworkOut]


@router.get("/frameworks")
def list_frameworks(database: Annotated[Database, Depends(get_database)]) -> FrameworkList:
    counts = (
        select(Control.framework_id, func.count().label("n"))
        .group_by(Control.framework_id)
        .subquery()
    )
    with database.session() as session:
        rows: Any = session.execute(
            select(Framework, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.framework_id == Framework.id)
            .order_by(Framework.name, Framework.version)
        ).all()
        return FrameworkList(
            frameworks=[
                FrameworkOut(
                    id=f.id,
                    name=f.name,
                    version=f.version,
                    source_reference=f.source_reference,
                    control_count=int(n),
                )
                for f, n in rows
            ]
        )
