"""The curated model catalog manifest: strict loading and no partial state
(Sprint 6, S6-03).

`models_catalog/` at the repository root (mirroring `frameworks/`) ships
the owner-approved curated catalog (`catalog.json`) and the synthetic
recommendation profiles (`profiles.json`) as DATA. Everything here is
untrusted input handled the `framework_catalog.py` way: size-capped,
`extra="forbid"` everywhere, fail-closed refusal -- a manifest that does
not validate makes the catalog *unavailable* (fixed
``catalog_unavailable`` code), never partially loaded, never defaulted.

The manifest deliberately lives OUTSIDE the trees the model-agnostic
source guard scans (`service/src`, `app/src`, `app/src-tauri/src`): the
guard keeps guarding code, the catalog is data (`docs/MODEL_RUNTIME.md`
§3.2). Pin values are never hand-invented: they are fetched from the
Hugging Face API at the pinned revision at dev time and recorded in
`docs/MODEL_CATALOG.md` (provenance table); the env-gated live match test
re-verifies them on demand.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..model_runtime.errors import ModelRuntimeError

logger = logging.getLogger("controlgauge_service.model_catalog")

CATALOG_DIR_NAME = "models_catalog"
CATALOG_MANIFEST_FILENAME = "catalog.json"
PROFILES_FILENAME = "profiles.json"
MAX_MANIFEST_BYTES = 256 * 1024

_SHA256 = r"^[0-9a-f]{64}$"
_REVISION = r"^[0-9a-f]{40}$"
_HTTPS_URL = r"^https://[^\s]+$"
_CATALOG_VERSION = r"^[0-9]+\.[0-9]+\.[0-9]+$"
_IDENTIFIER = r"^[a-z0-9][a-z0-9.-]*$"


class _Strict(BaseModel):
    """The `framework_catalog._Strict` base, restated locally: the same
    closed, frozen, whitespace-stripping shape for every manifest model."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CatalogFileSpec(_Strict):
    """One downloadable file. The URL embeds the 40-hex revision (never a
    moving ref): pinned `resolve/<sha>/` URLs are immutable."""

    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256)
    download_url: str = Field(max_length=500, pattern=_HTTPS_URL)


class CatalogSourceProvenance(_Strict):
    repository_url: str = Field(max_length=500, pattern=_HTTPS_URL)
    revision: str = Field(pattern=_REVISION)
    revision_ref: str = Field(min_length=1, max_length=100)
    license: str = Field(min_length=1, max_length=100)
    license_url: str | None = Field(default=None, max_length=500, pattern=_HTTPS_URL)


class CatalogFootprint(_Strict):
    """Author-assigned estimates consumed by the MODEL_RUNTIME.md §5
    formulas -- requirements are computed from these plus detected
    hardware, never stored as flat per-OS fields."""

    runtime_overhead_bytes: int = Field(ge=0)
    kv_cache_bytes_per_token: int = Field(ge=0)
    notes: str = Field(min_length=1, max_length=2000)


class CatalogEvaluation(_Strict):
    """Reserved hallucination-minimization field (PD-026 highest
    priority): fail-closed against fabricated evaluation data until the
    Sprint 14 suite exists -- no value but `unevaluated` loads."""

    status: Literal["unevaluated"]
    suite_version: None = None
    results: None = None


class CatalogEntry(_Strict):
    model_id: str = Field(pattern=_IDENTIFIER, min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    format: Literal["gguf"]
    backend: Literal["llama_cpp"]
    roles: tuple[Literal["generation", "embedding"], ...] = Field(min_length=1, max_length=2)
    tier: str | None = Field(default=None, min_length=1, max_length=50)
    recommended: bool
    recommendation_withheld_reason: str | None = Field(default=None, min_length=1, max_length=100)
    context_window: int = Field(ge=1)
    curated_context_tokens: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    tokenizer: str = Field(min_length=1, max_length=100)
    footprint: CatalogFootprint
    embedding_dimensions: int | None = Field(default=None, ge=1)
    embedding_normalization: Literal["none", "l2"] | None = None
    embedding_similarity_metric: Literal["cosine", "dot_product"] | None = None
    files: tuple[CatalogFileSpec, ...] = Field(min_length=1, max_length=64)
    source: CatalogSourceProvenance
    pairing_default: bool
    evaluation: CatalogEvaluation

    @model_validator(mode="after")
    def _role_conditional_fields(self) -> CatalogEntry:
        if "embedding" in self.roles:
            if (
                self.embedding_dimensions is None
                or self.embedding_normalization is None
                or self.embedding_similarity_metric is None
            ):
                raise ValueError("embedding_fields_required")
            if self.footprint.kv_cache_bytes_per_token != 0:
                raise ValueError("embedding_kv_cache_must_be_zero")
        elif (
            self.embedding_dimensions is not None
            or self.embedding_normalization is not None
            or self.embedding_similarity_metric is not None
        ):
            raise ValueError("embedding_fields_forbidden")
        if "generation" in self.roles and self.max_output_tokens is None:
            raise ValueError("max_output_tokens_required")
        if set(self.roles) == {"embedding"} and self.max_output_tokens is not None:
            raise ValueError("max_output_tokens_forbidden")
        if self.curated_context_tokens > self.context_window:
            raise ValueError("curated_context_exceeds_window")
        if self.max_input_tokens > self.context_window:
            raise ValueError("max_input_exceeds_window")
        filenames = [entry_file.filename for entry_file in self.files]
        if len(set(filenames)) != len(filenames):
            raise ValueError("duplicate_filename")
        # No-ceiling gate (MODEL_RUNTIME.md §3.2): "recommended" and a
        # withheld reason are mutually exclusive by construction.
        if self.recommended == (self.recommendation_withheld_reason is not None):
            raise ValueError("recommended_reason_mismatch")
        return self

    @model_validator(mode="after")
    def _urls_pin_revision_and_filename(self) -> CatalogEntry:
        """Every download URL is the immutable pinned shape
        `https://huggingface.co/<repo>/resolve/<revision>/<filename>`: it
        embeds THIS entry's 40-hex revision (never a moving ref) and its
        last segment is the declared filename."""
        for entry_file in self.files:
            if f"/resolve/{self.source.revision}/" not in entry_file.download_url:
                raise ValueError("download_url_revision_mismatch")
            if not entry_file.download_url.endswith(f"/{entry_file.filename}"):
                raise ValueError("download_url_filename_mismatch")
        return self


class CatalogManifest(_Strict):
    manifest_version: Literal[1]
    catalog_version: str = Field(min_length=1, max_length=50, pattern=_CATALOG_VERSION)
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique_ids_and_pairing(self) -> CatalogManifest:
        ids = [entry.model_id for entry in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_model_id")
        # Exactly one default pairing partner per role present in the
        # manifest (D-023's combined-footprint evaluation pair).
        for role in ("generation", "embedding"):
            with_role = [entry for entry in self.entries if role in entry.roles]
            if with_role and sum(1 for entry in with_role if entry.pairing_default) != 1:
                raise ValueError("pairing_default_count")
        return self


class CatalogProfile(_Strict):
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    ram_bytes: int = Field(ge=0)
    vram_bytes: int = Field(ge=0)
    gpu_count: int = Field(ge=0)


class ProfileManifest(_Strict):
    manifest_version: Literal[1]
    profiles: tuple[CatalogProfile, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique_profile_ids(self) -> ProfileManifest:
        ids = [profile.profile_id for profile in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_profile_id")
        return self


def resolve_catalog_root() -> Path | None:
    """`models_catalog/` shipped with the app. Frozen (PyInstaller) builds
    carry it next to the bundle's data (`scripts/package-windows.ps1`);
    a source checkout finds the repository's own `models_catalog/`
    directory (mirrors `framework_catalog.default_frameworks_root`)."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        candidate = Path(frozen_root) / CATALOG_DIR_NAME
        return candidate if candidate.is_dir() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / CATALOG_DIR_NAME
        if candidate.is_dir() and (parent / "service").is_dir():
            return candidate
    return None


def _load_raw(root: Path | None, filename: str) -> dict[str, object]:
    """Read and parse one manifest under the size cap. Every failure mode
    (no root, missing file, oversized, bad JSON, non-object) is the same
    fixed ``catalog_unavailable`` failure -- never a path in an error."""
    if root is None:
        root = resolve_catalog_root()
    if root is None:
        raise ModelRuntimeError("catalog_unavailable")
    path = root / filename
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("catalog_too_large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ModelRuntimeError("catalog_unavailable") from None
    if not isinstance(raw, dict):
        raise ModelRuntimeError("catalog_unavailable")
    return raw


def load_catalog(root: Path | None = None) -> CatalogManifest:
    """The strict, fail-closed catalog load: any validation failure is
    ``catalog_unavailable`` -- the catalog is never partially loaded."""
    raw = _load_raw(root, CATALOG_MANIFEST_FILENAME)
    try:
        return CatalogManifest.model_validate(raw)
    except ValidationError:
        raise ModelRuntimeError("catalog_unavailable") from None


def load_profiles(root: Path | None = None) -> ProfileManifest:
    """The strict, fail-closed profiles load (same discipline)."""
    raw = _load_raw(root, PROFILES_FILENAME)
    try:
        return ProfileManifest.model_validate(raw)
    except ValidationError:
        raise ModelRuntimeError("catalog_unavailable") from None


def entry_total_bytes(entry: CatalogEntry) -> int:
    """The download footprint of an entry: the sum of its declared files
    (what the user must approve and what §5's disk gate consumes)."""
    return sum(entry_file.size_bytes for entry_file in entry.files)
