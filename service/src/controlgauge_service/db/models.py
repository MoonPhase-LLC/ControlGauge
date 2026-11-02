"""ORM models (S2-02). Schema follows `docs/DATABASE.md`; tables are created
only by versioned migrations (`migrations/versions/`).

Framework-generic by construction (`docs/ARCHITECTURE.md` §8, D-016): no
column, constraint or default names or assumes NIST. `identifier` is the
framework-native identifier (e.g. `AC-2`) and is never parsed; `id` is an
opaque key. `official_text` holds verbatim framework language only and is
empty until a real catalog is imported (Sprint 3) -- nothing in Sprint 2
writes control rows.

Deviations from `docs/DATABASE.md` (listed in `docs/DATABASE.md` §7):
uniqueness constraints on natural keys, the composite family/framework
foreign key the doc names as the stricter option, `official_text` nullable,
and `assessment.description` nullable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UTCDateTime, utc_now

#: Upper bounds shared by the API validation layer and the CHECK
#: constraints, so the database rejects what the API would reject.
ASSESSMENT_NAME_MAX = 200
ASSESSMENT_DESCRIPTION_MAX = 2000
ID_MAX = 64


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


class ControlFamily(Base):
    __tablename__ = "control_family"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(
        ForeignKey("framework.id", ondelete="RESTRICT"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("framework_id", "identifier"),
        # Target of `control`'s composite FK below.
        UniqueConstraint("id", "framework_id"),
    )


class Control(Base):
    __tablename__ = "control"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(
        ForeignKey("framework.id", ondelete="RESTRICT"), index=True
    )
    #: Nullable: a control may attach directly to its framework (D-016).
    family_id: Mapped[str | None] = mapped_column(String(ID_MAX), index=True)
    identifier: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    official_text: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("framework_id", "identifier"),
        # `docs/DATABASE.md` §2: a set family must belong to the control's own
        # framework. With `family_id` NULL, SQLite does not enforce a
        # composite FK (MATCH SIMPLE), which is exactly the "ungrouped" case.
        ForeignKeyConstraint(
            ["family_id", "framework_id"],
            ["control_family.id", "control_family.framework_id"],
            ondelete="RESTRICT",
            name="fk_control_family_id_framework_id_control_family",
        ),
    )


class ControlEnhancement(Base):
    __tablename__ = "control_enhancement"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    control_id: Mapped[str] = mapped_column(
        ForeignKey("control.id", ondelete="RESTRICT"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    official_text: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("control_id", "identifier"),)


class Assessment(Base):
    __tablename__ = "assessment"

    id: Mapped[str] = mapped_column(String(ID_MAX), primary_key=True)
    framework_id: Mapped[str] = mapped_column(ForeignKey("framework.id", ondelete="RESTRICT"))
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
        Index("ix_assessment_framework_id", "framework_id"),
        Index("ix_assessment_created_at", "created_at"),
    )
