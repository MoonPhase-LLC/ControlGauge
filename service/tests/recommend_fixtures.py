"""Shared synthetic factories for the S6-06 recommendation tests.

Everything here is pure in-memory data: synthetic `CatalogEntry` and
`HardwareInfo` values with no relation to the dev box, so the rule tests
are deterministic on any machine (PD-026). The SHIPPED manifest/profile
pinning lives in `test_recommend_pairs.py` / `test_recommend_routes.py`
and uses `load_catalog()`/`load_profiles()` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from controlgauge_service.hardware.models import GpuInfo, HardwareInfo
from controlgauge_service.model_catalog.manifest import CatalogEntry

GIB = 1024**3

#: Synthetic content identity (64 hex / 40 hex) -- never a real digest.
_SHA256 = "ab" * 32
_REVISION = "cd" * 20


def make_entry(
    model_id: str = "entry-a",
    *,
    roles: tuple[Literal["generation", "embedding"], ...] = ("generation",),
    recommended: bool = True,
    weights: tuple[int, ...] = (1 * GIB,),
    kv_per_token: int = 0,
    overhead: int = 0,
    curated_context: int = 1024,
    pairing_default: bool = False,
) -> CatalogEntry:
    """One synthetic catalog entry. `total` = Σ weights + kv×ctx +
    overhead; with the defaults that is exactly 1 GiB."""
    is_embedding = "embedding" in roles
    return CatalogEntry(
        model_id=model_id,
        display_name=f"Display {model_id}",
        format="gguf",
        backend="llama_cpp",
        roles=roles,
        recommended=recommended,
        recommendation_withheld_reason=None if recommended else "evaluation_suite_pending",
        context_window=max(curated_context, 2048),
        curated_context_tokens=curated_context,
        max_input_tokens=curated_context,
        max_output_tokens=curated_context if not is_embedding else None,
        tokenizer="synthetic-tokenizer",
        footprint={
            "runtime_overhead_bytes": overhead,
            "kv_cache_bytes_per_token": 0 if is_embedding else kv_per_token,
            "notes": "synthetic test fixture",
        },
        embedding_dimensions=768 if is_embedding else None,
        embedding_normalization="l2" if is_embedding else None,
        embedding_similarity_metric="cosine" if is_embedding else None,
        files=tuple(
            {
                "filename": f"{model_id}-{index}.gguf",
                "size_bytes": size,
                "sha256": _SHA256,
                "download_url": (
                    f"https://example.invalid/org/repo/resolve/{_REVISION}/"
                    f"{model_id}-{index}.gguf"
                ),
            }
            for index, size in enumerate(weights)
        ),
        source={
            "repository_url": "https://example.invalid/org/repo",
            "revision": _REVISION,
            "revision_ref": "main",
            "license": "apache-2.0",
        },
        pairing_default=pairing_default,
        evaluation={"status": "unevaluated"},
    )


def make_gpu(vram_bytes: int | None, *, vendor: str = "nvidia") -> GpuInfo:
    return GpuInfo(vendor=vendor, name=None, vram_bytes=vram_bytes)


def make_hw(
    *,
    ram: int | None = 16 * GIB,
    gpus: tuple[GpuInfo, ...] = (),
    free: int | None = None,
) -> HardwareInfo:
    """A synthetic snapshot: known scalars by default, empty GPU list
    (the CPU-only shape), disk unknown unless pinned."""
    return HardwareInfo(
        os_name="linux",
        os_version="synthetic",
        cpu_model="Synthetic CPU",
        cpu_cores_logical=8,
        ram_total_bytes=ram,
        ram_available_bytes=None,
        gpus=gpus,
        model_dir_free_bytes=free,
        detected_at=datetime.now(tz=UTC),
    )
