"""Catalog release import and verification at startup (S3-03).

For each framework manifest that declares a catalog (`framework_catalog.py`):

1. If a release with the declared version is already in the database, the
   stored source SHA-256 must equal the manifest's. A different hash for
   the same version means the source changed under a version that is
   already imported; that is refused, and the stored release is left
   untouched (PD-013: an import can never silently alter a release).
2. Otherwise the pinned files are read, hash-verified, parsed and validated
   (`oscal.py`), and the whole release -- families, controls,
   enhancements, parts, parameters, links, reference documents and
   profiles -- is written in one transaction. Any failure writes nothing.
3. The declared release becomes the framework's *current* release (the one
   new assessments are pinned to). Earlier releases stay as they are.
4. Assessments of that framework that have no pinned release yet (created
   before any catalog existed) are pinned to it, once (PD-015). A pinned
   release is never changed afterwards; the database refuses it
   (`trg_assessment_release_pinned`).

A source that fails validation is logged with a fixed reason code and
skipped: the service still starts, and that framework simply has no
catalog (the UI says so). Database errors propagate and fail startup closed
(`db/bootstrap.py`). Nothing from the source text is ever logged.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from . import oscal
from .db.engine import Database
from .db.models import (
    Assessment,
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
)
from .framework_catalog import CatalogSource, FrameworkMetadata, ProfileSource

logger = logging.getLogger("controlgauge_service.catalog")


class CatalogImportError(Exception):
    """A catalog could not be imported. `reason` is a fixed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _new_id() -> str:
    return str(uuid.uuid4())


def _source_file(directory: Path | None, relative: str) -> Path:
    """Resolves a manifest path and refuses anything outside the framework's
    own directory (including through a symlink)."""
    if directory is None:
        raise CatalogImportError("source_path_invalid")
    base = directory.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise CatalogImportError("source_path_invalid")
    return path


# --- parsing (outside any transaction) ---------------------------------------------


def _read_sources(
    meta: FrameworkMetadata, catalog_source: CatalogSource
) -> tuple[oscal.Catalog, list[tuple[ProfileSource, oscal.Profile]]]:
    try:
        data = oscal.read_verified(
            _source_file(meta.directory, catalog_source.path),
            catalog_source.sha256,
            oscal.MAX_CATALOG_BYTES,
        )
        catalog = oscal.parse_catalog(data)
        if catalog.version != catalog_source.release_version:
            raise CatalogImportError("release_version_mismatch")
        profiles = []
        for profile_source in meta.profiles:
            profile_data = oscal.read_verified(
                _source_file(meta.directory, profile_source.path),
                profile_source.sha256,
                oscal.MAX_PROFILE_BYTES,
            )
            profiles.append((profile_source, oscal.parse_profile(profile_data)))
    except oscal.OscalError as exc:
        raise CatalogImportError(exc.reason) from None
    return catalog, profiles


# --- writing -------------------------------------------------------------------------


class _Rows:
    """Row dictionaries for bulk inserts, built in dependency order."""

    def __init__(self, release_id: str) -> None:
        self.release_id = release_id
        self.families: list[dict[str, Any]] = []
        self.controls: list[dict[str, Any]] = []
        self.enhancements: list[dict[str, Any]] = []
        self.parts: list[dict[str, Any]] = []
        self.params: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        #: source id -> ("control" | "enhancement", row id)
        self.by_source_id: dict[str, tuple[str, str]] = {}

    def owned(self, owner: dict[str, str | None], position: int) -> dict[str, Any]:
        return {
            "id": _new_id(),
            "catalog_release_id": self.release_id,
            "position": position,
            **owner,
        }

    def add_parts(
        self, parts: Iterable[oscal.Part], owner: dict[str, str | None], parent: str | None
    ) -> None:
        for position, part in enumerate(parts):
            row = self.owned(owner, position)
            row.update(
                parent_id=parent,
                name=part.name,
                source_id=part.source_id,
                label=part.label,
                title=part.title,
                prose=part.prose,
            )
            self.parts.append(row)  # parents precede their children
            self.add_parts(part.parts, owner, row["id"])

    def add_body(self, control: oscal.Control, owner: dict[str, str | None]) -> None:
        self.add_parts(control.parts, owner, None)
        for position, param in enumerate(control.params):
            row = self.owned(owner, position)
            row.update(
                source_id=param.source_id,
                label=param.label,
                guidelines=list(param.guidelines) or None,
                select_how_many=param.select_how_many,
                choices=list(param.choices) if param.choices is not None else None,
            )
            self.params.append(row)
        for position, link in enumerate(control.links):
            row = self.owned(owner, position)
            row.update(rel=link.rel, href=link.href)
            self.links.append(row)

    def add_control(
        self, control: oscal.Control, framework_id: str, family_id: str | None, position: int
    ) -> None:
        control_id = _new_id()
        self.controls.append(
            {
                "id": control_id,
                "framework_id": framework_id,
                "catalog_release_id": self.release_id,
                "family_id": family_id,
                "source_id": control.source_id,
                "identifier": control.identifier,
                "title": control.title,
                "status": control.status,
                "position": position,
            }
        )
        self.by_source_id[control.source_id] = ("control", control_id)
        self.add_body(
            control, {"family_id": None, "control_id": control_id, "enhancement_id": None}
        )
        for enh_position, enhancement in enumerate(control.enhancements):
            enhancement_id = _new_id()
            self.enhancements.append(
                {
                    "id": enhancement_id,
                    "control_id": control_id,
                    "catalog_release_id": self.release_id,
                    "source_id": enhancement.source_id,
                    "identifier": enhancement.identifier,
                    "title": enhancement.title,
                    "status": enhancement.status,
                    "position": enh_position,
                }
            )
            self.by_source_id[enhancement.source_id] = ("enhancement", enhancement_id)
            self.add_body(
                enhancement,
                {"family_id": None, "control_id": None, "enhancement_id": enhancement_id},
            )


def _strip_owner_keys(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parameters and links have no `family_id` column."""
    return [{k: v for k, v in row.items() if k != "family_id"} for row in rows]


def _write_release(
    session: Session,
    meta: FrameworkMetadata,
    source: CatalogSource,
    catalog: oscal.Catalog,
    profiles: list[tuple[ProfileSource, oscal.Profile]],
) -> str:
    release_id = _new_id()
    session.add(
        CatalogRelease(
            id=release_id,
            framework_id=meta.id,
            version=catalog.version,
            title=catalog.title,
            source_format=source.format,
            source_format_version=catalog.oscal_version,
            source_last_modified=catalog.last_modified,
            source_file_name=Path(source.path).name,
            source_sha256=source.sha256,
            source_repository_url=source.source.repository_url,
            source_release_url=source.source.release_url,
            source_file_url=source.source.file_url,
            source_tag=source.source.tag,
            source_commit=source.source.commit,
            license_notice=source.license_notice,
            attribution=source.attribution,
            is_current=False,
        )
    )
    session.flush()

    rows = _Rows(release_id)
    position = 0
    for group_position, group in enumerate(catalog.groups):
        family_id = _new_id()
        rows.families.append(
            {
                "id": family_id,
                "framework_id": meta.id,
                "catalog_release_id": release_id,
                "source_id": group.source_id,
                "identifier": group.identifier,
                "name": group.title,
                "position": group_position,
            }
        )
        rows.add_parts(
            group.parts, {"family_id": family_id, "control_id": None, "enhancement_id": None}, None
        )
        for control in group.controls:
            rows.add_control(control, meta.id, family_id, position)
            position += 1
    for control in catalog.ungrouped:
        rows.add_control(control, meta.id, None, position)
        position += 1

    for model, batch in (
        (ControlFamily, rows.families),
        (Control, rows.controls),
        (ControlEnhancement, rows.enhancements),
        (ControlPart, rows.parts),
        (ControlParameter, _strip_owner_keys(rows.params)),
        (ControlLink, _strip_owner_keys(rows.links)),
    ):
        if batch:
            session.execute(insert(model), batch)

    resources = [
        {
            "id": _new_id(),
            "catalog_release_id": release_id,
            "source_id": r.source_id,
            "title": r.title,
            "citation": r.citation,
            "href": r.href,
        }
        for r in catalog.resources
    ]
    if resources:
        session.execute(insert(CatalogResource), resources)

    for profile_position, (profile_source, profile) in enumerate(profiles):
        baseline_id = _new_id()
        session.execute(
            insert(Baseline),
            [
                {
                    "id": baseline_id,
                    "catalog_release_id": release_id,
                    "identifier": profile_source.id,
                    "title": profile.title,
                    "version": profile.version,
                    "source_file_name": Path(profile_source.path).name,
                    "source_sha256": profile_source.sha256,
                    "source_file_url": profile_source.file_url,
                    "position": profile_position,
                }
            ],
        )
        selected = profile.with_ids if profile.with_ids is not None else list(rows.by_source_id)
        members = []
        for source_id in selected:
            target = rows.by_source_id.get(source_id)
            if target is None:
                raise CatalogImportError("unknown_profile_reference")
            kind, row_id = target
            members.append(
                {
                    "id": _new_id(),
                    "baseline_id": baseline_id,
                    "control_id": row_id if kind == "control" else None,
                    "enhancement_id": row_id if kind == "enhancement" else None,
                }
            )
        if members:
            session.execute(insert(BaselineMember), members)
    return release_id


def _make_current_and_pin(session: Session, framework_id: str, release_id: str) -> int:
    release = session.get(CatalogRelease, release_id)
    if release is not None and not release.is_current:
        session.execute(
            update(CatalogRelease)
            .where(CatalogRelease.framework_id == framework_id, CatalogRelease.is_current.is_(True))
            .values(is_current=False)
        )
        session.flush()
        session.execute(
            update(CatalogRelease).where(CatalogRelease.id == release_id).values(is_current=True)
        )
    result = session.execute(
        update(Assessment)
        .where(Assessment.framework_id == framework_id, Assessment.catalog_release_id.is_(None))
        .values(catalog_release_id=release_id)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _existing_release(session: Session, framework_id: str, version: str) -> CatalogRelease | None:
    return session.scalars(
        select(CatalogRelease).where(
            CatalogRelease.framework_id == framework_id, CatalogRelease.version == version
        )
    ).one_or_none()


def sync_catalog(database: Database, meta: FrameworkMetadata) -> str:
    """Imports or verifies one framework's declared catalog. Returns
    `imported` or `verified`; raises `CatalogImportError` on refusal."""
    source = meta.catalog
    if source is None:
        raise CatalogImportError("no_catalog_declared")

    with database.session() as session:
        existing = _existing_release(session, meta.id, source.release_version)
        needs_import = existing is None
        if existing is not None and existing.source_sha256 != source.sha256:
            raise CatalogImportError("release_conflict")

    parsed = _read_sources(meta, source) if needs_import else None

    with database.session(write=True) as session:
        existing = _existing_release(session, meta.id, source.release_version)
        if existing is not None:
            # Already imported (possibly by a concurrent process meanwhile).
            if existing.source_sha256 != source.sha256:
                raise CatalogImportError("release_conflict")
            release_id = existing.id
            outcome = "verified"
        else:
            if parsed is None:
                raise CatalogImportError("release_disappeared")
            catalog, profiles = parsed
            release_id = _write_release(session, meta, source, catalog, profiles)
            outcome = "imported"
        pinned = _make_current_and_pin(session, meta.id, release_id)
    logger.info(
        "catalog_release_ready",
        extra={"framework_id": meta.id, "outcome": outcome, "assessments_pinned": pinned},
    )
    return outcome


def sync_catalogs(database: Database, frameworks: Iterable[FrameworkMetadata]) -> None:
    for meta in frameworks:
        if meta.catalog is None:
            continue
        try:
            sync_catalog(database, meta)
        except CatalogImportError as exc:
            logger.warning(
                "catalog_import_refused", extra={"framework_id": meta.id, "reason": exc.reason}
            )


def current_release_id(session: Session, framework_id: str) -> str | None:
    return session.scalars(
        select(CatalogRelease.id).where(
            CatalogRelease.framework_id == framework_id, CatalogRelease.is_current.is_(True)
        )
    ).one_or_none()
