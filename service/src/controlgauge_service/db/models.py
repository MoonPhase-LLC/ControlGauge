"""ORM models. Schema follows `docs/DATABASE.md`; tables are created only by
versioned migrations (`migrations/versions/`).

Framework-generic by construction (`docs/ARCHITECTURE.md` §8, D-016): no
column, constraint or default names or assumes NIST. `identifier` is the
framework-native display identifier (e.g. `AC-2`) and is never parsed;
`source_id` is the identifier inside the source document; `id` is an opaque
key.

Sprint 3 (migration 0003, `docs/DATABASE.md` §8): framework *content* is
scoped to an immutable `catalog_release`, one imported version of a
framework's catalog with its provenance. Families, controls, enhancements,
their parts (the verbatim official text), parameters and links all belong
to one release; a later release is imported beside it, never over it. An
assessment is pinned to one release. Database triggers (created by the
migration) refuse changes to imported content and to a pinned release.

Deviations from `docs/DATABASE.md` §1 are listed in its §7 and §8.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UTCDateTime, utc_now

#: Upper bounds shared by the API validation layer and the CHECK
#: constraints, so the database rejects what the API would reject.
ASSESSMENT_NAME_MAX = 200
ASSESSMENT_DESCRIPTION_MAX = 2000
ID_MAX = 64
#: Catalog field bounds, enforced by the importer before any insert.
SOURCE_ID_MAX = 100
IDENTIFIER_MAX = 100
TITLE_MAX = 500
STATUS_MAX = 50
URL_MAX = 2000


class Framework(Base):
    __tablename__ = "framework"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(100))
    source_reference: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint("length(trim(id)) > 0", name="id_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(trim(version)) > 0", name="version_not_blank"),
    )


class CatalogRelease(Base):
    """One imported version of a framework's catalog. Immutable once
    imported, except `is_current` (which release new assessments use)."""

    __tablename__ = "catalog_release"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(
        ForeignKey("framework.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(TITLE_MAX))
    source_format: Mapped[str] = mapped_column(String(STATUS_MAX))
    source_format_version: Mapped[str | None] = mapped_column(String(STATUS_MAX))
    source_last_modified: Mapped[str | None] = mapped_column(String(64))
    source_file_name: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_repository_url: Mapped[str | None] = mapped_column(String(500))
    source_release_url: Mapped[str | None] = mapped_column(String(500))
    source_file_url: Mapped[str | None] = mapped_column(String(500))
    source_tag: Mapped[str | None] = mapped_column(String(100))
    source_commit: Mapped[str | None] = mapped_column(String(64))
    license_notice: Mapped[str] = mapped_column(Text)
    attribution: Mapped[str] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    __table_args__ = (
        UniqueConstraint("framework_id", "version"),
        # Target of the release-scoped composite FKs below.
        UniqueConstraint("id", "framework_id"),
        CheckConstraint("length(trim(version)) > 0", name="version_not_blank"),
        CheckConstraint("length(source_sha256) = 64", name="sha256_length"),
        # At most one current release per framework.
        Index(
            "uq_catalog_release_current",
            "framework_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )


class ControlFamily(Base):
    __tablename__ = "control_family"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(String(ID_MAX))
    catalog_release_id: Mapped[str] = mapped_column(String(ID_MAX), index=True)
    source_id: Mapped[str] = mapped_column(String(SOURCE_ID_MAX))
    identifier: Mapped[str] = mapped_column(String(IDENTIFIER_MAX))
    name: Mapped[str] = mapped_column(String(TITLE_MAX))
    position: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        ForeignKeyConstraint(
            ["catalog_release_id", "framework_id"],
            ["catalog_release.id", "catalog_release.framework_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("catalog_release_id", "identifier"),
        UniqueConstraint("catalog_release_id", "source_id"),
        # Target of `control`'s composite FK below.
        UniqueConstraint("id", "catalog_release_id"),
    )


class Control(Base):
    __tablename__ = "control"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(String(ID_MAX), index=True)
    catalog_release_id: Mapped[str] = mapped_column(String(ID_MAX), index=True)
    #: Nullable: a control may attach directly to its framework (D-016).
    family_id: Mapped[str | None] = mapped_column(String(ID_MAX), index=True)
    source_id: Mapped[str] = mapped_column(String(SOURCE_ID_MAX))
    identifier: Mapped[str] = mapped_column(String(IDENTIFIER_MAX))
    title: Mapped[str] = mapped_column(String(TITLE_MAX))
    #: Source lifecycle status verbatim (e.g. `withdrawn`); NULL when the
    #: source states none.
    status: Mapped[str | None] = mapped_column(String(STATUS_MAX))
    position: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        ForeignKeyConstraint(
            ["catalog_release_id", "framework_id"],
            ["catalog_release.id", "catalog_release.framework_id"],
            ondelete="RESTRICT",
        ),
        # A set family must belong to the control's own release (and so its
        # framework). NULL `family_id` is the "ungrouped" case (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["family_id", "catalog_release_id"],
            ["control_family.id", "control_family.catalog_release_id"],
            ondelete="RESTRICT",
            name="fk_control_family_id_release_control_family",
        ),
        UniqueConstraint("catalog_release_id", "identifier"),
        UniqueConstraint("catalog_release_id", "source_id"),
        UniqueConstraint("id", "catalog_release_id"),
    )


class ControlEnhancement(Base):
    __tablename__ = "control_enhancement"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    control_id: Mapped[str] = mapped_column(String(ID_MAX), index=True)
    catalog_release_id: Mapped[str] = mapped_column(String(ID_MAX), index=True)
    source_id: Mapped[str] = mapped_column(String(SOURCE_ID_MAX))
    identifier: Mapped[str] = mapped_column(String(IDENTIFIER_MAX))
    title: Mapped[str] = mapped_column(String(TITLE_MAX))
    status: Mapped[str | None] = mapped_column(String(STATUS_MAX))
    position: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        ForeignKeyConstraint(
            ["control_id", "catalog_release_id"],
            ["control.id", "control.catalog_release_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("control_id", "identifier"),
        UniqueConstraint("catalog_release_id", "source_id"),
        UniqueConstraint("id", "catalog_release_id"),
    )


_ONE_OWNER = "(control_id IS NULL) <> (enhancement_id IS NULL)"
_ONE_OF_THREE_OWNERS = (
    "(family_id IS NOT NULL) + (control_id IS NOT NULL) + (enhancement_id IS NOT NULL) = 1"
)


class ControlPart(Base):
    """A part of a family, control or enhancement (statement, item,
    guidance, overview, ...), in source order. `prose` is the verbatim
    source text, including the source's own markup and parameter-insertion
    markers."""

    __tablename__ = "control_part"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    catalog_release_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_release.id", ondelete="RESTRICT"), index=True
    )
    family_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_family.id", ondelete="RESTRICT"), index=True
    )
    control_id: Mapped[str | None] = mapped_column(
        ForeignKey("control.id", ondelete="RESTRICT"), index=True
    )
    enhancement_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_enhancement.id", ondelete="RESTRICT"), index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_part.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(SOURCE_ID_MAX))
    source_id: Mapped[str | None] = mapped_column(String(SOURCE_ID_MAX))
    label: Mapped[str | None] = mapped_column(String(IDENTIFIER_MAX))
    title: Mapped[str | None] = mapped_column(String(TITLE_MAX))
    prose: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint(_ONE_OF_THREE_OWNERS, name="one_owner"),)


class ControlParameter(Base):
    """A parameter the source text refers to. `guidelines` and `choices` are
    verbatim lists of source strings."""

    __tablename__ = "control_parameter"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    catalog_release_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_release.id", ondelete="RESTRICT"), index=True
    )
    control_id: Mapped[str | None] = mapped_column(
        ForeignKey("control.id", ondelete="RESTRICT"), index=True
    )
    enhancement_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_enhancement.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(String(SOURCE_ID_MAX))
    label: Mapped[str | None] = mapped_column(String(TITLE_MAX))
    guidelines: Mapped[list[str] | None] = mapped_column(JSON)
    select_how_many: Mapped[str | None] = mapped_column(String(STATUS_MAX))
    choices: Mapped[list[str] | None] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("catalog_release_id", "source_id"),
        CheckConstraint(_ONE_OWNER, name="one_owner"),
    )


class ControlLink(Base):
    """A source link (`rel` + `href`, verbatim). Targets are resolved at read
    time within the same release; nothing here assumes a link vocabulary."""

    __tablename__ = "control_link"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    catalog_release_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_release.id", ondelete="RESTRICT"), index=True
    )
    control_id: Mapped[str | None] = mapped_column(
        ForeignKey("control.id", ondelete="RESTRICT"), index=True
    )
    enhancement_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_enhancement.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    rel: Mapped[str | None] = mapped_column(String(STATUS_MAX))
    href: Mapped[str] = mapped_column(String(URL_MAX))

    __table_args__ = (CheckConstraint(_ONE_OWNER, name="one_owner"),)


class CatalogResource(Base):
    """A reference document listed by the source (OSCAL back-matter)."""

    __tablename__ = "catalog_resource"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    catalog_release_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_release.id", ondelete="RESTRICT"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(SOURCE_ID_MAX))
    title: Mapped[str | None] = mapped_column(String(TITLE_MAX))
    citation: Mapped[str | None] = mapped_column(Text)
    href: Mapped[str | None] = mapped_column(String(URL_MAX))

    __table_args__ = (UniqueConstraint("catalog_release_id", "source_id"),)


class Baseline(Base):
    """A named control selection published with a release. Reference data
    only: assessments are not scoped by it (`OPEN_QUESTIONS.md` P-1)."""

    __tablename__ = "baseline"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    catalog_release_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_release.id", ondelete="RESTRICT"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(IDENTIFIER_MAX))
    title: Mapped[str] = mapped_column(String(TITLE_MAX))
    version: Mapped[str | None] = mapped_column(String(100))
    source_file_name: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_file_url: Mapped[str | None] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("catalog_release_id", "identifier"),)


class BaselineMember(Base):
    __tablename__ = "baseline_member"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("baseline.id", ondelete="RESTRICT"), index=True
    )
    control_id: Mapped[str | None] = mapped_column(
        ForeignKey("control.id", ondelete="RESTRICT"), index=True
    )
    enhancement_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_enhancement.id", ondelete="RESTRICT"), index=True
    )

    __table_args__ = (
        CheckConstraint(_ONE_OWNER, name="one_owner"),
        UniqueConstraint("baseline_id", "control_id"),
        UniqueConstraint("baseline_id", "enhancement_id"),
    )


class Assessment(Base):
    __tablename__ = "assessment"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(ForeignKey("framework.id", ondelete="RESTRICT"))
    #: The catalog release this assessment uses. NULL only while its
    #: framework has no imported catalog; once set it never changes
    #: (trigger `trg_assessment_release_pinned`, PD-013/PD-015).
    catalog_release_id: Mapped[str | None] = mapped_column(String(ID_MAX))
    name: Mapped[str] = mapped_column(String(ASSESSMENT_NAME_MAX))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint(f"length(name) <= {ASSESSMENT_NAME_MAX}", name="name_max_length"),
        CheckConstraint(
            f"description IS NULL OR length(description) <= {ASSESSMENT_DESCRIPTION_MAX}",
            name="description_max_length",
        ),
        # The pinned release must belong to the assessment's own framework.
        ForeignKeyConstraint(
            ["catalog_release_id", "framework_id"],
            ["catalog_release.id", "catalog_release.framework_id"],
            ondelete="RESTRICT",
        ),
        Index("ix_assessment_framework_id", "framework_id"),
        Index("ix_assessment_catalog_release_id", "catalog_release_id"),
        Index("ix_assessment_created_at", "created_at"),
    )


# --- Sprint 4: evidence (migration 0004, docs/DATABASE.md §9) ----------------

ARTIFACT_FILENAME_MAX = 255
SECTION_LOCATOR_MAX = 300
#: `DATABASE.md` §3 extraction outcomes.
PARSE_STATUSES = (
    "pending",
    "parsing",
    "parsed",
    "partial",
    "empty",
    "unsupported_format",
    "failed",
)
ARTIFACT_FORMATS = ("pdf", "docx", "txt", "csv")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Artifact(Base):
    """One uploaded evidence file in an assessment. The file itself lives in
    the evidence store under a hash-derived name (`evidence/store.py`);
    `filename` is display text only and never used as a path (T-05)."""

    __tablename__ = "artifact"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessment.id", ondelete="RESTRICT"))
    filename: Mapped[str] = mapped_column(String(ARTIFACT_FILENAME_MAX))
    sha256_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(10))
    mime_type: Mapped[str] = mapped_column(String(100))
    #: Set by classification (Sprint 7); null until then.
    classification: Mapped[str | None] = mapped_column(String(100))
    classification_source: Mapped[str | None] = mapped_column(String(20))
    parse_status: Mapped[str] = mapped_column(String(30), default="pending")
    #: Fixed category explaining a non-`parsed` outcome (e.g. `encrypted`,
    #: `timeout`, `page_limit`). Never document content.
    parse_detail: Mapped[str | None] = mapped_column(String(50))
    parser_version: Mapped[str | None] = mapped_column(String(50))
    section_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    parsed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        # Duplicate detection scope is the assessment (DATABASE.md §5).
        UniqueConstraint("assessment_id", "sha256_hash"),
        CheckConstraint("length(trim(filename)) > 0", name="filename_not_blank"),
        CheckConstraint(f"length(filename) <= {ARTIFACT_FILENAME_MAX}", name="filename_max_length"),
        CheckConstraint("length(sha256_hash) = 64", name="sha256_length"),
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        CheckConstraint(_in("format", ARTIFACT_FORMATS), name="format_known"),
        CheckConstraint(_in("parse_status", PARSE_STATUSES), name="parse_status_known"),
        CheckConstraint(
            "classification_source IS NULL OR classification_source IN ('ai', 'analyst')",
            name="classification_source_known",
        ),
        CheckConstraint("section_count >= 0", name="section_count_not_negative"),
        Index("ix_artifact_assessment_id", "assessment_id"),
        Index("ix_artifact_parse_status", "parse_status"),
    )


class ArtifactSection(Base):
    """One extracted chunk of an artifact, with a human-meaningful locator
    (page, heading path, line or row range). Immutable once written."""

    __tablename__ = "artifact_section"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifact.id", ondelete="RESTRICT"))
    sequence_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(String(SECTION_LOCATOR_MAX))

    __table_args__ = (
        UniqueConstraint("artifact_id", "sequence_index"),
        CheckConstraint("sequence_index >= 0", name="sequence_index_not_negative"),
        CheckConstraint(f"length(locator) <= {SECTION_LOCATOR_MAX}", name="locator_max_length"),
        Index("ix_artifact_section_artifact_id", "artifact_id"),
    )
