"""GET /frameworks: frameworks an assessment can target.

`current_release` is the catalog release new assessments are pinned to
(null while no catalog is imported) and `control_count` is the number of
controls in it, so clients can say plainly when no catalog is loaded.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from ..db import Database
from ..db.models import CatalogRelease, Control, Framework
from .deps import get_database

router = APIRouter()


class ReleaseSummary(BaseModel):
    id: str
    version: str
    title: str


class FrameworkOut(BaseModel):
    id: str
    name: str
    version: str
    source_reference: str | None
    control_count: int
    current_release: ReleaseSummary | None


class FrameworkList(BaseModel):
    frameworks: list[FrameworkOut]


@router.get("/frameworks")
def list_frameworks(database: Annotated[Database, Depends(get_database)]) -> FrameworkList:
    counts = (
        select(Control.catalog_release_id, func.count().label("n"))
        .group_by(Control.catalog_release_id)
        .subquery()
    )
    with database.session() as session:
        rows: Any = session.execute(
            select(Framework, CatalogRelease, func.coalesce(counts.c.n, 0))
            .outerjoin(
                CatalogRelease,
                (CatalogRelease.framework_id == Framework.id) & CatalogRelease.is_current.is_(True),
            )
            .outerjoin(counts, counts.c.catalog_release_id == CatalogRelease.id)
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
                    current_release=(
                        ReleaseSummary(id=r.id, version=r.version, title=r.title)
                        if r is not None
                        else None
                    ),
                )
                for f, r, n in rows
            ]
        )
