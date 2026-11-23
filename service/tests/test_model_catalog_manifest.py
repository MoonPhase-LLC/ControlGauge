"""S6-03: the shipped catalog manifest (content guard) and the strict
loader's failure modes.

The content tests ARE the catalog guard: the shipped
`models_catalog/catalog.json` must load against the strict schema and
satisfy every structural invariant, and its dev-verifiable pins must keep
matching `docs/DEV_MODELS.md` (the two sources are cross-checked both
ways). Loader tests mutate copies of the real manifest so every refusal
is exercised against realistic data.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from controlgauge_service.model_catalog import manifest as manifest_module
from controlgauge_service.model_catalog.manifest import (
    CATALOG_MANIFEST_FILENAME,
    PROFILES_FILENAME,
    entry_total_bytes,
    load_catalog,
    load_profiles,
    resolve_catalog_root,
)
from controlgauge_service.model_runtime.errors import ModelRuntimeError

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = REPO_ROOT / "models_catalog"

EXPECTED_MODEL_IDS = (
    "qwen2.5-1.5b-instruct-q4-k-m",
    "qwen2.5-7b-instruct-q4-k-m",
    "qwen2.5-14b-instruct-q4-k-m",
    "nomic-embed-text-v1.5-q4-k-m",
)
#: PD-026 tiers, in entry order; the embedding entry carries no tier.
EXPECTED_TIERS = ("FAST", "BALANCED", "ACCURATE", None)
#: dev-model files whose pins DEV_MODELS.md §1 independently records
#: (downloaded, hash-verified, and decode-benchmarked on the dev box).
DEV_MODELS_PINNED = {
    "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    "nomic-embed-text-v1.5.Q4_K_M.gguf",
}


@pytest.fixture(scope="module")
def shipped() -> Any:
    return json.loads((CATALOG_ROOT / CATALOG_MANIFEST_FILENAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> manifest_module.CatalogManifest:
    return load_catalog(CATALOG_ROOT)


# --- the shipped manifest loads and satisfies the structural invariants ----------


def test_shipped_manifest_loads(manifest: manifest_module.CatalogManifest) -> None:
    assert manifest.manifest_version == 1
    assert manifest.catalog_version == "1.0.0"
    assert [entry.model_id for entry in manifest.entries] == list(EXPECTED_MODEL_IDS)


def test_pd026_entries_roles_and_tiers(manifest: manifest_module.CatalogManifest) -> None:
    for entry, tier in zip(manifest.entries, EXPECTED_TIERS, strict=True):
        assert entry.tier == tier
        assert entry.format == "gguf"
        assert entry.backend == "llama_cpp"
        assert entry.recommended is True
        assert entry.recommendation_withheld_reason is None
        assert entry.evaluation.status == "unevaluated"
    assert [entry.roles for entry in manifest.entries] == [
        ("generation",),
        ("generation",),
        ("generation",),
        ("embedding",),
    ]


def test_pairing_default_is_exactly_the_7b_and_nomic(
    manifest: manifest_module.CatalogManifest,
) -> None:
    defaults = {entry.model_id for entry in manifest.entries if entry.pairing_default}
    assert defaults == {"qwen2.5-7b-instruct-q4-k-m", "nomic-embed-text-v1.5-q4-k-m"}


def test_download_urls_embed_revision_and_filename(
    manifest: manifest_module.CatalogManifest,
) -> None:
    for entry in manifest.entries:
        for entry_file in entry.files:
            url = entry_file.download_url
            assert url.startswith("https://huggingface.co/")
            assert f"/resolve/{entry.source.revision}/" in url  # never a moving ref
            assert url.endswith(f"/{entry_file.filename}")
            assert entry.source.revision_ref == "main"
            assert len(entry.source.revision) == 40


def test_shard_counts_and_nomic_embedding_fields(
    manifest: manifest_module.CatalogManifest,
) -> None:
    by_id = {entry.model_id: entry for entry in manifest.entries}
    assert len(by_id["qwen2.5-1.5b-instruct-q4-k-m"].files) == 1
    assert len(by_id["qwen2.5-7b-instruct-q4-k-m"].files) == 2
    assert len(by_id["qwen2.5-14b-instruct-q4-k-m"].files) == 3
    nomic = by_id["nomic-embed-text-v1.5-q4-k-m"]
    assert (nomic.embedding_dimensions, nomic.embedding_normalization) == (768, "l2")
    assert nomic.embedding_similarity_metric == "cosine"
    assert nomic.max_output_tokens is None
    assert nomic.footprint.kv_cache_bytes_per_token == 0
    for qwen_id in EXPECTED_MODEL_IDS[:3]:
        qwen = by_id[qwen_id]
        assert qwen.embedding_dimensions is None
        assert qwen.max_output_tokens is not None


def test_total_bytes_sums_declared_files(manifest: manifest_module.CatalogManifest) -> None:
    by_id = {entry.model_id: entry for entry in manifest.entries}
    assert entry_total_bytes(by_id["qwen2.5-1.5b-instruct-q4-k-m"]) == 1_117_320_736
    assert entry_total_bytes(by_id["qwen2.5-7b-instruct-q4-k-m"]) == 4_683_073_632
    assert entry_total_bytes(by_id["qwen2.5-14b-instruct-q4-k-m"]) == 8_988_110_496
    assert entry_total_bytes(by_id["nomic-embed-text-v1.5-q4-k-m"]) == 84_106_624


def test_dev_verifiable_pins_match_dev_models_md(
    manifest: manifest_module.CatalogManifest,
) -> None:
    """The doc↔manifest cross-check: every pin DEV_MODELS.md §1 records
    independently (the dev models were downloaded and hash-verified there)
    must be byte-identical in the shipped manifest."""
    doc = (REPO_ROOT / "docs" / "DEV_MODELS.md").read_text(encoding="utf-8")
    checked = 0
    for entry in manifest.entries:
        for entry_file in entry.files:
            if entry_file.filename not in DEV_MODELS_PINNED:
                continue
            assert entry_file.sha256 in doc, entry_file.filename
            assert f"{entry_file.size_bytes:,}" in doc, entry_file.filename
            checked += 1
    assert checked == len(DEV_MODELS_PINNED)


def test_shipped_profiles_load() -> None:
    profiles = load_profiles(CATALOG_ROOT)
    assert [profile.profile_id for profile in profiles.profiles] == [
        "consumer-cpu",
        "workstation-32gb",
        "enterprise-288gb",
    ]
    enterprise = profiles.profiles[2]
    assert (enterprise.ram_bytes, enterprise.vram_bytes, enterprise.gpu_count) == (
        274_877_906_944,
        309_237_645_312,
        8,
    )


# --- loader failure modes (fail-closed, fixed code, no paths) ---------------------


@pytest.fixture()
def tmp_root(tmp_path: Path, shipped: Any) -> Path:
    shutil.copytree(CATALOG_ROOT, tmp_path / manifest_module.CATALOG_DIR_NAME)
    return tmp_path / manifest_module.CATALOG_DIR_NAME


def _write(root: Path, payload: Any, filename: str = CATALOG_MANIFEST_FILENAME) -> None:
    (root / filename).write_text(json.dumps(payload), encoding="utf-8")


def _refused(root: Path | None) -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        load_catalog(root)
    assert excinfo.value.code == "catalog_unavailable"
    assert "models_catalog" not in str(excinfo.value)  # T-10: no path in the message


def test_missing_root_is_unavailable(tmp_path: Path) -> None:
    _refused(tmp_path / "absent")


def test_missing_file_is_unavailable(tmp_root: Path) -> None:
    (tmp_root / CATALOG_MANIFEST_FILENAME).unlink()
    _refused(tmp_root)


def test_oversized_manifest_is_refused(tmp_root: Path) -> None:
    # 256 KiB + 1: the size cap fires before any parse. Small allocation.
    (tmp_root / CATALOG_MANIFEST_FILENAME).write_bytes(
        b"x" * (manifest_module.MAX_MANIFEST_BYTES + 1)
    )
    _refused(tmp_root)


def test_json_syntax_error_is_refused(tmp_root: Path) -> None:
    (tmp_root / CATALOG_MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    _refused(tmp_root)


def test_non_object_manifest_is_refused(tmp_root: Path) -> None:
    (tmp_root / CATALOG_MANIFEST_FILENAME).write_text("[1, 2]", encoding="utf-8")
    _refused(tmp_root)


def test_unknown_extra_field_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["bogus"] = 1
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_entry_extra_field_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][0]["bogus"] = 1
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_evaluation_status_other_than_unevaluated_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][0]["evaluation"]["status"] = "evaluated"
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_evaluation_results_are_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][0]["evaluation"]["results"] = {"accuracy": 1.0}
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_duplicate_model_id_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"].append(copy.deepcopy(payload["entries"][0]))
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_second_generation_pairing_default_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][0]["pairing_default"] = True
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_recommended_with_withheld_reason_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][0]["recommendation_withheld_reason"] = "evaluation_suite_pending"
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_embedding_fields_on_generation_entry_are_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][0]["embedding_dimensions"] = 768
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_embedding_entry_missing_dimensions_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][3]["embedding_dimensions"] = None
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_max_output_tokens_on_embedding_entry_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][3]["max_output_tokens"] = 512
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_nonzero_kv_cache_on_embedding_entry_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    payload["entries"][3]["footprint"]["kv_cache_bytes_per_token"] = 100
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_http_download_url_is_refused(tmp_root: Path, shipped: Any) -> None:
    payload = copy.deepcopy(shipped)
    entry_file = payload["entries"][0]["files"][0]
    entry_file["download_url"] = entry_file["download_url"].replace("https://", "http://")
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_moving_ref_download_url_is_refused(tmp_root: Path, shipped: Any) -> None:
    # The URL must embed the immutable revision; a `resolve/main/` URL is
    # a moving ref and must never validate.
    payload = copy.deepcopy(shipped)
    entry = payload["entries"][0]
    entry["files"][0]["download_url"] = entry["files"][0]["download_url"].replace(
        f"/resolve/{entry['source']['revision']}/", "/resolve/main/"
    )
    _write(tmp_root, payload)
    _refused(tmp_root)


def test_profiles_failure_modes(tmp_root: Path, shipped: Any) -> None:
    profiles = json.loads((CATALOG_ROOT / PROFILES_FILENAME).read_text(encoding="utf-8"))
    (tmp_root / PROFILES_FILENAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(ModelRuntimeError) as excinfo:
        load_profiles(tmp_root)
    assert excinfo.value.code == "catalog_unavailable"
    profiles["profiles"].append(copy.deepcopy(profiles["profiles"][0]))
    _write(tmp_root, profiles, filename=PROFILES_FILENAME)
    with pytest.raises(ModelRuntimeError) as excinfo:
        load_profiles(tmp_root)
    assert excinfo.value.code == "catalog_unavailable"


# --- root resolution ----------------------------------------------------------------


def test_resolve_catalog_root_from_source_tree() -> None:
    assert resolve_catalog_root() == REPO_ROOT / "models_catalog"


def test_resolve_catalog_root_from_frozen_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = tmp_path / "bundle"
    shutil.copytree(CATALOG_ROOT, frozen / manifest_module.CATALOG_DIR_NAME)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    assert resolve_catalog_root() == frozen / manifest_module.CATALOG_DIR_NAME
    # root=None resolves through the frozen layout and loads.
    loaded = load_catalog()
    assert loaded.catalog_version == "1.0.0"


def test_resolve_catalog_root_frozen_layout_without_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resolve_catalog_root() is None
    _refused(None)
