"""User-managed model registry + external endpoints (Sprint 6, S6-05).

Persists the curated-catalog vs user-managed-registry distinction
(`docs/MODEL_RUNTIME.md` §3.1, PD-026):

- `model_file` gains `origin` (where the content came from) and
  `verification` (how strongly it was verified), backfilled for existing
  Sprint 5 rows as `local_import`/`none` -- an honest description of every
  pre-0006 import. SQLite has no ``ALTER TABLE ... ADD CONSTRAINT``, so the
  change is a deterministic table recreate (rename -> create with the full
  new shape -> copy -> drop), never a reflection-dependent batch recreate.
- `model_source` (1:1 with `model_file`) carries full provenance:
  catalog model id, source URL, revision, license notice, and
  `files_json` -- a JSON array of `{filename, sha256, size_bytes}`
  covering ALL files of a model (e.g. both shards of a split GGUF).
  Backfill: one row per existing `model_file`, `files_json` built from
  that row's own hash/size with `filename = PurePath(path).name`.
- `external_endpoint` records a user-registered LOOPBACK OpenAI-compatible
  local inference server and its last health result. **No credential
  column** -- V0.1 registers unauthenticated loopback endpoints only;
  whether/where to store endpoint credentials is an owner decision
  (`docs/OPEN_QUESTIONS.md`), not invented here.

Hardware detection results are deliberately NOT in this migration:
machine facts are cached as JSON in the data dir (`hardware.json`,
`MODEL_RUNTIME.md` §4), never in the assessment database.

Revision ID: 0006_model_registry
Revises: 0005_model_files
Create Date: 2026-11-22
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from pathlib import PurePath
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0006_model_registry"
down_revision: str | None = "0005_model_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGINS = "('local_import', 'catalog_download', 'url_import')"
_VERIFICATIONS = "('none', 'user_checksum', 'catalog_manifest')"
_BACKENDS = "('llama_cpp', 'openai_compatible')"
_CAPABILITIES = "('generation', 'embedding', 'both')"
_HEALTH_STATES = "('ready', 'unhealthy', 'unreachable')"

#: The exact `0005_model_files` `model_file` shape, restated so both the
#: upgrade (add origin/verification) and the downgrade (remove them) copy
#: through a fully specified table -- no reflection of CHECK constraints,
#: which SQLite's dialect does not reliably provide.
_MODEL_FILE_0005_COLUMNS = [
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
]

_COPY_COLUMNS_0005 = (
    "id, role, display_name, path, sha256_hash, size_bytes, imported_at, "
    "last_verified_at, embedding_dimensions, embedding_normalization, "
    "embedding_similarity_metric"
)


def _model_file_0005_constraints() -> list[Any]:
    """The exact `0005` constraint set, built LAZILY: `op.f()` resolves
    through alembic's migration-context proxy, which exists only while a
    migration is running -- never at module import time."""
    return [
        sa.CheckConstraint(
            "embedding_normalization IS NULL OR embedding_normalization IN ('none', 'l2')",
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
        sa.UniqueConstraint("sha256_hash", "role", name=op.f("uq_model_file_sha256_hash_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_file")),
    ]


def _recreate_model_file(*, with_registry_columns: bool) -> None:
    """Rename the live `model_file` aside, create it in the target shape,
    copy every row, drop the old table. `with_registry_columns` selects the
    0006 shape (origin/verification) vs the 0005 shape."""
    op.rename_table("model_file", "model_file_0006_old")
    columns: list[Any] = list(_MODEL_FILE_0005_COLUMNS)
    constraints: list[Any] = _model_file_0005_constraints()
    if with_registry_columns:
        columns.append(
            sa.Column(
                "origin",
                sa.String(length=32),
                nullable=False,
                server_default="local_import",
            )
        )
        columns.append(
            sa.Column(
                "verification",
                sa.String(length=32),
                nullable=False,
                server_default="none",
            )
        )
        constraints.append(
            sa.CheckConstraint(f"origin IN {_ORIGINS}", name=op.f("ck_model_file_origin_known"))
        )
        constraints.append(
            sa.CheckConstraint(
                f"verification IN {_VERIFICATIONS}",
                name=op.f("ck_model_file_verification_known"),
            )
        )
    op.create_table("model_file", *columns, *constraints)
    if with_registry_columns:
        op.execute(
            f"INSERT INTO model_file ({_COPY_COLUMNS_0005}, origin, verification) "  # noqa: S608 - column list is a module constant
            f"SELECT {_COPY_COLUMNS_0005}, 'local_import', 'none' FROM model_file_0006_old"
        )
    else:
        op.execute(
            f"INSERT INTO model_file ({_COPY_COLUMNS_0005}) "  # noqa: S608 - column list is a module constant
            f"SELECT {_COPY_COLUMNS_0005} FROM model_file_0006_old"
        )
    op.drop_table("model_file_0006_old")


def upgrade() -> None:
    # 1. model_file gains origin/verification, backfilled via the defaults.
    _recreate_model_file(with_registry_columns=True)

    # 2. model_source: full provenance, 1:1 with model_file.
    op.create_table(
        "model_source",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("model_file_id", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("catalog_model_id", sa.String(length=100), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("revision", sa.String(length=40), nullable=True),
        sa.Column("license_notice", sa.String(length=2000), nullable=True),
        sa.Column("files_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(f"origin IN {_ORIGINS}", name=op.f("ck_model_source_origin_known")),
        sa.ForeignKeyConstraint(
            ["model_file_id"],
            ["model_file.id"],
            name=op.f("fk_model_source_model_file_id_model_file"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_source")),
        sa.UniqueConstraint("model_file_id", name=op.f("uq_model_source_model_file_id")),
    )
    op.create_index(
        op.f("ix_model_source_catalog_model_id"),
        "model_source",
        ["catalog_model_id"],
        unique=False,
    )

    # Backfill: one row per existing model_file, built from that row's own
    # identity (a Sprint 5 import is a single local file). created_at/
    # updated_at ride the row's own imported_at so the backfill is
    # deterministic.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, path, sha256_hash, size_bytes, imported_at FROM model_file")
    ).fetchall()
    for row in rows:
        filename = PurePath(row.path).name
        files_json = json.dumps(
            [{"filename": filename, "sha256": row.sha256_hash, "size_bytes": row.size_bytes}]
        )
        connection.execute(
            sa.text(
                "INSERT INTO model_source (id, model_file_id, origin, catalog_model_id, "
                "source_url, revision, license_notice, files_json, created_at, updated_at) "
                "VALUES (:id, :model_file_id, 'local_import', NULL, NULL, NULL, NULL, "
                ":files_json, :imported_at, :imported_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "model_file_id": row.id,
                "files_json": files_json,
                "imported_at": row.imported_at,
            },
        )

    # 3. external_endpoint: loopback local inference servers + last health.
    op.create_table(
        "external_endpoint",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("capabilities", sa.String(length=32), nullable=False),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.Column("last_health_at", sa.DateTime(), nullable=True),
        sa.Column("last_health_state", sa.String(length=20), nullable=True),
        sa.Column("last_health_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0",
            name=op.f("ck_external_endpoint_display_name_not_blank"),
        ),
        sa.CheckConstraint(
            "length(display_name) <= 255",
            name=op.f("ck_external_endpoint_display_name_max_length"),
        ),
        sa.CheckConstraint(
            f"backend IN {_BACKENDS}", name=op.f("ck_external_endpoint_backend_known")
        ),
        sa.CheckConstraint(
            f"capabilities IN {_CAPABILITIES}",
            name=op.f("ck_external_endpoint_capabilities_known"),
        ),
        sa.CheckConstraint(
            "last_health_state IS NULL OR last_health_state IN " + _HEALTH_STATES,
            name=op.f("ck_external_endpoint_last_health_state_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_endpoint")),
    )
    op.create_index(
        op.f("ix_external_endpoint_backend_base_url_capabilities"),
        "external_endpoint",
        ["backend", "base_url", "capabilities"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("external_endpoint")
    op.drop_table("model_source")
    _recreate_model_file(with_registry_columns=False)
