"""Catalog browsing (S3-04): read-only views of an imported catalog release.

- `GET /catalog-releases/{id}`: provenance, families, baselines, counts.
- `GET /catalog-releases/{id}/controls`: controls in source order, with
  optional family/baseline filters and an identifier/title search.
- `GET /controls/{id}`: one control with its parts (verbatim official
  text), parameters, links (resolved within the release), enhancements and
  baseline membership.

Authenticated like every route. Official text is returned exactly as
stored; nothing here rewrites it, and clients must render it as inert
text (`docs/SECURITY.md` T-21). Framework-agnostic: link relations, part
names and statuses are passed through, never interpreted.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from ..db import Database
from ..db.models import (
    Baseline,
    BaselineMember,
    CatalogRelease,
    CatalogResource,
    Control,
    ControlEnhancement,
    ControlFamily,
    ControlLink,
    ControlParameter,
    ControlPart,
    Framework,
)
from .deps import get_database
from .errors import ApiError

router = APIRouter()

DatabaseDep = Annotated[Database, Depends(get_database)]

CONTROL_LIMIT_DEFAULT = 100
CONTROL_LIMIT_MAX = 500
SEARCH_MAX = 100
_SEARCH_DISALLOWED = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


# --- response shapes ---------------------------------------------------------------


class PartOut(BaseModel):
    name: str
    label: str | None
    title: str | None
    prose: str | None
    parts: list[PartOut]


class ParamOut(BaseModel):
    source_id: str
    label: str | None
    guidelines: list[str]
    select_how_many: str | None
    choices: list[str] | None


class LinkTarget(BaseModel):
    #: control | enhancement | part | family | resource | external | unresolved
    kind: str
    id: str | None = None
    #: The control to open for this target (control, enhancement or part).
    control_id: str | None = None
    #: For a part target: the part's own label within its owner.
    part_label: str | None = None
    identifier: str | None = None
    title: str | None = None
    citation: str | None = None
    href: str | None = None


class LinkOut(BaseModel):
    rel: str | None
    href: str
    target: LinkTarget


class FamilyRef(BaseModel):
    id: str
    identifier: str
    name: str


class FamilyOut(FamilyRef):
    control_count: int
    parts: list[PartOut]


class BaselineOut(BaseModel):
    id: str
    identifier: str
    title: str
    version: str | None
    member_count: int
    source_file_name: str
    source_sha256: str
    source_file_url: str | None


class SourceOut(BaseModel):
    format: str
    format_version: str | None
    last_modified: str | None
    file_name: str
    sha256: str
    repository_url: str | None
    release_url: str | None
    file_url: str | None
    tag: str | None
    commit: str | None


class FrameworkRef(BaseModel):
    id: str
    name: str
    version: str


class ReleaseOut(BaseModel):
    id: str
    framework: FrameworkRef
    version: str
    title: str
    is_current: bool
    imported_at: datetime
    source: SourceOut
    license_notice: str
    attribution: str
    control_count: int
    enhancement_count: int
    ungrouped_control_count: int
    families: list[FamilyOut]
    baselines: list[BaselineOut]


class ControlSummary(BaseModel):
    id: str
    identifier: str
    title: str
    status: str | None
    family_id: str | None
    enhancement_count: int
    baselines: list[str]


class ControlList(BaseModel):
    controls: list[ControlSummary]
    total: int
    limit: int
    offset: int


class EnhancementOut(BaseModel):
    id: str
    identifier: str
    title: str
    status: str | None
    baselines: list[str]
    parts: list[PartOut]
    params: list[ParamOut]
    links: list[LinkOut]


class ReleaseRef(BaseModel):
    id: str
    version: str
    title: str
    is_current: bool
    attribution: str
    framework: FrameworkRef


class ControlDetail(BaseModel):
    id: str
    identifier: str
    title: str
    status: str | None
    release: ReleaseRef
    family: FamilyRef | None
    baselines: list[str]
    parts: list[PartOut]
    params: list[ParamOut]
    links: list[LinkOut]
    enhancements: list[EnhancementOut]


# --- helpers -------------------------------------------------------------------------


def _canonical_uuid(value: str, not_found: ApiError) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise not_found from None
    if canonical != value:
        raise not_found
    return canonical


def _release_not_found() -> ApiError:
    return ApiError(404, "catalog_release_not_found", "The catalog release was not found.")


def _control_not_found() -> ApiError:
    return ApiError(404, "control_not_found", "The control was not found.")


def _framework_ref(framework: Framework) -> FrameworkRef:
    return FrameworkRef(id=framework.id, name=framework.name, version=framework.version)


def _part_trees(rows: list[ControlPart], key: str) -> dict[str, list[PartOut]]:
    """Builds part trees from flat rows (already in `position` order),
    grouped by the owner column `key`."""
    children: dict[str | None, list[ControlPart]] = defaultdict(list)
    for row in rows:
        children[row.parent_id].append(row)

    def build(row: ControlPart) -> PartOut:
        return PartOut(
            name=row.name,
            label=row.label,
            title=row.title,
            prose=row.prose,
            parts=[build(child) for child in children.get(row.id, [])],
        )

    trees: dict[str, list[PartOut]] = defaultdict(list)
    for row in children.get(None, []):
        trees[getattr(row, key)].append(build(row))
    return trees


def _baseline_names(session: Session, column: Any, owner_ids: list[str]) -> dict[str, list[str]]:
    if not owner_ids:
        return {}
    rows = session.execute(
        select(column, Baseline.identifier)
        .join(Baseline, Baseline.id == BaselineMember.baseline_id)
        .where(column.in_(owner_ids))
        .order_by(Baseline.position)
    ).all()
    names: dict[str, list[str]] = defaultdict(list)
    for owner_id, identifier in rows:
        names[owner_id].append(identifier)
    return names


def _param_out(row: ControlParameter) -> ParamOut:
    return ParamOut(
        source_id=row.source_id,
        label=row.label,
        guidelines=list(row.guidelines or []),
        select_how_many=row.select_how_many,
        choices=list(row.choices) if row.choices is not None else None,
    )


class _LinkResolver:
    """Resolves `#fragment` hrefs against the same release only. A fragment
    may name a control, an enhancement, a part (resolved to the control or
    enhancement that owns it), a family, or a reference document."""

    def __init__(self, session: Session, release_id: str, links: list[ControlLink]) -> None:
        fragments = {link.href[1:] for link in links if link.href.startswith("#")}
        self.controls: dict[str, Control] = {}
        self.enhancements: dict[str, ControlEnhancement] = {}
        self.parts: dict[str, ControlPart] = {}
        self.families: dict[str, ControlFamily] = {}
        self.resources: dict[str, CatalogResource] = {}
        self.part_owner_controls: dict[str, Control] = {}
        self.part_owner_enhancements: dict[str, ControlEnhancement] = {}
        if not fragments:
            return

        def by_source_id(model: Any) -> list[Any]:
            return list(
                session.scalars(
                    select(model).where(
                        model.catalog_release_id == release_id, model.source_id.in_(fragments)
                    )
                )
            )

        self.controls = {c.source_id: c for c in by_source_id(Control)}
        self.enhancements = {e.source_id: e for e in by_source_id(ControlEnhancement)}
        self.parts = {p.source_id: p for p in by_source_id(ControlPart) if p.source_id}
        self.families = {f.source_id: f for f in by_source_id(ControlFamily)}
        self.resources = {r.source_id: r for r in by_source_id(CatalogResource)}

        owner_controls = {p.control_id for p in self.parts.values() if p.control_id}
        owner_enhancements = {p.enhancement_id for p in self.parts.values() if p.enhancement_id}
        if owner_controls:
            self.part_owner_controls = {
                c.id: c
                for c in session.scalars(select(Control).where(Control.id.in_(owner_controls)))
            }
        if owner_enhancements:
            self.part_owner_enhancements = {
                e.id: e
                for e in session.scalars(
                    select(ControlEnhancement).where(ControlEnhancement.id.in_(owner_enhancements))
                )
            }

    def _part_target(self, part: ControlPart) -> LinkTarget:
        control = self.part_owner_controls.get(part.control_id or "")
        if control is not None:
            return LinkTarget(
                kind="part",
                id=control.id,
                control_id=control.id,
                identifier=control.identifier,
                title=control.title,
                part_label=part.label,
            )
        enhancement = self.part_owner_enhancements.get(part.enhancement_id or "")
        if enhancement is not None:
            return LinkTarget(
                kind="part",
                id=enhancement.id,
                control_id=enhancement.control_id,
                identifier=enhancement.identifier,
                title=enhancement.title,
                part_label=part.label,
            )
        return LinkTarget(kind="unresolved")

    def resolve(self, link: ControlLink) -> LinkOut:
        href = link.href
        if not href.startswith("#"):
            target = LinkTarget(kind="external", href=href)
        else:
            fragment = href[1:]
            if (c := self.controls.get(fragment)) is not None:
                target = LinkTarget(
                    kind="control",
                    id=c.id,
                    control_id=c.id,
                    identifier=c.identifier,
                    title=c.title,
                )
            elif (e := self.enhancements.get(fragment)) is not None:
                target = LinkTarget(
                    kind="enhancement",
                    id=e.id,
                    control_id=e.control_id,
                    identifier=e.identifier,
                    title=e.title,
                )
            elif (p := self.parts.get(fragment)) is not None:
                target = self._part_target(p)
            elif (f := self.families.get(fragment)) is not None:
                target = LinkTarget(kind="family", id=f.id, identifier=f.identifier, title=f.name)
            elif (r := self.resources.get(fragment)) is not None:
                target = LinkTarget(
                    kind="resource", title=r.title, citation=r.citation, href=r.href
                )
            else:
                target = LinkTarget(kind="unresolved")
        return LinkOut(rel=link.rel, href=href, target=target)


# --- routes --------------------------------------------------------------------------


@router.get("/catalog-releases/{release_id}")
def get_catalog_release(release_id: str, database: DatabaseDep) -> ReleaseOut:
    canonical = _canonical_uuid(release_id, _release_not_found())
    with database.session() as session:
        row = session.execute(
            select(CatalogRelease, Framework)
            .join(Framework, Framework.id == CatalogRelease.framework_id)
            .where(CatalogRelease.id == canonical)
        ).first()
        if row is None:
            raise _release_not_found()
        release, framework = row

        control_counts: dict[str | None, int] = {
            family_id: int(n)
            for family_id, n in session.execute(
                select(Control.family_id, func.count())
                .where(Control.catalog_release_id == canonical)
                .group_by(Control.family_id)
            ).all()
        }
        enhancement_count = (
            session.scalar(
                select(func.count())
                .select_from(ControlEnhancement)
                .where(ControlEnhancement.catalog_release_id == canonical)
            )
            or 0
        )
        families = session.scalars(
            select(ControlFamily)
            .where(ControlFamily.catalog_release_id == canonical)
            .order_by(ControlFamily.position)
        ).all()
        family_parts = _part_trees(
            list(
                session.scalars(
                    select(ControlPart)
                    .where(
                        ControlPart.catalog_release_id == canonical,
                        ControlPart.family_id.is_not(None),
                    )
                    .order_by(ControlPart.position)
                )
            ),
            "family_id",
        )
        baselines = session.execute(
            select(Baseline, func.count(BaselineMember.id))
            .outerjoin(BaselineMember, BaselineMember.baseline_id == Baseline.id)
            .where(Baseline.catalog_release_id == canonical)
            .group_by(Baseline.id)
            .order_by(Baseline.position)
        ).all()

        return ReleaseOut(
            id=release.id,
            framework=_framework_ref(framework),
            version=release.version,
            title=release.title,
            is_current=release.is_current,
            imported_at=release.imported_at,
            source=SourceOut(
                format=release.source_format,
                format_version=release.source_format_version,
                last_modified=release.source_last_modified,
                file_name=release.source_file_name,
                sha256=release.source_sha256,
                repository_url=release.source_repository_url,
                release_url=release.source_release_url,
                file_url=release.source_file_url,
                tag=release.source_tag,
                commit=release.source_commit,
            ),
            license_notice=release.license_notice,
            attribution=release.attribution,
            control_count=sum(control_counts.values()),
            enhancement_count=int(enhancement_count),
            ungrouped_control_count=int(control_counts.get(None, 0)),
            families=[
                FamilyOut(
                    id=f.id,
                    identifier=f.identifier,
                    name=f.name,
                    control_count=int(control_counts.get(f.id, 0)),
                    parts=family_parts.get(f.id, []),
                )
                for f in families
            ],
            baselines=[
                BaselineOut(
                    id=b.id,
                    identifier=b.identifier,
                    title=b.title,
                    version=b.version,
                    member_count=int(n),
                    source_file_name=b.source_file_name,
                    source_sha256=b.source_sha256,
                    source_file_url=b.source_file_url,
                )
                for b, n in baselines
            ],
        )


def _search_term(q: str | None) -> str | None:
    if q is None:
        return None
    q = q.strip()
    if not q:
        return None
    if len(q) > SEARCH_MAX or _SEARCH_DISALLOWED.search(q):
        raise ApiError(
            422,
            "validation_failed",
            "The request is not valid.",
            [{"field": "q", "code": "invalid", "message": "This value is not valid."}],
        )
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filter_error(field: str) -> ApiError:
    return ApiError(
        422,
        "validation_failed",
        "The request is not valid.",
        [{"field": field, "code": "invalid", "message": "This value is not valid."}],
    )


@router.get("/catalog-releases/{release_id}/controls")
def list_catalog_controls(
    release_id: str,
    database: DatabaseDep,
    family_id: Annotated[str | None, Query(max_length=64)] = None,
    baseline_id: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=SEARCH_MAX * 2)] = None,
    limit: Annotated[int, Query(ge=1, le=CONTROL_LIMIT_MAX)] = CONTROL_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> ControlList:
    canonical = _canonical_uuid(release_id, _release_not_found())
    pattern = _search_term(q)
    with database.session() as session:
        if session.get(CatalogRelease, canonical) is None:
            raise _release_not_found()

        conditions: list[Any] = [Control.catalog_release_id == canonical]
        if family_id is not None:
            family = _canonical_uuid(family_id, _filter_error("family_id"))
            if session.get(ControlFamily, family) is None:
                raise _filter_error("family_id")
            conditions.append(Control.family_id == family)
        if baseline_id is not None:
            baseline = _canonical_uuid(baseline_id, _filter_error("baseline_id"))
            if session.get(Baseline, baseline) is None:
                raise _filter_error("baseline_id")
            in_baseline = select(BaselineMember.id).where(BaselineMember.baseline_id == baseline)
            conditions.append(
                or_(
                    exists(in_baseline.where(BaselineMember.control_id == Control.id)),
                    exists(
                        in_baseline.join(
                            ControlEnhancement,
                            ControlEnhancement.id == BaselineMember.enhancement_id,
                        ).where(ControlEnhancement.control_id == Control.id)
                    ),
                )
            )
        if pattern is not None:
            conditions.append(
                or_(
                    Control.identifier.like(pattern, escape="\\"),
                    Control.title.like(pattern, escape="\\"),
                    exists(
                        select(ControlEnhancement.id).where(
                            ControlEnhancement.control_id == Control.id,
                            or_(
                                ControlEnhancement.identifier.like(pattern, escape="\\"),
                                ControlEnhancement.title.like(pattern, escape="\\"),
                            ),
                        )
                    ),
                )
            )
        where = and_(*conditions)
        total = session.scalar(select(func.count()).select_from(Control).where(where)) or 0
        enhancement_counts = (
            select(ControlEnhancement.control_id, func.count().label("n"))
            .group_by(ControlEnhancement.control_id)
            .subquery()
        )
        query: Select[Any] = (
            select(Control, func.coalesce(enhancement_counts.c.n, 0))
            .outerjoin(enhancement_counts, enhancement_counts.c.control_id == Control.id)
            .where(where)
            .order_by(Control.position)
            .limit(limit)
            .offset(offset)
        )
        rows = session.execute(query).all()
        baselines = _baseline_names(session, BaselineMember.control_id, [c.id for c, _ in rows])
        return ControlList(
            controls=[
                ControlSummary(
                    id=c.id,
                    identifier=c.identifier,
                    title=c.title,
                    status=c.status,
                    family_id=c.family_id,
                    enhancement_count=int(n),
                    baselines=baselines.get(c.id, []),
                )
                for c, n in rows
            ],
            total=int(total),
            limit=limit,
            offset=offset,
        )


@router.get("/controls/{control_id}")
def get_control(control_id: str, database: DatabaseDep) -> ControlDetail:
    canonical = _canonical_uuid(control_id, _control_not_found())
    with database.session() as session:
        row = session.execute(
            select(Control, CatalogRelease, Framework)
            .join(CatalogRelease, CatalogRelease.id == Control.catalog_release_id)
            .join(Framework, Framework.id == Control.framework_id)
            .where(Control.id == canonical)
        ).first()
        if row is None:
            raise _control_not_found()
        control, release, framework = row
        family = session.get(ControlFamily, control.family_id) if control.family_id else None
        enhancements = list(
            session.scalars(
                select(ControlEnhancement)
                .where(ControlEnhancement.control_id == control.id)
                .order_by(ControlEnhancement.position)
            )
        )
        enhancement_ids = [e.id for e in enhancements]

        def owned(model: Any) -> tuple[list[Any], list[Any]]:
            control_rows = list(
                session.scalars(
                    select(model).where(model.control_id == control.id).order_by(model.position)
                )
            )
            enhancement_rows = (
                list(
                    session.scalars(
                        select(model)
                        .where(model.enhancement_id.in_(enhancement_ids))
                        .order_by(model.position)
                    )
                )
                if enhancement_ids
                else []
            )
            return control_rows, enhancement_rows

        control_parts, enhancement_parts = owned(ControlPart)
        control_params, enhancement_params = owned(ControlParameter)
        control_links, enhancement_links = owned(ControlLink)
        resolver = _LinkResolver(session, release.id, control_links + enhancement_links)

        parts_by_enhancement = _part_trees(enhancement_parts, "enhancement_id")
        params_by_enhancement: dict[str, list[ParamOut]] = defaultdict(list)
        for p in enhancement_params:
            params_by_enhancement[p.enhancement_id].append(_param_out(p))
        links_by_enhancement: dict[str, list[LinkOut]] = defaultdict(list)
        for link in enhancement_links:
            links_by_enhancement[link.enhancement_id].append(resolver.resolve(link))
        control_baselines = _baseline_names(session, BaselineMember.control_id, [control.id])
        enhancement_baselines = _baseline_names(
            session, BaselineMember.enhancement_id, enhancement_ids
        )

        return ControlDetail(
            id=control.id,
            identifier=control.identifier,
            title=control.title,
            status=control.status,
            release=ReleaseRef(
                id=release.id,
                version=release.version,
                title=release.title,
                is_current=release.is_current,
                attribution=release.attribution,
                framework=_framework_ref(framework),
            ),
            family=(
                FamilyRef(id=family.id, identifier=family.identifier, name=family.name)
                if family is not None
                else None
            ),
            baselines=control_baselines.get(control.id, []),
            parts=_part_trees(control_parts, "control_id").get(control.id, []),
            params=[_param_out(p) for p in control_params],
            links=[resolver.resolve(link) for link in control_links],
            enhancements=[
                EnhancementOut(
                    id=e.id,
                    identifier=e.identifier,
                    title=e.title,
                    status=e.status,
                    baselines=enhancement_baselines.get(e.id, []),
                    parts=parts_by_enhancement.get(e.id, []),
                    params=params_by_enhancement.get(e.id, []),
                    links=links_by_enhancement.get(e.id, []),
                )
                for e in enhancements
            ],
        )
