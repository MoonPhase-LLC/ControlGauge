"""OSCAL catalog and profile reader (S3-03).

Turns an OSCAL JSON catalog or profile into plain, validated data for the
importer. Framework-agnostic (`docs/ARCHITECTURE.md` §8): it understands
OSCAL's generic structure (groups, controls, nested controls, parts,
params, props, links, back-matter) and nothing about any one framework.
Display identifiers come from a control's unclassed `label` property when
present and otherwise from its OSCAL `id`; they are never parsed.

Source files are untrusted input (`CLAUDE.md`). The caller verifies the
pinned SHA-256 before parsing (`read_verified`); this module then enforces:

- size, count, depth and per-field length limits;
- identifier syntax (OSCAL tokens) and uniqueness;
- no control characters other than tab/newline/carriage return;
- every parameter-insertion marker refers to a parameter that exists;
- shapes it cannot represent faithfully (deeper nesting than one
  enhancement level, nested groups, profile modifications) are refused
  rather than silently simplified.

All text is kept verbatim: nothing is trimmed, reflowed or rewritten.
Errors are `OscalError` with a fixed, content-free reason code.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db.models import IDENTIFIER_MAX, SOURCE_ID_MAX, STATUS_MAX, TITLE_MAX, URL_MAX

MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_PROFILE_BYTES = 2 * 1024 * 1024
MAX_CONTROLS = 20_000  # controls + enhancements
MAX_PARTS = 250_000
MAX_PART_DEPTH = 12
MAX_PROSE = 50_000
MAX_LIST = 10_000  # any single list (params of a control, choices, ...)
MAX_RESOURCES = 10_000

#: OSCAL `token`: starts with a letter or underscore; letters, digits,
#: `_`, `.`, `-` after that.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
_UUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
_DISALLOWED_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: OSCAL markup parameter insertion: `{{ insert: param, <id> }}`.
INSERT_PARAM = re.compile(r"\{\{\s*insert:\s*param,\s*([^\s}]+)\s*\}\}")


class OscalError(ValueError):
    """Invalid or unsupported source. `reason` is a fixed code, safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- normalized output ----------------------------------------------------------


@dataclass(frozen=True)
class Part:
    name: str
    source_id: str | None
    label: str | None
    title: str | None
    prose: str | None
    parts: tuple[Part, ...]


@dataclass(frozen=True)
class Param:
    source_id: str
    label: str | None
    guidelines: tuple[str, ...]
    select_how_many: str | None
    choices: tuple[str, ...] | None


@dataclass(frozen=True)
class Link:
    rel: str | None
    href: str


@dataclass(frozen=True)
class Control:
    source_id: str
    identifier: str
    title: str
    status: str | None
    params: tuple[Param, ...]
    parts: tuple[Part, ...]
    links: tuple[Link, ...]
    #: Nested controls (enhancements). Always empty on an enhancement.
    enhancements: tuple[Control, ...] = ()


@dataclass(frozen=True)
class Group:
    source_id: str
    identifier: str
    title: str
    parts: tuple[Part, ...]
    controls: tuple[Control, ...]


@dataclass(frozen=True)
class Resource:
    source_id: str
    title: str | None
    citation: str | None
    href: str | None


@dataclass(frozen=True)
class Catalog:
    title: str
    version: str
    oscal_version: str | None
    last_modified: str | None
    groups: tuple[Group, ...]
    #: Controls attached directly to the catalog, outside any group (D-016).
    ungrouped: tuple[Control, ...]
    resources: tuple[Resource, ...]

    def all_controls(self) -> Iterator[Control]:
        for group in self.groups:
            yield from group.controls
        yield from self.ungrouped


@dataclass(frozen=True)
class Profile:
    title: str
    version: str | None
    #: Source ids of the selected controls/enhancements, or None for "all".
    with_ids: tuple[str, ...] | None


# --- file access -------------------------------------------------------------------


def read_verified(path: Path, expected_sha256: str, max_bytes: int) -> bytes:
    """Reads a pinned source file and checks its SHA-256 before anything
    parses it. Size is checked before reading the whole file."""
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise OscalError("source_too_large")
        data = path.read_bytes()
    except OSError:
        raise OscalError("source_unreadable") from None
    if len(data) > max_bytes:
        raise OscalError("source_too_large")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise OscalError("source_hash_mismatch")
    return data


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OscalError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_name: str) -> None:
    raise OscalError("invalid_json")


def _load_json(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except OscalError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise OscalError("invalid_json") from None


# --- field helpers -----------------------------------------------------------------


def _obj(value: Any, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OscalError(reason)
    return value


def _list(value: Any, reason: str, limit: int = MAX_LIST) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise OscalError(reason)
    return value


def _text(value: Any, reason: str, max_len: int, *, required: bool = True) -> str | None:
    """A verbatim string: type, length and character checks only."""
    if value is None:
        if required:
            raise OscalError(reason)
        return None
    if not isinstance(value, str) or len(value) > max_len or _DISALLOWED_CHARS.search(value):
        raise OscalError(reason)
    if required and not value.strip():
        raise OscalError(reason)
    return value


def _req(value: Any, reason: str, max_len: int) -> str:
    text = _text(value, reason, max_len)
    if text is None:  # unreachable: required=True raises first
        raise OscalError(reason)
    return text


def _token(value: Any, reason: str) -> str:
    if not isinstance(value, str) or len(value) > SOURCE_ID_MAX or not _TOKEN.fullmatch(value):
        raise OscalError(reason)
    return value


def _props(item: dict[str, Any]) -> list[dict[str, Any]]:
    props = _list(item.get("props"), "invalid_props")
    for prop in props:
        _obj(prop, "invalid_props")
        _token(prop.get("name"), "invalid_props")
        _text(prop.get("value"), "invalid_props", TITLE_MAX)
        if prop.get("class") is not None:
            _token(prop.get("class"), "invalid_props")
    return props


def _prop_value(props: list[dict[str, Any]], name: str) -> str | None:
    """The first value of property `name` that carries no `class` (OSCAL's
    default form of a property) -- or None."""
    for prop in props:
        if prop["name"] == name and prop.get("class") is None and prop.get("ns") is None:
            return str(prop["value"])
    return None


# --- catalog -----------------------------------------------------------------------


class _Reader:
    def __init__(self) -> None:
        self.control_count = 0
        self.part_count = 0
        self.ids: set[str] = set()
        self.identifiers: set[str] = set()
        self.param_ids: set[str] = set()
        self.insert_refs: set[str] = set()

    def _claim_id(self, source_id: str) -> None:
        if source_id in self.ids:
            raise OscalError("duplicate_id")
        self.ids.add(source_id)

    def _scan_inserts(self, text: str | None) -> None:
        if text:
            self.insert_refs.update(INSERT_PARAM.findall(text))

    def part(self, raw: Any, depth: int) -> Part:
        if depth > MAX_PART_DEPTH:
            raise OscalError("part_too_deep")
        self.part_count += 1
        if self.part_count > MAX_PARTS:
            raise OscalError("too_many_parts")
        item = _obj(raw, "invalid_part")
        name = _token(item.get("name"), "invalid_part")
        source_id = None
        if item.get("id") is not None:
            source_id = _token(item.get("id"), "invalid_part")
            self._claim_id(source_id)
        props = _props(item)
        label = _text(_prop_value(props, "label"), "invalid_part", IDENTIFIER_MAX, required=False)
        title = _text(item.get("title"), "invalid_part", TITLE_MAX, required=False)
        prose = _text(item.get("prose"), "invalid_part", MAX_PROSE, required=False)
        self._scan_inserts(prose)
        self._scan_inserts(title)
        children = tuple(self.part(p, depth + 1) for p in _list(item.get("parts"), "invalid_part"))
        return Part(name, source_id, label, title, prose, children)

    def param(self, raw: Any) -> Param:
        item = _obj(raw, "invalid_param")
        source_id = _token(item.get("id"), "invalid_param")
        self._claim_id(source_id)
        self.param_ids.add(source_id)
        _props(item)
        label = _text(item.get("label"), "invalid_param", TITLE_MAX, required=False)
        guidelines: list[str] = []
        for g in _list(item.get("guidelines"), "invalid_param"):
            prose = _req(_obj(g, "invalid_param").get("prose"), "invalid_param", MAX_PROSE)
            self._scan_inserts(prose)
            guidelines.append(prose)
        how_many = None
        choices: tuple[str, ...] | None = None
        if item.get("select") is not None:
            select = _obj(item.get("select"), "invalid_param")
            if select.get("how-many") is not None:
                how_many = _token(select.get("how-many"), "invalid_param")
                if len(how_many) > STATUS_MAX:
                    raise OscalError("invalid_param")
            values: list[str] = []
            for choice in _list(select.get("choice"), "invalid_param"):
                text = _req(choice, "invalid_param", MAX_PROSE)
                self._scan_inserts(text)
                values.append(text)
            choices = tuple(values)
        return Param(source_id, label, tuple(guidelines), how_many, choices)

    def link(self, raw: Any) -> Link:
        item = _obj(raw, "invalid_link")
        href = _req(item.get("href"), "invalid_link", URL_MAX)
        rel = None
        if item.get("rel") is not None:
            rel = _token(item.get("rel"), "invalid_link")
            if len(rel) > STATUS_MAX:
                raise OscalError("invalid_link")
        return Link(rel, href)

    def control(self, raw: Any, *, nested: bool) -> Control:
        self.control_count += 1
        if self.control_count > MAX_CONTROLS:
            raise OscalError("too_many_controls")
        item = _obj(raw, "invalid_control")
        source_id = _token(item.get("id"), "invalid_control")
        self._claim_id(source_id)
        title = _req(item.get("title"), "invalid_control", TITLE_MAX)
        props = _props(item)
        identifier = _prop_value(props, "label") or source_id
        if len(identifier) > IDENTIFIER_MAX or not identifier.strip():
            raise OscalError("invalid_control")
        if identifier in self.identifiers:
            raise OscalError("duplicate_identifier")
        self.identifiers.add(identifier)
        status = _prop_value(props, "status")
        if status is not None and len(status) > STATUS_MAX:
            raise OscalError("invalid_control")

        params = tuple(self.param(p) for p in _list(item.get("params"), "invalid_control"))
        parts = tuple(self.part(p, 0) for p in _list(item.get("parts"), "invalid_control"))
        links = tuple(self.link(link) for link in _list(item.get("links"), "invalid_control"))
        children = _list(item.get("controls"), "invalid_control")
        if nested and children:
            # One enhancement level only (D-016, OPEN_QUESTIONS A-5).
            raise OscalError("unsupported_nesting")
        enhancements = tuple(self.control(c, nested=True) for c in children)
        return Control(source_id, identifier, title, status, params, parts, links, enhancements)

    def group(self, raw: Any) -> Group:
        item = _obj(raw, "invalid_group")
        source_id = _token(item.get("id"), "invalid_group")
        self._claim_id(source_id)
        title = _req(item.get("title"), "invalid_group", TITLE_MAX)
        identifier = _prop_value(_props(item), "label") or source_id
        if len(identifier) > IDENTIFIER_MAX:
            raise OscalError("invalid_group")
        if item.get("groups"):
            # A single optional grouping level (D-016).
            raise OscalError("unsupported_nesting")
        parts = tuple(self.part(p, 0) for p in _list(item.get("parts"), "invalid_group"))
        controls = tuple(
            self.control(c, nested=False) for c in _list(item.get("controls"), "invalid_group")
        )
        return Group(source_id, identifier, title, parts, controls)

    def resource(self, raw: Any) -> Resource:
        item = _obj(raw, "invalid_resource")
        uuid = item.get("uuid")
        if not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
            raise OscalError("invalid_resource")
        if uuid in self.ids:
            raise OscalError("duplicate_id")
        self.ids.add(uuid)
        title = _text(item.get("title"), "invalid_resource", TITLE_MAX, required=False)
        citation = None
        if item.get("citation") is not None:
            citation = _text(
                _obj(item.get("citation"), "invalid_resource").get("text"),
                "invalid_resource",
                MAX_PROSE,
                required=False,
            )
        href = None
        rlinks = _list(item.get("rlinks"), "invalid_resource")
        if rlinks:
            href = _text(
                _obj(rlinks[0], "invalid_resource").get("href"),
                "invalid_resource",
                URL_MAX,
                required=False,
            )
        return Resource(uuid, title, citation, href)


def parse_catalog(data: bytes) -> Catalog:
    root = _obj(_load_json(data), "not_a_catalog")
    catalog = _obj(root.get("catalog"), "not_a_catalog")
    metadata = _obj(catalog.get("metadata"), "invalid_metadata")
    title = _req(metadata.get("title"), "invalid_metadata", TITLE_MAX)
    version = _req(metadata.get("version"), "invalid_metadata", 100)
    oscal_version = _text(metadata.get("oscal-version"), "invalid_metadata", 50, required=False)
    last_modified = _text(metadata.get("last-modified"), "invalid_metadata", 64, required=False)

    reader = _Reader()
    groups = tuple(reader.group(g) for g in _list(catalog.get("groups"), "invalid_group"))
    ungrouped = tuple(
        reader.control(c, nested=False) for c in _list(catalog.get("controls"), "invalid_control")
    )
    if reader.control_count == 0:
        raise OscalError("empty_catalog")
    back_matter = catalog.get("back-matter")
    resources: tuple[Resource, ...] = ()
    if back_matter is not None:
        resources = tuple(
            reader.resource(r)
            for r in _list(
                _obj(back_matter, "invalid_resource").get("resources"),
                "invalid_resource",
                MAX_RESOURCES,
            )
        )
    if not reader.insert_refs <= reader.param_ids:
        raise OscalError("unknown_parameter_reference")
    return Catalog(title, version, oscal_version, last_modified, groups, ungrouped, resources)


# --- profile -----------------------------------------------------------------------

_PROFILE_SUPPORTED_KEYS = {"uuid", "metadata", "imports", "merge", "back-matter"}


def parse_profile(data: bytes) -> Profile:
    """A profile that selects controls from one catalog, unmodified. Profiles
    that alter controls (`modify`), exclude, match by pattern or import
    more than one source are refused: storing their selection without
    applying the rest would misrepresent them."""
    root = _obj(_load_json(data), "not_a_profile")
    profile = _obj(root.get("profile"), "not_a_profile")
    if set(profile) - _PROFILE_SUPPORTED_KEYS:
        raise OscalError("unsupported_profile")
    merge = profile.get("merge")
    if merge is not None and merge != {"as-is": True}:
        raise OscalError("unsupported_profile")
    metadata = _obj(profile.get("metadata"), "invalid_metadata")
    title = _req(metadata.get("title"), "invalid_metadata", TITLE_MAX)
    version = _text(metadata.get("version"), "invalid_metadata", 100, required=False)
    imports = _list(profile.get("imports"), "unsupported_profile")
    if len(imports) != 1:
        raise OscalError("unsupported_profile")
    imp = _obj(imports[0], "unsupported_profile")
    if set(imp) - {"href", "include-controls", "include-all"}:
        raise OscalError("unsupported_profile")
    if "include-all" in imp:
        if "include-controls" in imp or imp["include-all"] != {}:
            raise OscalError("unsupported_profile")
        return Profile(title, version, None)
    selections = _list(imp.get("include-controls"), "unsupported_profile")
    if not selections:
        raise OscalError("unsupported_profile")
    ids: list[str] = []
    for selection in selections:
        sel = _obj(selection, "unsupported_profile")
        if set(sel) != {"with-ids"}:
            raise OscalError("unsupported_profile")
        ids.extend(
            _token(i, "invalid_profile_id")
            for i in _list(sel["with-ids"], "unsupported_profile", MAX_CONTROLS)
        )
    if len(set(ids)) != len(ids):
        raise OscalError("duplicate_profile_id")
    return Profile(title, version, tuple(ids))


__all__ = [
    "Catalog",
    "Control",
    "Group",
    "INSERT_PARAM",
    "Link",
    "OscalError",
    "Param",
    "Part",
    "Profile",
    "Resource",
    "parse_catalog",
    "parse_profile",
    "read_verified",
]
