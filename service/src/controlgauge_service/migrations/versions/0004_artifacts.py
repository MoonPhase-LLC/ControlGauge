"""Evidence artifacts and their extracted sections (Sprint 4, S4-02).

Adds `artifact` (one uploaded file in an assessment; unique per assessment
by SHA-256 for duplicate detection) and `artifact_section` (extracted,
chunked text with a locator). See `docs/DATABASE.md` §9.

Triggers: an artifact's identity (assessment, hash, size, format, name) is
fixed once stored, and sections are never updated -- a re-parse would add
a new artifact version in a later sprint, not rewrite evidence text.

Revision ID: 0004_artifacts
Revises: 0003_catalog_releases
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_artifacts"
down_revision: str | None = "0003_catalog_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARTIFACT_FIXED = ("id", "assessment_id", "filename", "sha256_hash", "size_bytes", "format")


def upgrade() -> None:
    op.create_table(
        "artifact",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("assessment_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("classification", sa.String(length=100), nullable=True),
        sa.Column("classification_source", sa.String(length=20), nullable=True),
        sa.Column("parse_status", sa.String(length=30), nullable=False),
        sa.Column("parse_detail", sa.String(length=50), nullable=True),
        sa.Column("parser_version", sa.String(length=50), nullable=True),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "classification_source IS NULL OR classification_source IN ('ai', 'analyst')",
            name=op.f("ck_artifact_classification_source_known"),
        ),
        sa.CheckConstraint(
            "format IN ('pdf', 'docx', 'txt', 'csv')", name=op.f("ck_artifact_format_known")
        ),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'parsing', 'parsed', 'partial', 'empty', "
            "'unsupported_format', 'failed')",
            name=op.f("ck_artifact_parse_status_known"),
        ),
        sa.CheckConstraint("length(filename) <= 255", name=op.f("ck_artifact_filename_max_length")),
        sa.CheckConstraint("length(sha256_hash) = 64", name=op.f("ck_artifact_sha256_length")),
        sa.CheckConstraint(
            "length(trim(filename)) > 0", name=op.f("ck_artifact_filename_not_blank")
        ),
        sa.CheckConstraint(
            "section_count >= 0", name=op.f("ck_artifact_section_count_not_negative")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_artifact_size_not_negative")),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessment.id"],
            name=op.f("fk_artifact_assessment_id_assessment"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact")),
        sa.UniqueConstraint(
            "assessment_id", "sha256_hash", name=op.f("uq_artifact_assessment_id_sha256_hash")
        ),
    )
    op.create_index("ix_artifact_assessment_id", "artifact", ["assessment_id"], unique=False)
    op.create_index("ix_artifact_parse_status", "artifact", ["parse_status"], unique=False)

    op.create_table(
        "artifact_section",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("locator", sa.String(length=300), nullable=False),
        sa.CheckConstraint(
            "length(locator) <= 300", name=op.f("ck_artifact_section_locator_max_length")
        ),
        sa.CheckConstraint(
            "sequence_index >= 0", name=op.f("ck_artifact_section_sequence_index_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifact.id"],
            name=op.f("fk_artifact_section_artifact_id_artifact"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact_section")),
        sa.UniqueConstraint(
            "artifact_id",
            "sequence_index",
            name=op.f("uq_artifact_section_artifact_id_sequence_index"),
        ),
    )
    op.create_index(
        "ix_artifact_section_artifact_id", "artifact_section", ["artifact_id"], unique=False
    )

    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _ARTIFACT_FIXED)
    op.execute(
        "CREATE TRIGGER trg_artifact_identity_fixed BEFORE UPDATE ON artifact "
        f"WHEN {changed} "
        "BEGIN SELECT RAISE(ABORT, 'artifact_identity_fixed'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_artifact_section_immutable BEFORE UPDATE ON artifact_section "
        "BEGIN SELECT RAISE(ABORT, 'artifact_section_immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_artifact_section_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_artifact_identity_fixed")
    op.drop_index("ix_artifact_section_artifact_id", table_name="artifact_section")
    op.drop_table("artifact_section")
    op.drop_index("ix_artifact_parse_status", table_name="artifact")
    op.drop_index("ix_artifact_assessment_id", table_name="artifact")
    op.drop_table("artifact")
