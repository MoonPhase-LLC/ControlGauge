"""S3-03: catalog import into the database, startup sync, version pinning.

Synthetic frameworks prove the importer is generic; the shipped catalog is
checked for completeness and verbatim fidelity against its own source file.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from controlgauge_service.catalog_import import CatalogImportError, sync_catalog
from controlgauge_service.db import Database, initialize_database
from controlgauge_service.db.models import (
    Assessment,
    Baseline,
    BaselineMember,
    CatalogRelease,
    CatalogResource,
    Control,
    ControlEnhancement,
    ControlFamily,
    ControlLink,
    ControlParameter,
    ControlPart,
)
from controlgauge_service.framework_catalog import (
    FrameworkMetadata,
    default_frameworks_root,
    load_framework_metadata,
    sync_frameworks,
    sync_from_default_location,
)

from .conftest import SeedDataDir
from .oscal_fixtures import catalog_doc, encode, mutated, profile_doc, write_framework

FW = "example-fw"


@pytest.fixture
def database(tmp_path: Path, seed_data_dir: SeedDataDir) -> Iterator[Database]:
    db = initialize_database(seed_data_dir(tmp_path / "data"))
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "frameworks"
    directory.mkdir()
    return directory


def _meta(
    root: Path, framework_id: str = FW, database: Database | None = None
) -> FrameworkMetadata:
    """The manifest, registered as a framework row in `database` when given
    (startup always registers before importing)."""
    meta = next(m for m in load_framework_metadata(root) if m.id == framework_id)
    if database is not None:
        sync_frameworks(database, [meta])
    return meta


def _count(db: Database, model: Any, **where: Any) -> int:
    with db.session() as s:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return int(s.scalar(query) or 0)


def _releases(db: Database, framework_id: str = FW) -> list[tuple[str, bool]]:
    with db.session() as s:
        rows = s.execute(
            select(CatalogRelease.version, CatalogRelease.is_current)
            .where(CatalogRelease.framework_id == framework_id)
            .order_by(CatalogRelease.version)
        ).all()
        return [(v, bool(c)) for v, c in rows]


def _release_id(db: Database, version: str, framework_id: str = FW) -> str:
    with db.session() as s:
        return s.scalars(
            select(CatalogRelease.id).where(
                CatalogRelease.framework_id == framework_id, CatalogRelease.version == version
            )
        ).one()


# --- synthetic framework ------------------------------------------------------------


def test_imports_a_generic_catalog(database: Database, root: Path) -> None:
    write_framework(root, profiles={"pick": profile_doc(["x_1", "x_1.e1"])})
    sync_from_default_location(database, root)
    assert _releases(database) == [("2.0", True)]
    release_id = _release_id(database, "2.0")

    with database.session() as s:
        release = s.get(CatalogRelease, release_id)
        assert release is not None
        assert (release.title, release.source_format, release.source_format_version) == (
            "Example Catalog",
            "oscal-catalog-json",
            "1.1.2",
        )
        assert (release.source_tag, release.source_commit) == ("v2", "a" * 40)
        assert release.attribution == "Example attribution."
        assert release.source_file_name == "catalog.json"

        [family] = s.scalars(
            select(ControlFamily).where(ControlFamily.catalog_release_id == release_id)
        )
        assert (family.identifier, family.name, family.source_id) == ("G1", "Group One", "grp.one")
        controls = s.scalars(
            select(Control)
            .where(Control.catalog_release_id == release_id)
            .order_by(Control.position)
        ).all()
        assert [(c.identifier, c.family_id) for c in controls] == [
            ("X-1", family.id),
            ("x_2", family.id),
            ("loose", None),  # D-016: attached directly to the framework
        ]
        enhancements = s.scalars(
            select(ControlEnhancement)
            .order_by(ControlEnhancement.position)
            .where(ControlEnhancement.control_id == controls[0].id)
        ).all()
        assert [(e.identifier, e.status) for e in enhancements] == [
            ("X-1/E1", None),
            ("x_1.e2", "withdrawn"),
        ]

        parts = s.scalars(
            select(ControlPart)
            .where(ControlPart.control_id == controls[0].id)
            .order_by(ControlPart.parent_id.is_not(None), ControlPart.position)
        ).all()
        top = [p for p in parts if p.parent_id is None]
        assert [(p.name, p.source_id, p.prose) for p in top] == [
            ("requirement", "x_1_req", "  Keep   the text  exactly. "),
            ("statement", None, "Statement without id."),
        ]
        children = [p for p in parts if p.parent_id == top[0].id]
        assert [(p.label, p.prose) for p in children] == [
            ("(a)", "Every {{ insert: param, x_1_p1 }}."),
            ("(b)", "Choose {{ insert: param, x_1_p2 }}."),
        ]
        [overview] = s.scalars(select(ControlPart).where(ControlPart.family_id == family.id))
        assert (overview.name, overview.prose) == ("overview", "Intro.")

        params = s.scalars(
            select(ControlParameter)
            .where(ControlParameter.control_id == controls[0].id)
            .order_by(ControlParameter.position)
        ).all()
        assert [(p.source_id, p.guidelines, p.select_how_many, p.choices) for p in params] == [
            ("x_1_p1", ["A period is set;"], None, None),
            ("x_1_p2", None, "one-or-more", ["alpha", "beta {{ insert: param, x_1_p1 }}"]),
        ]
        links = s.scalars(
            select(ControlLink)
            .where(ControlLink.control_id == controls[0].id)
            .order_by(ControlLink.position)
        ).all()
        assert [(link.rel, link.href) for link in links] == [
            ("related", "#x_2"),
            ("reference", "#00000000-0000-4000-8000-0000000000aa"),
            (None, "https://example.invalid/doc"),
        ]
        [resource] = s.scalars(
            select(CatalogResource).where(CatalogResource.catalog_release_id == release_id)
        )
        assert (resource.title, resource.citation) == ("Reference Doc", "Reference Doc, *2026*.")

        [baseline] = s.scalars(select(Baseline).where(Baseline.catalog_release_id == release_id))
        assert (baseline.identifier, baseline.title) == ("pick", "Example Selection")
        members = s.execute(
            select(BaselineMember.control_id, BaselineMember.enhancement_id).where(
                BaselineMember.baseline_id == baseline.id
            )
        ).all()
        assert set(members) == {(controls[0].id, None), (None, enhancements[0].id)}


def test_resync_verifies_without_writing(database: Database, root: Path) -> None:
    write_framework(root)
    meta = _meta(root)
    sync_from_default_location(database, root)
    before = _count(database, ControlPart)
    assert sync_catalog(database, meta) == "verified"
    assert _count(database, ControlPart) == before
    assert _releases(database) == [("2.0", True)]


def test_tampered_source_is_refused_and_startup_continues(
    database: Database, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = write_framework(root)
    secret_prose = "Keep   the text  exactly."
    (directory / "src" / "catalog.json").write_bytes(
        encode(mutated(catalog_doc(), ["catalog", "metadata", "title"], "Tampered"))
    )
    with caplog.at_level(logging.INFO):
        sync_from_default_location(database, root)
    assert _releases(database) == []  # nothing written
    assert _count(database, Control, framework_id=FW) == 0
    refusals = [r for r in caplog.records if r.getMessage() == "catalog_import_refused"]
    assert [getattr(r, "reason", None) for r in refusals] == ["source_hash_mismatch"]
    assert secret_prose not in caplog.text


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"catalog": {"release_version": "9.9"}}, "release_version_mismatch"),
        ({"catalog": {"sha256": "0" * 64}}, "source_hash_mismatch"),
        ({"catalog": {"path": "src/missing.json"}}, "source_path_invalid"),
    ],
)
def test_manifest_mismatches_are_refused(
    database: Database, root: Path, change: dict[str, Any], reason: str
) -> None:
    write_framework(root, manifest_overrides=change)
    with pytest.raises(CatalogImportError) as caught:
        sync_catalog(database, _meta(root, database=database))
    assert caught.value.reason == reason
    assert _releases(database) == []


@pytest.mark.parametrize(
    "path",
    ["../outside.json", "/abs/catalog.json", "C:/catalog.json", "src\\catalog.json", ".hidden"],
)
def test_manifest_paths_must_stay_inside_the_framework_directory(root: Path, path: str) -> None:
    write_framework(root, manifest_overrides={"catalog": {"path": path}})
    assert load_framework_metadata(root) == []


def test_symlinked_source_outside_the_directory_is_refused(
    database: Database, root: Path, tmp_path: Path
) -> None:
    directory = write_framework(root)
    outside = tmp_path / "outside.json"
    outside.write_bytes((directory / "src" / "catalog.json").read_bytes())
    link = directory / "src" / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this machine")
    meta = _meta(root).model_copy(
        update={"catalog": _meta(root).catalog.model_copy(update={"path": "src/linked.json"})}  # type: ignore[union-attr]
    )
    with pytest.raises(CatalogImportError, match="source_path_invalid"):
        sync_catalog(database, meta)


def test_profile_errors_refuse_the_whole_release(database: Database, root: Path) -> None:
    write_framework(root, profiles={"bad": profile_doc(["x_1", "does-not-exist"])})
    with pytest.raises(CatalogImportError, match="unknown_profile_reference"):
        sync_catalog(database, _meta(root, database=database))
    assert _releases(database) == []
    assert _count(database, Control, framework_id=FW) == 0


def test_manifest_profiles_need_a_catalog_and_unique_ids() -> None:
    profile = {"id": "p", "format": "oscal-profile-json", "path": "p.json", "sha256": "0" * 64}
    with pytest.raises(ValidationError):
        FrameworkMetadata.model_validate(
            {"id": "f", "name": "F", "version": "1", "profiles": [profile]}
        )


# --- versions and pinning (PD-013 / PD-015) ---------------------------------------


def test_same_version_with_different_bytes_is_refused_and_original_kept(
    database: Database, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_framework(root)
    sync_from_default_location(database, root)
    release_id = _release_id(database, "2.0")
    changed = mutated(catalog_doc(), [*["catalog", "groups", 0, "controls", 1], "title"], "Renamed")
    write_framework(root, catalog=changed)
    with caplog.at_level(logging.WARNING):
        sync_from_default_location(database, root)
    assert [getattr(r, "reason", None) for r in caplog.records] == ["release_conflict"]
    assert _release_id(database, "2.0") == release_id
    with database.session() as s:
        titles = s.scalars(select(Control.title).where(Control.catalog_release_id == release_id))
        assert "Renamed" not in set(titles)


def test_new_release_is_imported_beside_the_old_one(database: Database, root: Path) -> None:
    write_framework(root)
    sync_from_default_location(database, root)
    old_release = _release_id(database, "2.0")
    with database.session(write=True) as s:
        s.add(Assessment(id="on-old", framework_id=FW, catalog_release_id=old_release, name="x"))

    write_framework(root, catalog=catalog_doc(version="3.0"), release_version="3.0")
    sync_from_default_location(database, root)
    assert _releases(database) == [("2.0", False), ("3.0", True)]
    new_release = _release_id(database, "3.0")
    with database.session() as s:
        # An existing assessment keeps its release.
        assert s.get(Assessment, "on-old").catalog_release_id == old_release  # type: ignore[union-attr]
    # Both releases keep their full content.
    assert _count(database, Control, catalog_release_id=old_release) == 3
    assert _count(database, Control, catalog_release_id=new_release) == 3


def test_unpinned_assessments_are_pinned_once(database: Database, root: Path) -> None:
    # Registered without a catalog first (as in Sprint 2).
    directory = write_framework(root)
    manifest = json.loads((directory / "framework.json").read_text(encoding="utf-8"))
    without_catalog = {k: v for k, v in manifest.items() if k not in ("catalog", "profiles")}
    (directory / "framework.json").write_text(json.dumps(without_catalog), encoding="utf-8")
    sync_from_default_location(database, root)
    with database.session(write=True) as s:
        s.add(Assessment(id="early", framework_id=FW, name="Created before the catalog"))

    write_framework(root)
    sync_from_default_location(database, root)
    first = _release_id(database, "2.0")
    write_framework(root, catalog=catalog_doc(version="3.0"), release_version="3.0")
    sync_from_default_location(database, root)
    with database.session() as s:
        assert s.get(Assessment, "early").catalog_release_id == first  # type: ignore[union-attr]


def test_switching_back_to_an_imported_release_makes_it_current(
    database: Database, root: Path
) -> None:
    write_framework(root)
    sync_from_default_location(database, root)
    write_framework(root, catalog=catalog_doc(version="3.0"), release_version="3.0")
    sync_from_default_location(database, root)
    write_framework(root)
    sync_from_default_location(database, root)
    assert _releases(database) == [("2.0", True), ("3.0", False)]


def test_import_into_an_empty_directory_runs_at_startup(tmp_path: Path) -> None:
    """A fresh data directory (no template) gets the shipped catalog at
    startup, and a restart only verifies it."""
    db = initialize_database(tmp_path)
    try:
        with db.session() as s:
            assert s.scalar(select(func.count()).select_from(CatalogRelease)) == 1
            sha = s.scalar(select(CatalogRelease.source_sha256))
    finally:
        db.dispose()
    again = initialize_database(tmp_path)
    try:
        with again.session() as s:
            assert s.scalar(select(func.count()).select_from(CatalogRelease)) == 1
            assert s.scalar(select(CatalogRelease.source_sha256)) == sha
    finally:
        again.dispose()


# --- the shipped catalog ------------------------------------------------------------


def _shipped() -> tuple[FrameworkMetadata, dict[str, Any]]:
    root = default_frameworks_root()
    assert root is not None
    [meta] = [m for m in load_framework_metadata(root) if m.catalog is not None]
    assert meta.directory is not None and meta.catalog is not None
    source = json.loads((meta.directory / meta.catalog.path).read_bytes())
    return meta, source


def _label(item: dict[str, Any]) -> str | None:
    for prop in item.get("props", []):
        if prop["name"] == "label" and "class" not in prop and "ns" not in prop:
            return str(prop["value"])
    return None


def _source_parts(parts: list[dict[str, Any]]) -> list[Any]:
    return [
        (
            p["name"],
            p.get("id"),
            _label(p),
            p.get("title"),
            p.get("prose"),
            _source_parts(p.get("parts", [])),
        )
        for p in parts
    ]


def _stored_parts(rows: list[ControlPart], parent: str | None = None) -> list[Any]:
    return [
        (r.name, r.source_id, r.label, r.title, r.prose, _stored_parts(rows, r.id))
        for r in sorted((r for r in rows if r.parent_id == parent), key=lambda r: r.position)
    ]


def test_shipped_catalog_is_stored_completely_and_verbatim(database: Database) -> None:
    """Every control and enhancement in the shipped source -- titles,
    statuses, the full part tree (names, labels, titles, prose), every
    parameter and every link -- matches the database exactly."""
    meta, source = _shipped()
    release_id = _release_id(database, meta.catalog.release_version, meta.id)  # type: ignore[union-attr]
    with database.session() as s:
        parts: dict[str, list[ControlPart]] = {}
        for row in s.scalars(
            select(ControlPart).where(ControlPart.catalog_release_id == release_id)
        ):
            owner = row.control_id or row.enhancement_id or row.family_id
            assert owner is not None
            parts.setdefault(owner, []).append(row)
        params: dict[str, list[ControlParameter]] = {}
        for p in s.scalars(
            select(ControlParameter).where(ControlParameter.catalog_release_id == release_id)
        ):
            params.setdefault(p.control_id or p.enhancement_id or "", []).append(p)
        links: dict[str, list[ControlLink]] = {}
        for link in s.scalars(
            select(ControlLink).where(ControlLink.catalog_release_id == release_id)
        ):
            links.setdefault(link.control_id or link.enhancement_id or "", []).append(link)
        controls = {
            c.source_id: c
            for c in s.scalars(select(Control).where(Control.catalog_release_id == release_id))
        }
        enhancements = {
            e.source_id: e
            for e in s.scalars(
                select(ControlEnhancement).where(
                    ControlEnhancement.catalog_release_id == release_id
                )
            )
        }
        families = {
            f.source_id: f
            for f in s.scalars(
                select(ControlFamily).where(ControlFamily.catalog_release_id == release_id)
            )
        }

    def check(item: dict[str, Any], row: Control | ControlEnhancement) -> None:
        assert row.title == item["title"]
        assert row.identifier == (_label(item) or item["id"])
        statuses = [p["value"] for p in item.get("props", []) if p["name"] == "status"]
        assert row.status == (statuses[0] if statuses else None)
        assert _stored_parts(parts.get(row.id, [])) == _source_parts(item.get("parts", []))
        stored_params = sorted(params.get(row.id, []), key=lambda p: p.position)
        assert [
            (p.source_id, p.label, p.guidelines, p.select_how_many, p.choices)
            for p in stored_params
        ] == [
            (
                p["id"],
                p.get("label"),
                [g["prose"] for g in p.get("guidelines", [])] or None,
                p.get("select", {}).get("how-many"),
                p["select"].get("choice") if "select" in p else None,
            )
            for p in item.get("params", [])
        ]
        stored_links = sorted(links.get(row.id, []), key=lambda link: link.position)
        assert [(link.rel, link.href) for link in stored_links] == [
            (link.get("rel"), link["href"]) for link in item.get("links", [])
        ]

    seen = 0
    for group in source["catalog"]["groups"]:
        family = families[group["id"]]
        assert family.name == group["title"]
        assert _stored_parts(parts.get(family.id, [])) == _source_parts(group.get("parts", []))
        for control in group["controls"]:
            check(control, controls[control["id"]])
            seen += 1
            for enhancement in control.get("controls", []):
                check(enhancement, enhancements[enhancement["id"]])
                assert enhancements[enhancement["id"]].control_id == controls[control["id"]].id
                seen += 1
    assert seen == len(controls) + len(enhancements) == 1196


def test_shipped_release_records_its_provenance(database: Database) -> None:
    meta, _ = _shipped()
    catalog = meta.catalog
    assert catalog is not None
    with database.session() as s:
        release = s.scalars(
            select(CatalogRelease).where(CatalogRelease.framework_id == meta.id)
        ).one()
        assert release.source_sha256 == catalog.sha256
        assert release.source_tag == catalog.source.tag
        assert release.source_commit == catalog.source.commit
        assert release.source_release_url == catalog.source.release_url
        assert release.license_notice == catalog.license_notice
        assert release.attribution == catalog.attribution
        assert release.is_current
        baselines = s.execute(
            select(Baseline.identifier, func.count(BaselineMember.id))
            .join(BaselineMember, BaselineMember.baseline_id == Baseline.id)
            .where(Baseline.catalog_release_id == release.id)
            .group_by(Baseline.identifier)
        ).all()
    assert {identifier for identifier, _ in baselines} == {p.id for p in meta.profiles}
    assert all(count > 0 for _, count in baselines)


def test_concurrent_first_imports_create_one_release(tmp_path: Path, root: Path) -> None:
    """Two processes starting at once on a fresh database: the second
    import waits for the first and then only verifies."""
    import concurrent.futures

    write_framework(root)
    db = initialize_database(tmp_path / "data")
    meta = _meta(root, database=db)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            outcomes = sorted(pool.map(lambda _: sync_catalog(db, meta), range(3)))
        assert outcomes == ["imported", "verified", "verified"]
        assert _releases(db) == [("2.0", True)]
        with db.session() as s:
            assert s.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        db.dispose()
