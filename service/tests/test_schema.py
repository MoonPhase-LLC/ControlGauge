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
    CatalogRelease,
    Control,
    ControlEnhancement,
    ControlFamily,
    ControlPart,
    Framework,
)
from controlgauge_service.framework_catalog import (
    FrameworkMetadata,
    default_frameworks_root,
    load_framework_metadata,
    sync_frameworks,
)

from .conftest import SeedDataDir

SRC = Path(__file__).resolve().parents[1] / "src" / "controlgauge_service"


@pytest.fixture
def database(tmp_path: Path, seed_data_dir: SeedDataDir) -> Iterator[Database]:
    db = initialize_database(seed_data_dir(tmp_path))
    try:
        yield db
    finally:
        db.dispose()


def _add_framework(db: Database, framework_id: str = "fw-a", name: str = "Framework A") -> None:
    with db.session(write=True) as s:
        s.add(Framework(id=framework_id, name=name, version="1", source_reference=None))


def _add_release(
    db: Database, release_id: str = "rel-a", framework_id: str = "fw-a", version: str = "1"
) -> None:
    with db.session(write=True) as s:
        s.add(
            CatalogRelease(
                id=release_id,
                framework_id=framework_id,
                version=version,
                title="Release",
                source_format="test",
                source_file_name="catalog.json",
                source_sha256="0" * 64,
                license_notice="notice",
                attribution="attribution",
            )
        )


def _control(
    control_id: str,
    identifier: str,
    *,
    family_id: str | None = None,
    framework_id: str = "fw-a",
    release_id: str = "rel-a",
) -> Control:
    return Control(
        id=control_id,
        framework_id=framework_id,
        catalog_release_id=release_id,
        family_id=family_id,
        source_id=identifier.lower(),
        identifier=identifier,
        title="t",
        position=0,
    )


def test_upgrade_from_empty_creates_every_table(database: Database) -> None:
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "alembic_version",
        "framework",
        "catalog_release",
        "control_family",
        "control",
        "control_enhancement",
        "control_part",
        "control_parameter",
        "control_link",
        "catalog_resource",
        "baseline",
        "baseline_member",
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
        "catalog_release_id": False,
        "source_id": False,
        "identifier": False,
        "name": False,
        "position": False,
    }
    assert cols("control") == {
        "id": False,
        "framework_id": False,
        "catalog_release_id": False,
        "family_id": True,  # D-016: a control may attach directly to its framework
        "source_id": False,
        "identifier": False,
        "title": False,
        "status": True,
        "position": False,
    }
    assert cols("control_enhancement") == {
        "id": False,
        "control_id": False,
        "catalog_release_id": False,
        "source_id": False,
        "identifier": False,
        "title": False,
        "status": True,
        "position": False,
    }
    assert cols("assessment") == {
        "id": False,
        "framework_id": False,
        "catalog_release_id": True,
        "name": False,
        "description": True,
        "created_at": False,
    }


def test_startup_imports_the_shipped_catalog_into_one_current_release(
    database: Database,
) -> None:
    with database.session() as s:
        releases = s.scalars(select(CatalogRelease)).all()
        assert [(r.framework_id, r.version, r.is_current) for r in releases] == [
            ("nist-800-53-rev5", "5.2.0", True)
        ]
        for model in (ControlFamily, Control, ControlEnhancement):
            assert (s.scalar(select(func.count()).select_from(model)) or 0) > 0


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


def test_control_family_must_belong_to_the_controls_release(database: Database) -> None:
    _add_framework(database, "fw-a", "Framework A")
    _add_release(database, "rel-a", "fw-a", "1")
    _add_release(database, "rel-a2", "fw-a", "2")
    with database.session(write=True) as s:
        s.add(
            ControlFamily(
                id="fam-a",
                framework_id="fw-a",
                catalog_release_id="rel-a",
                source_id="f",
                identifier="F",
                name="Family",
                position=0,
            )
        )
    # Same release: allowed.
    with database.session(write=True) as s:
        s.add(_control("c1", "C-1", family_id="fam-a"))
    # Family from a different release: rejected by the composite FK.
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(_control("c2", "C-1", family_id="fam-a", release_id="rel-a2"))


def test_release_must_belong_to_the_controls_framework(database: Database) -> None:
    _add_framework(database, "fw-a", "Framework A")
    _add_framework(database, "fw-b", "Framework B")
    _add_release(database, "rel-a", "fw-a")
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(_control("c1", "C-1", framework_id="fw-b", release_id="rel-a"))


def test_control_without_family_attaches_to_framework(database: Database) -> None:
    _add_framework(database)
    _add_release(database)
    with database.session(write=True) as s:
        s.add(_control("c1", "X"))
    with database.session(write=True) as s:
        s.add(
            ControlEnhancement(
                id="e1",
                control_id="c1",
                catalog_release_id="rel-a",
                source_id="x.1",
                identifier="X(1)",
                title="t",
                position=0,
            )
        )


def test_control_identifier_unique_per_release(database: Database) -> None:
    _add_framework(database)
    _add_release(database, "rel-a", version="1")
    _add_release(database, "rel-b", version="2")
    with database.session(write=True) as s:
        s.add(_control("c1", "X"))
        s.add(_control("c-other-release", "X", release_id="rel-b"))  # allowed
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(_control("c2", "X"))


def test_enhancement_requires_existing_control(database: Database) -> None:
    _add_framework(database)
    _add_release(database)
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(
            ControlEnhancement(
                id="e1",
                control_id="missing",
                catalog_release_id="rel-a",
                source_id="x",
                identifier="X",
                title="t",
                position=0,
            )
        )


@pytest.mark.parametrize(
    "owners",
    [{}, {"control_id": "c1", "enhancement_id": None, "family_id": "fam-x"}],
)
def test_part_needs_exactly_one_owner(database: Database, owners: dict[str, str | None]) -> None:
    _add_framework(database)
    _add_release(database)
    with database.session(write=True) as s:
        s.add(_control("c1", "X"))
        s.add(
            ControlFamily(
                id="fam-x",
                framework_id="fw-a",
                catalog_release_id="rel-a",
                source_id="fx",
                identifier="FX",
                name="Family",
                position=0,
            )
        )
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(ControlPart(id="p1", catalog_release_id="rel-a", position=0, name="n", **owners))


# --- immutability (PD-013 / PD-015) ---------------------------------------------

_CONTENT_UPDATES = [
    "UPDATE control SET title = 'changed'",
    "UPDATE control_part SET prose = 'changed'",
    "UPDATE control_enhancement SET status = NULL",
    "UPDATE control_parameter SET label = 'changed'",
    "UPDATE control_link SET href = '#x'",
    "UPDATE control_family SET name = 'changed'",
    "UPDATE baseline SET title = 'changed'",
    "UPDATE baseline_member SET control_id = control_id",
    "UPDATE catalog_resource SET title = 'changed'",
    "UPDATE catalog_release SET source_sha256 = '" + "1" * 64 + "'",
    "UPDATE catalog_release SET version = '9.9.9'",
]


@pytest.mark.parametrize("statement", _CONTENT_UPDATES)
def test_imported_catalog_content_cannot_be_updated(database: Database, statement: str) -> None:
    with pytest.raises(IntegrityError, match="catalog_content_immutable"):
        with database.session(write=True) as s:
            s.execute(text(statement))


def test_release_current_flag_may_change(database: Database) -> None:
    with database.session(write=True) as s:
        s.execute(text("UPDATE catalog_release SET is_current = 0"))
        s.execute(text("UPDATE catalog_release SET is_current = 1"))


def test_only_one_current_release_per_framework(database: Database) -> None:
    _add_framework(database)
    _add_release(database, "rel-a", version="1")
    _add_release(database, "rel-b", version="2")
    with database.session(write=True) as s:
        s.execute(text("UPDATE catalog_release SET is_current = 1 WHERE id = 'rel-a'"))
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.execute(text("UPDATE catalog_release SET is_current = 1 WHERE id = 'rel-b'"))


@pytest.mark.parametrize("new_value", ["'rel-b'", "NULL"])
def test_pinned_assessment_release_cannot_change(database: Database, new_value: str) -> None:
    _add_framework(database)
    _add_release(database, "rel-a", version="1")
    _add_release(database, "rel-b", version="2")
    with database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-a", name="x"))
    # Unpinned -> pinned is allowed once.
    with database.session(write=True) as s:
        s.execute(text("UPDATE assessment SET catalog_release_id = 'rel-a' WHERE id = 'a1'"))
    with pytest.raises(IntegrityError, match="assessment_catalog_release_pinned"):
        with database.session(write=True) as s:
            s.execute(text(f"UPDATE assessment SET catalog_release_id = {new_value}"))  # noqa: S608
    # Other fields stay editable at the database level.
    with database.session(write=True) as s:
        s.execute(text("UPDATE assessment SET name = 'renamed' WHERE id = 'a1'"))


def test_assessment_cannot_pin_a_release_of_another_framework(database: Database) -> None:
    _add_framework(database, "fw-a", "Framework A")
    _add_framework(database, "fw-b", "Framework B")
    _add_release(database, "rel-b", "fw-b")
    with pytest.raises(IntegrityError), database.session(write=True) as s:
        s.add(Assessment(id="a1", framework_id="fw-a", catalog_release_id="rel-b", name="x"))


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
