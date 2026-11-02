"""S2-02: framework/control/assessment schema, constraints, metadata sync."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from controlgauge_service.db import Database, initialize_database
from controlgauge_service.db.models import (
    Assessment,
    Control,
    ControlEnhancement,
    ControlFamily,
    Framework,
)
from controlgauge_service.framework_catalog import (
    FrameworkMetadata,
    default_frameworks_root,
    load_framework_metadata,
    sync_frameworks,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "controlgauge_service"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = initialize_database(tmp_path)
    try:
        yield db
    finally:
        db.dispose()


def _add_framework(db: Database, framework_id: str = "fw-a", name: str = "Framework A") -> None:
    with db.session(write=True) as s:
        s.add(Framework(id=framework_id, name=name, version="1", source_reference=None))


def test_upgrade_from_empty_creates_every_sprint_2_table(database: Database) -> None:
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "alembic_version",
        "framework",
        "control_family",
        "control",
        "control_enhancement",
        "assessment",
    } <= tables


def test_column_shape_matches_database_md(database: Database) -> None:
    insp = inspect(database.engine)

    def cols(table: str) -> dict[str, bool]:
        return {c["name"]: c["nullable"] for c in insp.get_columns(table)}

    assert cols("framework") == {
        "id": False,
        "name": False,
        "version": False,
        "source_reference": True,
    }
    assert cols("control_family") == {
        "id": False,
        "framework_id": False,
        "identifier": False,
        "name": False,
    }
    assert cols("control") == {
        "id": False,
        "framework_id": False,
        "family_id": True,  # D-016: a control may attach directly to its framework
        "identifier": False,
        "title": False,
        "official_text": True,
    }
    assert cols("control_enhancement") == {
        "id": False,
        "control_id": False,
        "identifier": False,
        "title": False,
        "official_text": True,
    }
    assert cols("assessment") == {
        "id": False,
        "framework_id": False,
        "name": False,
        "description": True,
        "created_at": False,
    }


def test_no_control_content_is_inserted(database: Database) -> None:
    with database.session() as s:
        for model in (ControlFamily, Control, ControlEnhancement):
            assert s.scalar(select(func.count()).select_from(model)) == 0


def test_startup_registers_framework_metadata_from_files(database: Database) -> None:
    with database.session() as s:
        frameworks = s.scalars(select(Framework)).all()
    assert [f.id for f in frameworks] == ["nist-800-53-rev5"]
    assert frameworks[0].name == "NIST SP 800-53"
    assert frameworks[0].version == "Revision 5"


def _non_docstring_strings(tree: ast.AST) -> Iterator[str]:
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.value


def test_service_code_is_not_nist_hardcoded() -> None:
    """ARCHITECTURE §8: framework specifics live in data files, never in code.
    Docstrings may mention NIST; no executable string literal may."""
    pattern = re.compile(r"nist|800-53", re.IGNORECASE)
    hits = [
        f"{path.relative_to(SRC)}: {value!r}"
        for path in SRC.rglob("*.py")
        for value in _non_docstring_strings(ast.parse(path.read_text(encoding="utf-8")))
        if pattern.search(value)
    ]
    assert hits == []


def test_foreign_keys_reject_missing_framework(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="does-not-exist", name="x"))


def test_framework_in_use_cannot_be_deleted(database: Database) -> None:
    _add_framework(database)
    with database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-a", name="x"))
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.execute(text("DELETE FROM framework WHERE id = 'fw-a'"))


def test_control_family_must_belong_to_the_controls_framework(database: Database) -> None:
    _add_framework(database, "fw-a", "Framework A")
    _add_framework(database, "fw-b", "Framework B")
    with database.session(write=True) as s:
        s.add(ControlFamily(id="fam-a", framework_id="fw-a", identifier="F", name="Family"))
    # Same framework: allowed.
    with database.session(write=True) as s:
        s.add(Control(id="c1", framework_id="fw-a", family_id="fam-a", identifier="C-1", title="t"))
    # Family from a different framework: rejected by the composite FK.
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(Control(id="c2", framework_id="fw-b", family_id="fam-a", identifier="C-1", title="t"))


def test_control_without_family_attaches_to_framework(database: Database) -> None:
    _add_framework(database)
    with database.session(write=True) as s:
        s.add(Control(id="c1", framework_id="fw-a", family_id=None, identifier="X", title="t"))
        s.add(ControlEnhancement(id="e1", control_id="c1", identifier="X(1)", title="t"))


def test_control_identifier_unique_per_framework(database: Database) -> None:
    _add_framework(database)
    with database.session(write=True) as s:
        s.add(Control(id="c1", framework_id="fw-a", identifier="X", title="t"))
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(Control(id="c2", framework_id="fw-a", identifier="X", title="t"))


def test_enhancement_requires_existing_control(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(ControlEnhancement(id="e1", control_id="missing", identifier="X", title="t"))


@pytest.mark.parametrize(
    ("name", "description"),
    [("   ", None), ("x" * 201, None), ("ok", "d" * 2001)],
)
def test_assessment_check_constraints(database: Database, name: str, description: str) -> None:
    _add_framework(database)
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-a", name=name, description=description))


def test_created_at_round_trips_as_utc(database: Database) -> None:
    _add_framework(database)
    local = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-6)))
    with database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-a", name="x", created_at=local))
    with database.session() as s:
        stored = s.get(Assessment, "a1")
        assert stored is not None
        assert stored.created_at == local
        assert stored.created_at.tzinfo == UTC


def test_naive_datetime_is_rejected(database: Database) -> None:
    _add_framework(database)
    with pytest.raises(StatementError), database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-a", name="x", created_at=datetime(2026, 1, 1)))


def test_writes_use_begin_immediate(database: Database) -> None:
    statements: list[str] = []
    engine: Engine = database.engine

    from sqlalchemy import event

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with database.session(write=True) as s:
            s.execute(text("SELECT 1"))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert "BEGIN IMMEDIATE" in statements


# --- framework metadata files (untrusted input) ---------------------------------


def _write(root: Path, directory: str, payload: object) -> None:
    (root / directory).mkdir(parents=True)
    (root / directory / "framework.json").write_text(json.dumps(payload), encoding="utf-8")


def test_repository_metadata_file_is_found_and_valid() -> None:
    root = default_frameworks_root()
    assert root is not None
    ids = [m.id for m in load_framework_metadata(root)]
    assert ids == ["nist-800-53-rev5"]


def test_invalid_metadata_files_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "good", {"id": "good", "name": "Good", "version": "1"})
    _write(tmp_path, "extra", {"id": "extra", "name": "E", "version": "1", "official_text": "x"})
    _write(tmp_path, "bad-id", {"id": "Bad ID!", "name": "B", "version": "1"})
    _write(tmp_path, "blank", {"id": "blank", "name": "  ", "version": "1"})
    _write(tmp_path, "dupe", {"id": "good", "name": "Dupe", "version": "2"})
    (tmp_path / "notjson").mkdir()
    (tmp_path / "notjson" / "framework.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "huge").mkdir()
    (tmp_path / "huge" / "framework.json").write_text(" " * (65 * 1024), encoding="utf-8")

    ids = [m.id for m in load_framework_metadata(tmp_path)]
    assert ids == ["good"]


def test_sync_inserts_then_updates_without_deleting(database: Database) -> None:
    first = FrameworkMetadata(id="fw-x", name="X", version="1", source_reference="ref")
    assert sync_frameworks(database, [first]) == 2  # plus the repository framework
    with database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-x", name="kept"))

    renamed = FrameworkMetadata(id="fw-x", name="X renamed", version="1.1")
    sync_frameworks(database, [renamed])
    sync_frameworks(database, [])  # absence never deletes
    with database.session() as s:
        row = s.get(Framework, "fw-x")
        assert row is not None
        assert (row.name, row.version, row.source_reference) == ("X renamed", "1.1", None)
        assert s.get(Assessment, "a1") is not None
