"""Release-scoped framework catalogs (S3-02).

Adds `catalog_release` (one imported version of a framework's catalog, with
provenance) and scopes all framework content to it: `control_family`,
`control` and `control_enhancement` are rebuilt release-scoped, and
`control_part`, `control_parameter`, `control_link`, `catalog_resource`,
`baseline` and `baseline_member` are new. `assessment` gains a pinned
`catalog_release_id`. See `docs/DATABASE.md` §8.

The three rebuilt tables were created by 0002 but never written (Sprint 2
loaded framework metadata only), so they are dropped and recreated rather
than altered. The upgrade refuses to run if any of them holds rows, so no
data can be lost silently.

Triggers make imported catalog content immutable and an assessment's
pinned release unchangeable once set (PD-013, PD-015). They are part of
the schema, so they hold for every writer, not only the service code.

Revision ID: 0003_catalog_releases
Revises: 0002_framework_assessment
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_catalog_releases"
down_revision: str | None = "0002_framework_assessment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REBUILT = ("control_enhancement", "control", "control_family")

#: Catalog content tables whose rows may never be updated once imported.
_IMMUTABLE_CONTENT = (
    "control_family",
    "control",
    "control_enhancement",
    "control_part",
    "control_parameter",
    "control_link",
    "catalog_resource",
    "baseline",
    "baseline_member",
)

#: `catalog_release` columns that are fixed at import. Only `is_current`
#: may change (a newer release becoming the default for new assessments).
_RELEASE_FIXED_COLUMNS = (
    "id",
    "framework_id",
    "version",
    "title",
    "source_format",
    "source_format_version",
    "source_last_modified",
    "source_file_name",
    "source_sha256",
    "source_repository_url",
    "source_release_url",
    "source_file_url",
    "source_tag",
    "source_commit",
    "license_notice",
    "attribution",
    "imported_at",
)


def _one_owner(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "(control_id IS NULL) <> (enhancement_id IS NULL)", name=op.f(f"ck_{table}_one_owner")
    )


def _release_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["catalog_release_id"],
        ["catalog_release.id"],
        name=op.f(f"fk_{table}_catalog_release_id_catalog_release"),
        ondelete="RESTRICT",
    )


def _owner_fks(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["control_id"],
            ["control.id"],
            name=op.f(f"fk_{table}_control_id_control"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enhancement_id"],
            ["control_enhancement.id"],
            name=op.f(f"fk_{table}_enhancement_id_control_enhancement"),
            ondelete="RESTRICT",
        ),
    ]


def _index(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _REBUILT:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
        if count:
            raise RuntimeError(f"{table} is not empty; refusing to rebuild it")

    op.drop_index(op.f("ix_control_enhancement_control_id"), table_name="control_enhancement")
    op.drop_table("control_enhancement")
    op.drop_index(op.f("ix_control_framework_id"), table_name="control")
    op.drop_index(op.f("ix_control_family_id"), table_name="control")
    op.drop_table("control")
    op.drop_index(op.f("ix_control_family_framework_id"), table_name="control_family")
    op.drop_table("control_family")

    op.create_table(
        "catalog_release",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_format", sa.String(length=50), nullable=False),
        sa.Column("source_format_version", sa.String(length=50), nullable=True),
        sa.Column("source_last_modified", sa.String(length=64), nullable=True),
        sa.Column("source_file_name", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_repository_url", sa.String(length=500), nullable=True),
        sa.Column("source_release_url", sa.String(length=500), nullable=True),
        sa.Column("source_file_url", sa.String(length=500), nullable=True),
        sa.Column("source_tag", sa.String(length=100), nullable=True),
        sa.Column("source_commit", sa.String(length=64), nullable=True),
        sa.Column("license_notice", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(source_sha256) = 64", name=op.f("ck_catalog_release_sha256_length")
        ),
        sa.CheckConstraint(
            "length(trim(version)) > 0", name=op.f("ck_catalog_release_version_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name=op.f("fk_catalog_release_framework_id_framework"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_release")),
        sa.UniqueConstraint(
            "framework_id", "version", name=op.f("uq_catalog_release_framework_id_version")
        ),
        sa.UniqueConstraint("id", "framework_id", name=op.f("uq_catalog_release_id_framework_id")),
    )
    _index("catalog_release", "framework_id")
    op.create_index(
        "uq_catalog_release_current",
        "catalog_release",
        ["framework_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "control_family",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_release_id", "framework_id"],
            ["catalog_release.id", "catalog_release.framework_id"],
            name=op.f("fk_control_family_catalog_release_id_framework_id_catalog_release"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_family")),
        sa.UniqueConstraint(
            "catalog_release_id",
            "identifier",
            name=op.f("uq_control_family_catalog_release_id_identifier"),
        ),
        sa.UniqueConstraint(
            "catalog_release_id",
            "source_id",
            name=op.f("uq_control_family_catalog_release_id_source_id"),
        ),
        sa.UniqueConstraint(
            "id", "catalog_release_id", name=op.f("uq_control_family_id_catalog_release_id")
        ),
    )
    _index("control_family", "catalog_release_id")

    op.create_table(
        "control",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("framework_id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_release_id", "framework_id"],
            ["catalog_release.id", "catalog_release.framework_id"],
            name=op.f("fk_control_catalog_release_id_framework_id_catalog_release"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["family_id", "catalog_release_id"],
            ["control_family.id", "control_family.catalog_release_id"],
            name="fk_control_family_id_release_control_family",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control")),
        sa.UniqueConstraint(
            "catalog_release_id",
            "identifier",
            name=op.f("uq_control_catalog_release_id_identifier"),
        ),
        sa.UniqueConstraint(
            "catalog_release_id", "source_id", name=op.f("uq_control_catalog_release_id_source_id")
        ),
        sa.UniqueConstraint(
            "id", "catalog_release_id", name=op.f("uq_control_id_catalog_release_id")
        ),
    )
    _index("control", "catalog_release_id", "family_id", "framework_id")

    op.create_table(
        "control_enhancement",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("control_id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_id", "catalog_release_id"],
            ["control.id", "control.catalog_release_id"],
            name=op.f("fk_control_enhancement_control_id_catalog_release_id_control"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_enhancement")),
        sa.UniqueConstraint(
            "control_id", "identifier", name=op.f("uq_control_enhancement_control_id_identifier")
        ),
        sa.UniqueConstraint(
            "catalog_release_id",
            "source_id",
            name=op.f("uq_control_enhancement_catalog_release_id_source_id"),
        ),
        sa.UniqueConstraint(
            "id",
            "catalog_release_id",
            name=op.f("uq_control_enhancement_id_catalog_release_id"),
        ),
    )
    _index("control_enhancement", "catalog_release_id", "control_id")

    op.create_table(
        "control_part",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=True),
        sa.Column("control_id", sa.String(length=64), nullable=True),
        sa.Column("enhancement_id", sa.String(length=64), nullable=True),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("prose", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(family_id IS NOT NULL) + (control_id IS NOT NULL) + (enhancement_id IS NOT NULL) = 1",
            name=op.f("ck_control_part_one_owner"),
        ),
        _release_fk("control_part"),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["control_family.id"],
            name=op.f("fk_control_part_family_id_control_family"),
            ondelete="RESTRICT",
        ),
        *_owner_fks("control_part"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["control_part.id"],
            name=op.f("fk_control_part_parent_id_control_part"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_part")),
    )
    _index(
        "control_part",
        "catalog_release_id",
        "family_id",
        "control_id",
        "enhancement_id",
        "parent_id",
    )

    op.create_table(
        "control_parameter",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("control_id", sa.String(length=64), nullable=True),
        sa.Column("enhancement_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=True),
        sa.Column("guidelines", sa.JSON(), nullable=True),
        sa.Column("select_how_many", sa.String(length=50), nullable=True),
        sa.Column("choices", sa.JSON(), nullable=True),
        _one_owner("control_parameter"),
        _release_fk("control_parameter"),
        *_owner_fks("control_parameter"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_parameter")),
        sa.UniqueConstraint(
            "catalog_release_id",
            "source_id",
            name=op.f("uq_control_parameter_catalog_release_id_source_id"),
        ),
    )
    _index("control_parameter", "catalog_release_id", "control_id", "enhancement_id")

    op.create_table(
        "control_link",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("control_id", sa.String(length=64), nullable=True),
        sa.Column("enhancement_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rel", sa.String(length=50), nullable=True),
        sa.Column("href", sa.String(length=2000), nullable=False),
        _one_owner("control_link"),
        _release_fk("control_link"),
        *_owner_fks("control_link"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_link")),
    )
    _index("control_link", "catalog_release_id", "control_id", "enhancement_id")

    op.create_table(
        "catalog_resource",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("citation", sa.Text(), nullable=True),
        sa.Column("href", sa.String(length=2000), nullable=True),
        _release_fk("catalog_resource"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_resource")),
        sa.UniqueConstraint(
            "catalog_release_id",
            "source_id",
            name=op.f("uq_catalog_resource_catalog_release_id_source_id"),
        ),
    )
    _index("catalog_resource", "catalog_release_id")

    op.create_table(
        "baseline",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("catalog_release_id", sa.String(length=64), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("source_file_name", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_file_url", sa.String(length=500), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        _release_fk("baseline"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baseline")),
        sa.UniqueConstraint(
            "catalog_release_id",
            "identifier",
            name=op.f("uq_baseline_catalog_release_id_identifier"),
        ),
    )
    _index("baseline", "catalog_release_id")

    op.create_table(
        "baseline_member",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("baseline_id", sa.String(length=64), nullable=False),
        sa.Column("control_id", sa.String(length=64), nullable=True),
        sa.Column("enhancement_id", sa.String(length=64), nullable=True),
        _one_owner("baseline_member"),
        sa.ForeignKeyConstraint(
            ["baseline_id"],
            ["baseline.id"],
            name=op.f("fk_baseline_member_baseline_id_baseline"),
            ondelete="RESTRICT",
        ),
        *_owner_fks("baseline_member"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baseline_member")),
        sa.UniqueConstraint(
            "baseline_id", "control_id", name=op.f("uq_baseline_member_baseline_id_control_id")
        ),
        sa.UniqueConstraint(
            "baseline_id",
            "enhancement_id",
            name=op.f("uq_baseline_member_baseline_id_enhancement_id"),
        ),
    )
    _index("baseline_member", "baseline_id", "control_id", "enhancement_id")

    # `assessment` holds user data: batch mode copies every row into the
    # rebuilt table (SQLite cannot add a foreign key in place).
    with op.batch_alter_table("assessment", schema=None) as batch_op:
        batch_op.add_column(sa.Column("catalog_release_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_assessment_catalog_release_id_framework_id_catalog_release"),
            "catalog_release",
            ["catalog_release_id", "framework_id"],
            ["id", "framework_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_assessment_catalog_release_id", ["catalog_release_id"], unique=False
        )

    _create_triggers()


def _create_triggers() -> None:
    for table in _IMMUTABLE_CONTENT:
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'catalog_content_immutable'); END"
        )
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _RELEASE_FIXED_COLUMNS)
    op.execute(
        "CREATE TRIGGER trg_catalog_release_immutable BEFORE UPDATE ON catalog_release "
        f"WHEN {changed} "
        "BEGIN SELECT RAISE(ABORT, 'catalog_content_immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_assessment_release_pinned BEFORE UPDATE OF catalog_release_id "
        "ON assessment WHEN OLD.catalog_release_id IS NOT NULL "
        "AND NEW.catalog_release_id IS NOT OLD.catalog_release_id "
        "BEGIN SELECT RAISE(ABORT, 'assessment_catalog_release_pinned'); END"
    )


def _drop_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_assessment_release_pinned")
    op.execute("DROP TRIGGER IF EXISTS trg_catalog_release_immutable")
    for table in _IMMUTABLE_CONTENT:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable")


def downgrade() -> None:
    _drop_triggers()
    with op.batch_alter_table("assessment", schema=None) as batch_op:
        batch_op.drop_index("ix_assessment_catalog_release_id")
        batch_op.drop_constraint(
            batch_op.f("fk_assessment_catalog_release_id_framework_id_catalog_release"),
            type_="foreignkey",
        )
        batch_op.drop_column("catalog_release_id")

    for table in (
        "baseline_member",
        "baseline",
        "catalog_resource",
        "control_link",
        "control_parameter",
        "control_part",
        "control_enhancement",
        "control",
        "control_family",
        "catalog_release",
    ):
        op.drop_table(table)

    # Recreate the 0002 shapes (empty, as 0002 left them).
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
