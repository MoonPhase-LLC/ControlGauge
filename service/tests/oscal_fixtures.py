"""Small synthetic OSCAL documents for importer tests.

Deliberately *not* shaped like any real framework: identifiers such as
`grp.one` / `x_1`, a control attached directly to the catalog (no group),
and unusual part names, so tests prove the importer is framework-agnostic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def catalog_doc(version: str = "2.0") -> dict[str, Any]:
    return {
        "catalog": {
            "uuid": "00000000-0000-4000-8000-000000000001",
            "metadata": {
                "title": "Example Catalog",
                "last-modified": "2026-01-01T00:00:00Z",
                "version": version,
                "oscal-version": "1.1.2",
            },
            "groups": [
                {
                    "id": "grp.one",
                    "class": "section",
                    "title": "Group One",
                    "props": [{"name": "label", "value": "G1"}],
                    "parts": [{"id": "grp.one_intro", "name": "overview", "prose": "Intro."}],
                    "controls": [
                        {
                            "id": "x_1",
                            "title": "First Requirement",
                            "props": [
                                {"name": "label", "value": "X 1", "class": "padded"},
                                {"name": "label", "value": "X-1"},
                            ],
                            "params": [
                                {
                                    "id": "x_1_p1",
                                    "label": "period",
                                    "guidelines": [{"prose": "A period is set;"}],
                                },
                                {
                                    "id": "x_1_p2",
                                    "select": {
                                        "how-many": "one-or-more",
                                        "choice": ["alpha", "beta {{ insert: param, x_1_p1 }}"],
                                    },
                                },
                            ],
                            "parts": [
                                {
                                    "id": "x_1_req",
                                    "name": "requirement",
                                    "prose": "  Keep   the text  exactly. ",
                                    "parts": [
                                        {
                                            "id": "x_1_req.a",
                                            "name": "item",
                                            "props": [{"name": "label", "value": "(a)"}],
                                            "prose": "Every {{ insert: param, x_1_p1 }}.",
                                        },
                                        {
                                            "id": "x_1_req.b",
                                            "name": "item",
                                            "props": [{"name": "label", "value": "(b)"}],
                                            "prose": "Choose {{ insert: param, x_1_p2 }}.",
                                        },
                                    ],
                                },
                                {"name": "statement", "prose": "Statement without id."},
                            ],
                            "links": [
                                {"href": "#x_2", "rel": "related"},
                                {
                                    "href": "#00000000-0000-4000-8000-0000000000aa",
                                    "rel": "reference",
                                },
                                {"href": "https://example.invalid/doc"},
                            ],
                            "controls": [
                                {
                                    "id": "x_1.e1",
                                    "title": "First Extension",
                                    "props": [{"name": "label", "value": "X-1/E1"}],
                                    "parts": [{"name": "requirement", "prose": "Extension text."}],
                                },
                                {
                                    "id": "x_1.e2",
                                    "title": "Retired Extension",
                                    "props": [{"name": "status", "value": "withdrawn"}],
                                    "links": [{"href": "#x_1", "rel": "incorporated-into"}],
                                },
                            ],
                        },
                        {
                            "id": "x_2",
                            "title": "Second Requirement",
                            "parts": [{"name": "requirement", "prose": "Second."}],
                        },
                    ],
                }
            ],
            "controls": [
                {
                    "id": "loose",
                    "title": "Ungrouped Requirement",
                    "parts": [{"name": "requirement", "prose": "No group."}],
                }
            ],
            "back-matter": {
                "resources": [
                    {
                        "uuid": "00000000-0000-4000-8000-0000000000aa",
                        "title": "Reference Doc",
                        "citation": {"text": "Reference Doc, *2026*."},
                        "rlinks": [{"href": "https://example.invalid/ref"}],
                    }
                ]
            },
        }
    }


def profile_doc(with_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "profile": {
            "uuid": "00000000-0000-4000-8000-000000000002",
            "metadata": {"title": "Example Selection", "version": "2.0"},
            "imports": [
                {
                    "href": "#00000000-0000-4000-8000-0000000000ff",
                    "include-controls": [{"with-ids": with_ids or ["x_1", "x_1.e1"]}],
                }
            ],
            "merge": {"as-is": True},
        }
    }


def encode(doc: Any) -> bytes:
    return json.dumps(doc).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_framework(
    root: Path,
    *,
    framework_id: str = "example-fw",
    catalog: dict[str, Any] | None = None,
    release_version: str = "2.0",
    profiles: dict[str, dict[str, Any]] | None = None,
    catalog_bytes: bytes | None = None,
    manifest_overrides: dict[str, Any] | None = None,
) -> Path:
    """Writes `<root>/<framework_id>/` with a manifest pinning the hashes of
    the files it writes. Returns the framework directory."""
    directory = root / framework_id
    (directory / "src").mkdir(parents=True, exist_ok=True)
    data = catalog_bytes if catalog_bytes is not None else encode(catalog or catalog_doc())
    (directory / "src" / "catalog.json").write_bytes(data)
    profile_entries = []
    for profile_id, doc in (profiles if profiles is not None else {}).items():
        profile_data = encode(doc)
        (directory / "src" / f"{profile_id}.json").write_bytes(profile_data)
        profile_entries.append(
            {
                "id": profile_id,
                "format": "oscal-profile-json",
                "path": f"src/{profile_id}.json",
                "sha256": sha256(profile_data),
            }
        )
    manifest: dict[str, Any] = {
        "id": framework_id,
        "name": f"Example {framework_id}",
        "version": "Edition 2",
        "catalog": {
            "format": "oscal-catalog-json",
            "path": "src/catalog.json",
            "sha256": sha256(data),
            "release_version": release_version,
            "source": {"tag": "v2", "commit": "a" * 40},
            "license_notice": "Example license notice.",
            "attribution": "Example attribution.",
        },
        "profiles": profile_entries,
    }
    for key, value in (manifest_overrides or {}).items():
        if isinstance(value, dict) and isinstance(manifest.get(key), dict):
            manifest[key] = {**manifest[key], **value}
        else:
            manifest[key] = value
    (directory / "framework.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def mutated(doc: dict[str, Any], path: list[Any], value: Any) -> dict[str, Any]:
    """A deep copy of `doc` with the value at `path` replaced."""
    result = copy.deepcopy(doc)
    target: Any = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return result
