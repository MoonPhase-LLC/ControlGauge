"""S6-03: the revision-pin drift detector (env-gated, real network).

For every catalog entry, fetch the Hugging Face API at the PINNED
revision (never a moving ref) and assert every declared file's size and
SHA-256 still match the manifest exactly. Skipped unless
``CONTROLGAUGE_TEST_ALLOW_DOWNLOAD=1`` (Windows CI never sets it; dev
runs it on demand -- the same gate as the download tests). The pins were
fetched from the same API at curation time (docs/MODEL_CATALOG.md
provenance table); this test is the on-demand re-verification.

Module-level env read is deliberate: collection evaluates it while the
ambient environment is intact (conftest's ``_isolated_environment``
strips CONTROLGAUGE_* only at fixture time), so the skipif behaves.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from controlgauge_service.model_catalog.manifest import (
    CatalogEntry,
    load_catalog,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CONTROLGAUGE_TEST_ALLOW_DOWNLOAD") != "1",
    reason="real-network pin verification; enable with CONTROLGAUGE_TEST_ALLOW_DOWNLOAD=1",
)

CATALOG_ROOT = Path(__file__).resolve().parents[2] / "models_catalog"

ENTRIES: tuple[CatalogEntry, ...] = load_catalog(CATALOG_ROOT).entries


@pytest.mark.parametrize("entry", ENTRIES, ids=[entry.model_id for entry in ENTRIES])
def test_manifest_pins_match_live_huggingface(entry: CatalogEntry) -> None:
    """Every declared file's size and SHA-256 match the pinned revision's
    live HF API state, exactly."""
    repo_id = entry.source.repository_url.removeprefix("https://huggingface.co/")
    url = (
        f"https://huggingface.co/api/models/{repo_id}"
        f"/revision/{entry.source.revision}?blobs=true"
    )
    # trust_env=False: never route through ambient proxy variables (the
    # loopback-discipline rule, applied to the one sanctioned external
    # host this gate exists for).
    with httpx.Client(trust_env=False, timeout=30.0) as client:
        response = client.get(url)
    assert response.status_code == 200, response.text
    payload = response.json()
    # The revision endpoint answers with the commit the URL pinned.
    assert payload["sha"] == entry.source.revision
    siblings = {sibling["rfilename"]: sibling for sibling in payload["siblings"]}
    for declared in entry.files:
        sibling = siblings.get(declared.filename)
        assert sibling is not None, f"file missing upstream: {declared.filename}"
        lfs = sibling.get("lfs")
        assert lfs is not None, f"not an LFS file upstream: {declared.filename}"
        assert lfs["size"] == declared.size_bytes, declared.filename
        assert lfs["sha256"] == declared.sha256, declared.filename
