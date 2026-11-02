"""Baseline: empty schema (S2-01).

Establishes Alembic version tracking on a fresh database before any table
exists, so every later schema change is a versioned migration.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
