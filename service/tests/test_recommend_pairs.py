"""S6-06: D-023 combined-pair verdicts + the pinned shipped-catalog
profile targets.

Two layers:
- synthetic pair-rule tests (the two-check form on CPU and GPU); and
- the SHIPPED manifest evaluated against the SHIPPED synthetic profiles
  (`load_catalog()`/`load_profiles()`), with exact verdicts + exact
  footprints pinned so a catalog footprint edit cannot silently break
  the owner's primary targets (PD-026 no-ceiling evidence).
"""

from __future__ import annotations

from controlgauge_service.hardware.models import HardwareInfo
from controlgauge_service.model_catalog.manifest import (
    CatalogEntry,
    CatalogProfile,
    load_catalog,
    load_profiles,
)
from controlgauge_service.model_catalog.recommend import (
    evaluate_pair,
    recommend_for_profile,
)

from .recommend_fixtures import GIB, make_entry, make_gpu, make_hw

# --- exact shipped footprints (curated context) -------------------------------------

#: Σ files + kv×ctx + overhead, computed by hand from the shipped
#: manifest and pinned here (MODEL_RUNTIME.md §5).
EXPECTED_TOTALS = {
    "qwen2.5-1.5b-instruct-q4-k-m": 1_117_320_736 + 28_672 * 8_192 + 644_245_094,
    "qwen2.5-7b-instruct-q4-k-m": (3_993_201_344 + 689_872_288) + 57_344 * 8_192 + 1_073_741_824,
    "qwen2.5-14b-instruct-q4-k-m": (3_991_999_872 + 3_989_373_504 + 1_006_737_120)
    + 196_608 * 8_192
    + 1_610_612_736,
    "nomic-embed-text-v1.5-q4-k-m": 84_106_624 + 0 + 429_496_730,
}

GEN = "qwen2.5-7b-instruct-q4-k-m"
EMB = "nomic-embed-text-v1.5-q4-k-m"


def _shipped_pair(gen_id: str = GEN, emb_id: str = EMB) -> tuple[CatalogEntry, CatalogEntry]:
    manifest = load_catalog()
    gen = next(e for e in manifest.entries if e.model_id == gen_id)
    emb = next(e for e in manifest.entries if e.model_id == emb_id)
    return gen, emb


def _profile_by_id(profile_id: str) -> CatalogProfile:
    profiles = load_profiles()
    return next(p for p in profiles.profiles if p.profile_id == profile_id)


# --- synthetic pair rules ------------------------------------------------------------


def test_combined_fit_is_simultaneous() -> None:
    gen = make_entry("gen", weights=(6 * GIB,))
    emb = make_entry("emb", weights=(1 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=16 * GIB))
    assert pair.verdict == "simultaneous"
    assert pair.reasons == ()
    assert pair.combined_total_bytes == 7 * GIB


def test_each_fits_but_combined_does_not_is_sequential_only() -> None:
    """The D-023 bug class: both models fit individually but never
    together — the honest verdict is `sequential_only`, never a
    silent simultaneous."""
    gen = make_entry("gen", weights=(9 * GIB,))
    emb = make_entry("emb", weights=(9 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=16 * GIB))
    assert pair.verdict == "sequential_only"
    assert pair.reasons == ("insufficient_ram",)


def test_neither_fits_is_infeasible() -> None:
    gen = make_entry("gen", weights=(20 * GIB,))
    emb = make_entry("emb", weights=(9 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=16 * GIB))
    assert pair.verdict == "infeasible"
    assert pair.reasons == ("insufficient_ram",)


def test_gpu_combined_fit_is_simultaneous_without_ram() -> None:
    gen = make_entry("gen", weights=(15 * GIB,))
    emb = make_entry("emb", weights=(5 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=None, gpus=(make_gpu(24 * GIB),)))
    assert pair.verdict == "simultaneous"
    assert pair.reasons == ()


def test_gpu_each_fits_combined_does_not_is_sequential_only() -> None:
    """One 8 GiB GPU (7.2 GiB usable): each 5 GiB model fits, the 10 GiB
    combination does not — sequential, with both GPU codes (one GPU
    makes the aggregate and share bounds coincide)."""
    gen = make_entry("gen", weights=(5 * GIB,))
    emb = make_entry("emb", weights=(5 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=None, gpus=(make_gpu(8 * GIB),)))
    assert pair.verdict == "sequential_only"
    assert pair.reasons == ("insufficient_vram", "exceeds_per_gpu_share")


def test_gpu_failure_falls_through_to_cpu_simultaneous() -> None:
    """GPU too small to answer at all, but the combined CPU budget
    holds: simultaneous, with the GPU failures recorded."""
    gen = make_entry("gen", weights=(5 * GIB,))
    emb = make_entry("emb", weights=(1 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=16 * GIB, gpus=(make_gpu(4 * GIB),)))
    assert pair.verdict == "simultaneous"
    assert pair.reasons == ("insufficient_vram", "exceeds_per_gpu_share")


def test_pair_needs_both_entries_on_the_cpu_path() -> None:
    """Combined over budget and only ONE entry fits: not sequential —
    infeasible (sequential requires EACH to fit)."""
    gen = make_entry("gen", weights=(20 * GIB,))
    emb = make_entry("emb", weights=(1 * GIB,))
    pair = evaluate_pair(gen, emb, make_hw(ram=16 * GIB))
    assert pair.verdict == "infeasible"


# --- shipped catalog × shipped profiles (pinned) --------------------------------------


def test_footprints_of_the_shipped_catalog_are_pinned() -> None:
    manifest = load_catalog()
    for entry in manifest.entries:
        from controlgauge_service.model_catalog.recommend import estimate_footprint

        estimated = estimate_footprint(entry)
        assert estimated.total_bytes == EXPECTED_TOTALS[entry.model_id], entry.model_id


def test_consumer_cpu_profile_pins_the_tier_contract() -> None:
    """16 GiB RAM, no GPU (exact verdicts from the §5 math): 1.5B
    recommended; 7B recommended (9.34 GiB ×1.5 headroom still fits the
    11.2 GiB budget); 14B infeasible (12.2 GiB total over the budget);
    nomic recommended. The 7B+nomic default pair is simultaneous."""
    report = recommend_for_profile(load_catalog(), _profile_by_id("consumer-cpu"))
    assert report.hardware_source == "profile:consumer-cpu"
    verdicts = {v.model_id: v.verdict for v in report.entries}
    assert verdicts == {
        "qwen2.5-1.5b-instruct-q4-k-m": "recommended",
        "qwen2.5-7b-instruct-q4-k-m": "recommended",
        "qwen2.5-14b-instruct-q4-k-m": "infeasible",
        "nomic-embed-text-v1.5-q4-k-m": "recommended",
    }
    by_id = {(p.generation_model_id, p.embedding_model_id): p.verdict for p in report.pairs}
    assert by_id[("qwen2.5-1.5b-instruct-q4-k-m", "nomic-embed-text-v1.5-q4-k-m")] == "simultaneous"
    assert by_id[(GEN, EMB)] == "simultaneous"
    assert by_id[("qwen2.5-14b-instruct-q4-k-m", "nomic-embed-text-v1.5-q4-k-m")] == "infeasible"
    gen_verdict = next(v for v in report.entries if v.model_id == GEN)
    assert gen_verdict.load_mode == "simultaneous"
    infeasible = next(v for v in report.entries if v.model_id == "qwen2.5-14b-instruct-q4-k-m")
    assert infeasible.reasons == ("insufficient_ram",)


def test_workstation_32gb_profile_recommendations() -> None:
    """32 GiB VRAM (1 GPU) + 64 GiB RAM: every shipped entry fits on the
    GPU (the 14B's 11.4 GiB total is well under the 28.8 GiB usable) —
    all recommended, the default pair simultaneous."""
    report = recommend_for_profile(load_catalog(), _profile_by_id("workstation-32gb"))
    assert all(v.verdict == "recommended" for v in report.entries)
    pair = next(p for p in report.pairs if p.generation_model_id == GEN)
    assert pair.verdict == "simultaneous"
    assert pair.combined_total_bytes == (
        EXPECTED_TOTALS[GEN] + EXPECTED_TOTALS["nomic-embed-text-v1.5-q4-k-m"]
    )


def test_enterprise_288gb_profile_marks_the_full_catalog_recommended() -> None:
    """The no-ceiling evidence: 8×36 GiB + 256 GiB RAM recommends the
    ENTIRE shipped catalog with no ceiling."""
    report = recommend_for_profile(load_catalog(), _profile_by_id("enterprise-288gb"))
    assert report.hardware_source == "profile:enterprise-288gb"
    assert {v.model_id for v in report.entries} == set(EXPECTED_TOTALS)
    assert all(v.verdict == "recommended" for v in report.entries)
    assert all(p.verdict == "simultaneous" for p in report.pairs)


# --- load mode + report assembly ------------------------------------------------------


def test_generation_load_mode_comes_from_the_best_pair() -> None:
    """`simultaneous` beats `sequential_only`; an infeasible pair
    contributes no load mode; embedding verdicts carry none."""
    manifest = load_catalog()
    gen = next(e for e in manifest.entries if e.model_id == "qwen2.5-1.5b-instruct-q4-k-m")
    emb = next(e for e in manifest.entries if e.model_id == EMB)
    # 16 GiB RAM, no GPU: 1.5B + nomic fit together.
    report = recommend_for_profile(manifest, _profile_by_id("consumer-cpu"))
    small = next(v for v in report.entries if v.model_id == gen.model_id)
    assert small.load_mode == "simultaneous"
    embedding = next(v for v in report.entries if v.model_id == emb.model_id)
    assert embedding.load_mode is None
    assert embedding.role == "embedding"


def test_undecidable_pair_is_omitted_never_claimed() -> None:
    """No GPU path answer + unknown RAM: the pair is left out of the
    report instead of being labelled infeasible (the documented honesty
    rule)."""
    gen = make_entry("gen", weights=(100 * GIB,))
    emb = make_entry("emb", weights=(50 * GIB,))
    hw: HardwareInfo = make_hw(ram=None)  # no GPUs either
    report_gen = evaluate_pair(gen, emb, hw)
    assert report_gen.reasons == ("hardware_unknown",)


def test_pair_guard_restricts_to_pairing_defaults_above_25() -> None:
    """26 gen×emb combinations exceed the documented guard: only the
    curator-designated default pairing partners are evaluated."""
    from controlgauge_service.model_catalog.manifest import CatalogManifest
    from controlgauge_service.model_catalog.recommend import MAX_PAIR_EVALUATIONS, recommend

    entries = [make_entry(f"gen-{i:02d}", pairing_default=(i == 0)) for i in range(26)] + [
        make_entry("emb", roles=("embedding",), pairing_default=True)
    ]
    manifest = CatalogManifest(manifest_version=1, catalog_version="1.0.0", entries=entries)
    report = recommend(manifest, make_hw(ram=16 * GIB))
    assert len(report.pairs) == 1
    assert report.pairs[0].generation_model_id == "gen-00"
    assert report.pairs[0].embedding_model_id == "emb"
    assert MAX_PAIR_EVALUATIONS == 25
