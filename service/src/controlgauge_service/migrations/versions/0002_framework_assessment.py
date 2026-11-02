"""Framework/control schema and assessment (S2-02).

Creates `framework`, `control_family`, `control`, `control_enhancement` and
`assessment` per `docs/DATABASE.md` (deviations listed in its §7). Inserts
no framework content: framework metadata rows are synced from
`frameworks/*/framework.json` at startup, and control catalogs are Sprint 3.

Revision ID: 0002_framework_assessment
Revises: 0001_baseline
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_framework_assessment"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "framework",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.CheckConstraint("length(trim(id)) > 0", name=op.f("ck_framework_id_not_blank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_framework_name_not_blank")),
        sa.CheckConstraint(
            "length(trim(version)) > 0", name=op.f("ck_framework_version_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_framework")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_framework_name_version")),
    )

    op.create_table(
        "control_family",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name=op.f("fk_control_family_framework_id_framework"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_family")),
        sa.UniqueConstraint(
            "framework_id", "identifier", name=op.f("uq_control_family_framework_id_identifier")
        ),
        sa.UniqueConstraint("id", "framework_id", name=op.f("uq_control_family_id_framework_id")),
    )
    op.create_index(
        op.f("ix_control_family_framework_id"), "control_family", ["framework_id"], unique=False
    )

    op.create_table(
        "control",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=True),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("official_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["family_id", "framework_id"],
            ["control_family.id", "control_family.framework_id"],
            name="fk_control_family_id_framework_id_control_family",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name=op.f("fk_control_framework_id_framework"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control")),
        sa.UniqueConstraint(
            "framework_id", "identifier", name=op.f("uq_control_framework_id_identifier")
        ),
    )
    op.create_index(op.f("ix_control_family_id"), "control", ["family_id"], unique=False)
    op.create_index(op.f("ix_control_framework_id"), "control", ["framework_id"], unique=False)

    op.create_table(
        "control_enhancement",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("control_id", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("official_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["control_id"],
            ["control.id"],
            name=op.f("fk_control_enhancement_control_id_control"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_enhancement")),
        sa.UniqueConstraint(
            "control_id", "identifier", name=op.f("uq_control_enhancement_control_id_identifier")
        ),
    )
    op.create_index(
        op.f("ix_control_enhancement_control_id"),
        "control_enhancement",
        ["control_id"],
        unique=False,
    )

    op.create_table(
        "assessment",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name=op.f("ck_assessment_description_max_length"),
        ),
        sa.CheckConstraint("length(name) <= 200", name=op.f("ck_assessment_name_max_length")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_assessment_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name=op.f("fk_assessment_framework_id_framework"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment")),
    )
    op.create_index("ix_assessment_created_at", "assessment", ["created_at"], unique=False)
    op.create_index("ix_assessment_framework_id", "assessment", ["framework_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_assessment_framework_id", table_name="assessment")
    op.drop_index("ix_assessment_created_at", table_name="assessment")
    op.drop_table("assessment")
    op.drop_index(op.f("ix_control_enhancement_control_id"), table_name="control_enhancement")
    op.drop_table("control_enhancement")
    op.drop_index(op.f("ix_control_framework_id"), table_name="control")
    op.drop_index(op.f("ix_control_family_id"), table_name="control")
    op.drop_table("control")
    op.drop_index(op.f("ix_control_family_framework_id"), table_name="control_family")
    op.drop_table("control_family")
    op.drop_table("framework")
