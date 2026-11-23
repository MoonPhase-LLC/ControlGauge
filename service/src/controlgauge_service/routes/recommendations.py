"""Hardware-aware recommendation routes (Sprint 6, S6-06).

    GET /recommendations                      detected-hardware report
    GET /recommendations/profile/{profile_id} synthetic-profile report

Structural by contract (global contract 1): both routes are PURE
computation over the strictly loaded manifest + either the cached
hardware snapshot or a synthetic profile — no model-child contact, no
live re-detection, no network. The math lives in
`model_catalog/recommend.py` (`MODEL_RUNTIME.md` §5); this module only
projects the closed wire shape. No local path, URL, or credential ever
appears (T-10): hardware facts are byte counts and vendor names.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ..hardware.detect import cached_hardware
from ..model_catalog.manifest import (
    CatalogManifest,
    CatalogProfile,
    load_catalog,
    load_profiles,
)
from ..model_catalog.recommend import (
    RecommendationReport,
    recommend,
    recommend_for_profile,
)
from ..model_runtime.errors import ModelRuntimeError
from .errors import ApiError
from .hardware import _resolve_data_dir

logger = logging.getLogger("controlgauge_service.routes.recommendations")

router = APIRouter()


# --- response schemas (closed) ---------------------------------------------------


class FootprintOut(BaseModel):
    """The §5 footprint estimate in bytes."""

    weights_bytes: int
    kv_cache_bytes: int
    runtime_overhead_bytes: int
    total_bytes: int


class EntryVerdictOut(BaseModel):
    """One entry's feasibility for one declared role. ``verdict`` is the
    closed `recommended|feasible|infeasible|unknown` vocabulary,
    ``load_mode`` the closed `simultaneous|sequential` one (generation
    roles only, from the entry's best pair); ``reasons`` members of the
    fixed reason-code vocabulary."""

    model_id: str
    role: str
    verdict: str
    load_mode: str | None
    estimated: FootprintOut
    reasons: list[str]


class PairVerdictOut(BaseModel):
    """One generation×embedding combination (D-023). ``verdict`` is the
    closed `simultaneous|sequential_only|infeasible` vocabulary."""

    generation_model_id: str
    embedding_model_id: str
    verdict: str
    combined_total_bytes: int
    reasons: list[str]


class RecommendationReportOut(BaseModel):
    """The full report. ``hardware_source`` is `detected` or
    `profile:<id>`; undecidable pairs are omitted (never claimed)."""

    hardware_source: str
    entries: list[EntryVerdictOut]
    pairs: list[PairVerdictOut]


# --- helpers -----------------------------------------------------------------------


def _to_out(report: RecommendationReport) -> RecommendationReportOut:
    return RecommendationReportOut(
        hardware_source=report.hardware_source,
        entries=[
            EntryVerdictOut(
                model_id=verdict.model_id,
                role=verdict.role,
                verdict=verdict.verdict,
                load_mode=verdict.load_mode,
                estimated=FootprintOut(
                    weights_bytes=verdict.estimated.weights_bytes,
                    kv_cache_bytes=verdict.estimated.kv_cache_bytes,
                    runtime_overhead_bytes=verdict.estimated.runtime_overhead_bytes,
                    total_bytes=verdict.estimated.total_bytes,
                ),
                reasons=list(verdict.reasons),
            )
            for verdict in report.entries
        ],
        pairs=[
            PairVerdictOut(
                generation_model_id=pair.generation_model_id,
                embedding_model_id=pair.embedding_model_id,
                verdict=pair.verdict,
                combined_total_bytes=pair.combined_total_bytes,
                reasons=list(pair.reasons),
            )
            for pair in report.pairs
        ],
    )


def _load_manifest_or_503() -> CatalogManifest:
    """Mirrors the catalog router's fail-closed load: an unreadable or
    invalid manifest is a fixed 503, never a partial answer and never
    manifest content in an error."""
    try:
        return load_catalog()
    except ModelRuntimeError as exc:
        raise ApiError(503, exc.code, str(exc)) from None


def _profiles_or_503() -> tuple[CatalogProfile, ...]:
    """The strictly loaded profiles; same fail-closed discipline as the
    catalog manifest."""
    try:
        return load_profiles().profiles
    except ModelRuntimeError as exc:
        raise ApiError(503, exc.code, str(exc)) from None


def _profile_or_404(profile_id: str, profiles: tuple[CatalogProfile, ...]) -> CatalogProfile:
    for profile in profiles:
        if profile.profile_id == profile_id:
            return profile
    raise ApiError(404, "profile_unknown", "The requested recommendation profile does not exist.")


# --- routes --------------------------------------------------------------------------


@router.get("/recommendations")
def recommendations() -> RecommendationReportOut:
    """The detected-hardware report from the CACHED snapshot (never a
    live re-detection — that is the explicit POST /hardware/refresh).
    No cache yet is 503: the user runs detection first (USER_FLOWS
    §2.4)."""
    info = cached_hardware(_resolve_data_dir())
    if info is None:
        raise ApiError(503, "hardware_unavailable", "Hardware detection is not available.")
    return _to_out(recommend(_load_manifest_or_503(), info))


@router.get("/recommendations/profile/{profile_id}")
def profile_recommendations(profile_id: str) -> RecommendationReportOut:
    """The same math on a synthetic profile (the 32 GB / 288 GB owner
    targets of `MODEL_RUNTIME.md` §3.3). An unknown profile id is 404
    `profile_unknown`."""
    profile = _profile_or_404(profile_id, _profiles_or_503())
    return _to_out(recommend_for_profile(_load_manifest_or_503(), profile))
