"""Curated model catalog (Sprint 6, S6-03).

Tier 1 of the two-tier model universe (PD-026): the in-repo, versioned,
revision-pinned manifest shipped as data in `models_catalog/`, strictly
loaded and served structurally by `GET /model-catalog`. Catalog entries
are never written into the user-managed registry by browsing -- only an
explicit user-approved download creates a registry entry (S6-04).
"""

from .manifest import (
    CATALOG_DIR_NAME,
    CATALOG_MANIFEST_FILENAME,
    MAX_MANIFEST_BYTES,
    PROFILES_FILENAME,
    CatalogEntry,
    CatalogManifest,
    CatalogProfile,
    ProfileManifest,
    entry_total_bytes,
    load_catalog,
    load_profiles,
    resolve_catalog_root,
)

__all__ = [
    "CATALOG_DIR_NAME",
    "CATALOG_MANIFEST_FILENAME",
    "MAX_MANIFEST_BYTES",
    "PROFILES_FILENAME",
    "CatalogEntry",
    "CatalogManifest",
    "CatalogProfile",
    "ProfileManifest",
    "entry_total_bytes",
    "load_catalog",
    "load_profiles",
    "resolve_catalog_root",
]
