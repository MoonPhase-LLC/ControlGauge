"""S3-04: catalog browsing API over the shipped, imported catalog."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import Database, initialize_database

from .conftest import VALID_TOKEN, SeedDataDir

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}


@pytest.fixture(scope="module")
def module_data(tmp_path_factory: pytest.TempPathFactory, catalog_template: Path) -> Path:
    directory = tmp_path_factory.mktemp("catalog-api")
    (directory / catalog_template.name).write_bytes(catalog_template.read_bytes())
    return directory


@pytest.fixture(scope="module")
def api(module_data: Path) -> Iterator[TestClient]:
    """Read-only tests share one database for speed."""
    database: Database = initialize_database(module_data)
    try:
        yield TestClient(create_app(Settings(session_token=VALID_TOKEN), database=database))
    finally:
        database.dispose()


def _get(api: TestClient, path: str, **params: Any) -> Any:
    response = api.get(path, headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def release(api: TestClient) -> dict[str, Any]:
    framework = _get(api, "/frameworks")["frameworks"][0]
    return _get(api, f"/catalog-releases/{framework['current_release']['id']}")  # type: ignore[no-any-return]


def _control_id(api: TestClient, release: dict[str, Any], identifier: str) -> str:
    controls = _get(api, f"/catalog-releases/{release['id']}/controls", q=identifier, limit=500)
    [match] = [c for c in controls["controls"] if c["identifier"] == identifier]
    return str(match["id"])


def test_every_catalog_route_requires_the_session_token(
    api: TestClient, release: dict[str, Any]
) -> None:
    control = _control_id(api, release, "AC-2")
    for path in (
        f"/catalog-releases/{release['id']}",
        f"/catalog-releases/{release['id']}/controls",
        f"/controls/{control}",
    ):
        for headers in ({}, {"Authorization": "Bearer wrong-token-wrong-token-wrong"}):
            response = api.get(path, headers=headers)
            assert response.status_code == 401
            assert response.json() == {"detail": "Unauthorized"}


def test_release_reports_provenance_families_and_baselines(release: dict[str, Any]) -> None:
    assert release["version"] == "5.2.0"
    assert release["is_current"] is True
    assert release["framework"]["id"] == "nist-800-53-rev5"
    source = release["source"]
    assert source["format"] == "oscal-catalog-json"
    assert source["tag"] == "v1.5.0"
    assert len(source["sha256"]) == 64
    assert source["release_url"].startswith("https://github.com/usnistgov/oscal-content/")
    assert "public domain" in release["license_notice"]
    assert "does not endorse" in release["attribution"]
    assert (release["control_count"], release["enhancement_count"]) == (324, 872)
    assert release["ungrouped_control_count"] == 0
    families = release["families"]
    assert len(families) == 20
    assert families[0]["identifier"] == "AC"
    assert sum(f["control_count"] for f in families) == 324
    overview = [f for f in families if f["parts"]]
    assert [f["identifier"] for f in overview] == ["PM"]
    assert overview[0]["parts"][0]["name"] == "overview"
    assert [(b["identifier"], b["member_count"]) for b in release["baselines"]] == [
        ("low", 149),
        ("moderate", 287),
        ("high", 370),
        ("privacy", 96),
    ]


def test_control_list_paging_and_order(api: TestClient, release: dict[str, Any]) -> None:
    path = f"/catalog-releases/{release['id']}/controls"
    first = _get(api, path, limit=2)
    assert first["total"] == 324
    assert [c["identifier"] for c in first["controls"]] == ["AC-1", "AC-2"]
    second = _get(api, path, limit=2, offset=2)
    assert [c["identifier"] for c in second["controls"]] == ["AC-3", "AC-4"]
    ac2 = first["controls"][1]
    assert ac2["enhancement_count"] == 13
    assert ac2["baselines"] == ["low", "moderate", "high"]


def test_control_list_filters(api: TestClient, release: dict[str, Any]) -> None:
    path = f"/catalog-releases/{release['id']}/controls"
    family = next(f for f in release["families"] if f["identifier"] == "AT")
    at = _get(api, path, family_id=family["id"], limit=500)
    assert at["total"] == family["control_count"]
    assert all(c["family_id"] == family["id"] for c in at["controls"])

    low = next(b for b in release["baselines"] if b["identifier"] == "low")
    in_low = _get(api, path, baseline_id=low["id"], limit=500)
    assert 0 < in_low["total"] < 324
    assert all("low" in c["baselines"] for c in in_low["controls"])


def test_search_matches_identifiers_titles_and_enhancements(
    api: TestClient, release: dict[str, Any]
) -> None:
    path = f"/catalog-releases/{release['id']}/controls"
    by_title = _get(api, path, q="account management")
    assert "AC-2" in [c["identifier"] for c in by_title["controls"]]
    # Matching an enhancement returns its parent control.
    by_enhancement = _get(api, path, q="AC-2(1)")
    assert [c["identifier"] for c in by_enhancement["controls"]] == ["AC-2"]
    # LIKE wildcards are literal characters, not patterns.
    assert _get(api, path, q="%")["total"] == 0
    assert _get(api, path, q="_")["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x" * 101},
        {"q": "bad\x00char"},
        {"family_id": "not-a-uuid"},
        {"family_id": str(uuid.uuid4())},
        {"baseline_id": str(uuid.uuid4())},
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
    ],
)
def test_control_list_rejects_bad_parameters_without_echo(
    api: TestClient, release: dict[str, Any], params: dict[str, Any]
) -> None:
    response = api.get(f"/catalog-releases/{release['id']}/controls", headers=AUTH, params=params)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    for value in params.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in json.dumps(body)


@pytest.mark.parametrize(
    "path",
    [
        "/catalog-releases/{uuid}",
        "/catalog-releases/{uuid}/controls",
        "/catalog-releases/not-a-uuid",
        "/controls/{uuid}",
        "/controls/NOT-A-UUID",
    ],
)
def test_unknown_ids_are_404(api: TestClient, path: str) -> None:
    response = api.get(path.format(uuid=uuid.uuid4()), headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] in {"catalog_release_not_found", "control_not_found"}


def test_uppercase_uuid_is_not_an_alias(api: TestClient, release: dict[str, Any]) -> None:
    response = api.get(f"/catalog-releases/{release['id'].upper()}", headers=AUTH)
    assert response.status_code == 404


def test_control_detail(api: TestClient, release: dict[str, Any]) -> None:
    detail = _get(api, f"/controls/{_control_id(api, release, 'AC-2')}")
    assert (detail["identifier"], detail["title"], detail["status"]) == (
        "AC-2",
        "Account Management",
        None,
    )
    assert detail["family"]["identifier"] == "AC"
    assert detail["release"]["version"] == "5.2.0"
    assert "does not endorse" in detail["release"]["attribution"]
    assert detail["baselines"] == ["low", "moderate", "high"]

    statement = next(p for p in detail["parts"] if p["name"] == "statement")
    items = statement["parts"]
    assert [i["label"] for i in items][:3] == ["a.", "b.", "c."]
    assert (
        items[2]["prose"]
        == "Require {{ insert: param, ac-02_odp.01 }} for group and role membership;"
    )
    assert [p["label"] for p in items[3]["parts"]] == ["1.", "2.", "3."]
    assert any(p["name"] == "guidance" for p in detail["parts"])

    params = {p["source_id"]: p for p in detail["params"]}
    assert params["ac-02_odp.01"]["label"] == "prerequisites and criteria"
    assert params["ac-02_odp.01"]["guidelines"]

    related = [link for link in detail["links"] if link["rel"] == "related"]
    assert related[0]["target"]["kind"] == "control"
    assert related[0]["target"]["identifier"] == "AC-3"
    references = [link for link in detail["links"] if link["rel"] == "reference"]
    assert references and all(r["target"]["kind"] == "resource" for r in references)
    assert all(r["target"]["title"] for r in references)

    enhancements = detail["enhancements"]
    assert len(enhancements) == 13
    assert enhancements[0]["identifier"] == "AC-2(1)"
    assert enhancements[0]["baselines"] == ["moderate", "high"]
    assert enhancements[0]["parts"][0]["name"] == "statement"


def test_withdrawn_control_links_to_where_it_went(api: TestClient, release: dict[str, Any]) -> None:
    detail = _get(api, f"/controls/{_control_id(api, release, 'AC-13')}")
    assert detail["status"] == "withdrawn"
    assert detail["parts"] == []
    targets = [(link["rel"], link["target"]["identifier"]) for link in detail["links"]]
    assert targets == [("incorporated-into", "AC-2"), ("incorporated-into", "AU-6")]


def _all_link_targets(api: TestClient, release: dict[str, Any]) -> list[dict[str, Any]]:
    controls = _get(api, f"/catalog-releases/{release['id']}/controls", limit=500)["controls"]
    targets: list[dict[str, Any]] = []
    for summary in controls:
        detail = _get(api, f"/controls/{summary['id']}")
        for item in [detail, *detail["enhancements"]]:
            targets.extend({**link["target"], "href": link["href"]} for link in item["links"])
    return targets


def test_every_shipped_link_resolves(api: TestClient, release: dict[str, Any]) -> None:
    """Links may name controls, enhancements, parts, families or reference
    documents; in the shipped catalog every one resolves."""
    targets = _all_link_targets(api, release)
    assert [t["href"] for t in targets if t["kind"] == "unresolved"] == []
    assert {t["kind"] for t in targets} == {"control", "enhancement", "part", "family", "resource"}

    [part] = [t for t in targets if t["kind"] == "part"]
    assert (part["identifier"], part["part_label"]) == ("AC-2", "k.")
    assert part["control_id"] == _control_id(api, release, "AC-2")
    [family] = [t for t in targets if t["kind"] == "family"]
    assert family["identifier"] == "SR"
    for enhancement_target in (t for t in targets if t["kind"] == "enhancement"):
        assert enhancement_target["control_id"]


def test_assessment_detail_reports_the_pinned_release(
    tmp_path: Path, seed_data_dir: SeedDataDir
) -> None:
    database = initialize_database(seed_data_dir(tmp_path))
    try:
        client = TestClient(create_app(Settings(session_token=VALID_TOKEN), database=database))
        framework = _get(client, "/frameworks")["frameworks"][0]
        created = client.post(
            "/assessments", headers=AUTH, json={"name": "A", "framework_id": framework["id"]}
        ).json()
        assert created["catalog_release"]["id"] == framework["current_release"]["id"]
        assert created["catalog_release"]["version"] == "5.2.0"
    finally:
        database.dispose()
