"""Imported model files (Sprint 5, S5-05).

Adds `model_file`: one user-imported GGUF file the model runtime can run.
The model-management screen must list previously imported models across app
restarts (USER_FLOWS §12), and the learn-and-pinned embedding dimension
(S5-04) must survive restarts -- hence one persisted table, nothing more.

`path` is the machine-local absolute path as imported; the database is the
one sanctioned place for it (it contains the OS user name and is never
logged, never sent to the frontend). `display_name` is cleaned display text
only and is never used as a path (T-05 discipline, like `artifact.filename`).

Revision ID: 0005_model_files
Revises: 0004_artifacts
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_model_files"
down_revision: str | None = "0004_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_file",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("embedding_normalization", sa.String(length=20), nullable=True),
        sa.Column("embedding_similarity_metric", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "embedding_normalization IS NULL OR " "embedding_normalization IN ('none', 'l2')",
            name=op.f("ck_model_file_embedding_normalization_known"),
        ),
        sa.CheckConstraint(
            "embedding_similarity_metric IS NULL OR "
            "embedding_similarity_metric IN ('cosine', 'dot_product')",
            name=op.f("ck_model_file_embedding_similarity_metric_known"),
        ),
        sa.CheckConstraint("length(sha256_hash) = 64", name=op.f("ck_model_file_sha256_length")),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0", name=op.f("ck_model_file_display_name_not_blank")
        ),
        sa.CheckConstraint(
            "length(display_name) <= 255", name=op.f("ck_model_file_display_name_max_length")
        ),
        sa.CheckConstraint(
            "role IN ('generation', 'embedding')", name=op.f("ck_model_file_role_known")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_model_file_size_not_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_file")),
        sa.UniqueConstraint("sha256_hash", "role", name=op.f("uq_model_file_sha256_hash_role")),
    )


def downgrade() -> None:
    op.drop_table("model_file")
