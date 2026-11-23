"""Hardware-aware recommendation engine (Sprint 6, S6-06).

The normative math is `docs/MODEL_RUNTIME.md` §5; this module implements
it verbatim as PURE functions over the strictly loaded manifest plus a
`HardwareInfo` (detected or synthetic profile). No I/O, no clock, no
randomness: the same inputs always produce the same verdicts, which is
what makes the pinned synthetic-fixture tests meaningful (PD-026).

Verdict vocabularies (closed):
    entry:  "recommended" | "feasible" | "infeasible" | "unknown"
    pair:   "simultaneous" | "sequential_only" | "infeasible"

Reason codes (fixed vocabulary, reported in evaluation order):
    insufficient_ram, insufficient_vram, exceeds_per_gpu_share,
    insufficient_disk, hardware_unknown, curator_withheld

Honesty rules baked into the math:

- Any input the deciding path NEEDS but cannot get (``ram_total_bytes``
  unknown with no usable GPU path) yields ``unknown`` — never
  ``infeasible``. Infeasibility is only ever claimed from known numbers.
- An unknown free-disk-byte count SKIPS the disk gate (a gate can only
  fire on known data); synthetic profiles carry no disk figure by
  design, so this is also what keeps profile verdicts computable.
- A GPU whose VRAM the probe could not determine is excluded from GPU
  budgeting (never guessed); if that leaves no usable GPU, the CPU path
  decides. The aggregate is NOT one contiguous pool (PD-026): every GPU
  must hold its share.
- A feasibility pass on a ``recommended: false`` entry yields
  ``feasible`` with reason ``curator_withheld`` — larger entries can be
  listed feasible, never auto-promoted (the no-ceiling rule).
- A pair whose deciding inputs are unknown (GPU path unusable, RAM
  unknown) is OMITTED from reports: the pair vocabulary has no
  ``unknown``, and claiming ``infeasible`` from unknown data would be
  dishonest. On any real detection or profile the RAM figure is known,
  so this edge is rare and documented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..hardware.models import GpuInfo, HardwareInfo
from .manifest import CatalogEntry, CatalogManifest, CatalogProfile, entry_total_bytes

#: Headroom constants (`MODEL_RUNTIME.md` §5 -- the exact documented
#: fractions; do not change without editing that section in the same
#: commit).
USABLE_RAM_FRACTION = 0.7  # headroom for OS/service/evidence workloads
USABLE_VRAM_FRACTION = 0.9  # driver-reported totals overstate usable VRAM
RECOMMENDED_HEADROOM_FACTOR = 1.5  # recommended = total*1.5 still fits
MIN_DISK_MARGIN_BYTES = 1 << 30  # 1 GiB

#: Exact integer forms of the fractions above. 0.7 and 0.9 are not
#: binary-exact floats; the integer forms ARE the normative formulas
#: (floored), and integer arithmetic keeps every comparison
#: deterministic across platforms.
_RAM_NUMER, _RAM_DENOM = 7, 10
_VRAM_NUMER, _VRAM_DENOM = 9, 10
_HEADROOM_NUMER, _HEADROOM_DENOM = 3, 2  # RECOMMENDED_HEADROOM_FACTOR

#: Verdict / reason vocabularies (closed; pinned by test).
ENTRY_VERDICTS = ("recommended", "feasible", "infeasible", "unknown")
PAIR_VERDICTS = ("simultaneous", "sequential_only", "infeasible")
LOAD_MODES = ("simultaneous", "sequential")
REASON_CODES = (
    "insufficient_ram",
    "insufficient_vram",
    "exceeds_per_gpu_share",
    "insufficient_disk",
    "hardware_unknown",
    "curator_withheld",
)

#: Above this many gen×emb combinations the pair matrix is restricted to
#: `pairing_default` entries (documented guard; the shipped catalog is
#: 3×1 = 3).
MAX_PAIR_EVALUATIONS = 25


@dataclass(frozen=True)
class FootprintEstimate:
    """The §5 footprint of one entry at one context size."""

    weights_bytes: int
    kv_cache_bytes: int
    runtime_overhead_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class EntryVerdict:
    """One entry's feasibility for one of its declared roles."""

    model_id: str
    role: str
    verdict: str  # member of ENTRY_VERDICTS
    load_mode: str | None  # member of LOAD_MODES, or None
    estimated: FootprintEstimate
    reasons: tuple[str, ...]  # members of REASON_CODES


@dataclass(frozen=True)
class PairVerdict:
    """One generation×embedding combination (D-023)."""

    generation_model_id: str
    embedding_model_id: str
    verdict: str  # member of PAIR_VERDICTS
    combined_total_bytes: int
    reasons: tuple[str, ...]  # members of REASON_CODES


@dataclass(frozen=True)
class RecommendationReport:
    """The full deterministic report for one hardware source."""

    hardware_source: str  # "detected" | "profile:<id>"
    entries: tuple[EntryVerdict, ...]
    pairs: tuple[PairVerdict, ...]


# --- footprint ---------------------------------------------------------------------


def estimate_footprint(
    entry: CatalogEntry, *, context_tokens: int | None = None
) -> FootprintEstimate:
    """`MODEL_RUNTIME.md` §5: weights = Σ files; kv = per-token × context
    (defaulting to the entry's curated context); total adds the
    author-assigned runtime overhead."""
    weights = entry_total_bytes(entry)
    tokens = entry.curated_context_tokens if context_tokens is None else context_tokens
    kv = entry.footprint.kv_cache_bytes_per_token * tokens
    overhead = entry.footprint.runtime_overhead_bytes
    return FootprintEstimate(
        weights_bytes=weights,
        kv_cache_bytes=kv,
        runtime_overhead_bytes=overhead,
        total_bytes=weights + kv + overhead,
    )


# --- budgets -----------------------------------------------------------------------


def _ram_budget(hw: HardwareInfo) -> int | None:
    """The CPU budget: `ram_total_bytes × 0.7` in exact integer form.
    RAM *available* bytes are deliberately NOT used — available RAM
    fluctuates; feasibility must be stable across a launch. ``None`` =
    unknown: the CPU path cannot decide."""
    if hw.ram_total_bytes is None:
        return None
    return hw.ram_total_bytes * _RAM_NUMER // _RAM_DENOM


@dataclass(frozen=True)
class _GpuBudget:
    """Usable GPU budget: only GPUs with KNOWN ``vram_bytes`` contribute
    (an unknown VRAM is never guessed); ``gpu_count`` counts those."""

    total_bytes: int
    per_gpu_bytes: int
    gpu_count: int


def _gpu_budget(hw: HardwareInfo) -> _GpuBudget | None:
    usable = [gpu.vram_bytes for gpu in hw.gpus if gpu.vram_bytes is not None]
    if not usable:
        return None
    return _GpuBudget(
        total_bytes=sum(usable) * _VRAM_NUMER // _VRAM_DENOM,
        per_gpu_bytes=min(usable) * _VRAM_NUMER // _VRAM_DENOM,
        gpu_count=len(usable),
    )


# --- shared checks -------------------------------------------------------------------


def _fits_gpu(total: int, budget: _GpuBudget, reasons: list[str]) -> bool:
    """Full-offload GPU model: the aggregate AND every GPU's share must
    hold (the aggregate is NOT one contiguous pool, PD-026).
    ``total ≤ per_gpu × gpu_count`` is the exact integer form of
    ``total / gpu_count ≤ per_gpu``."""
    aggregate_ok = total <= budget.total_bytes
    share_ok = total <= budget.per_gpu_bytes * budget.gpu_count
    if aggregate_ok and share_ok:
        return True
    if not aggregate_ok:
        reasons.append("insufficient_vram")
    if not share_ok:
        reasons.append("exceeds_per_gpu_share")
    return False


def _disk_gate(entry: CatalogEntry, hw: HardwareInfo, reasons: list[str]) -> bool:
    """True when the entry is REFUSED on disk (known free bytes only):
    `entry_total_bytes + 1 GiB ≤ model_dir_free_bytes` else infeasible,
    regardless of memory. An unknown figure skips the gate — it can
    never fabricate a refusal."""
    free = hw.model_dir_free_bytes
    if free is None:
        return False
    if entry_total_bytes(entry) + MIN_DISK_MARGIN_BYTES > free:
        reasons.append("insufficient_disk")
        return True
    return False


def _recommended_gate(
    entry: CatalogEntry, total: int, budget_bytes: int, reasons: list[str]
) -> str:
    """Feasibility passed; `recommended` additionally requires the ×1.5
    headroom (`total × 1.5 ≤ budget`, the deciding path's budget) AND
    the curator's ``recommended`` flag — a fit on `recommended: false`
    is `feasible` with `curator_withheld` (the no-ceiling honesty
    rule)."""
    headroom_ok = _HEADROOM_NUMER * total <= _HEADROOM_DENOM * budget_bytes
    if entry.recommended and headroom_ok:
        return "recommended"
    if not entry.recommended:
        reasons.append("curator_withheld")
    return "feasible"


# --- entry evaluation ---------------------------------------------------------------


def evaluate_entry(entry: CatalogEntry, hw: HardwareInfo) -> EntryVerdict:
    """One entry's feasibility (pure). The math is role-independent, so
    the returned ``role`` is the entry's FIRST declared role;
    `recommend` evaluates every declared role by re-labeling. Disk gate
    first (refusals regardless of memory), GPU path preferred, CPU path
    decides the fallback; an undecidable fallback is ``unknown``."""
    estimated = estimate_footprint(entry)
    total = estimated.total_bytes
    role = entry.roles[0]
    reasons: list[str] = []
    if _disk_gate(entry, hw, reasons):
        return EntryVerdict(entry.model_id, role, "infeasible", None, estimated, tuple(reasons))
    gpu = _gpu_budget(hw)
    if gpu is not None and _fits_gpu(total, gpu, reasons):
        return EntryVerdict(
            entry.model_id,
            role,
            _recommended_gate(entry, total, gpu.total_bytes, reasons),
            None,
            estimated,
            tuple(reasons),
        )
    budget = _ram_budget(hw)
    if budget is None:
        # The GPU path (if any) did not fit and the CPU path cannot
        # decide: unknown, never infeasible.
        reasons.append("hardware_unknown")
        return EntryVerdict(entry.model_id, role, "unknown", None, estimated, tuple(reasons))
    if total > budget:
        reasons.append("insufficient_ram")
        return EntryVerdict(entry.model_id, role, "infeasible", None, estimated, tuple(reasons))
    return EntryVerdict(
        entry.model_id,
        role,
        _recommended_gate(entry, total, budget, reasons),
        None,
        estimated,
        tuple(reasons),
    )


# --- pair evaluation ----------------------------------------------------------------


def evaluate_pair(gen: CatalogEntry, emb: CatalogEntry, hw: HardwareInfo) -> PairVerdict:
    """D-023 combined-footprint verdict for one generation×embedding
    pair (pure). GPU path preferred (the same two-check form: combined
    fits → simultaneous; each fits individually → sequential_only), CPU
    path decides the fallback. An undecidable pair returns the
    ``hardware_unknown`` sentinel verdict (infeasible + that reason):
    `_assemble` omits such pairs from reports — see the module
    docstring."""
    gen_total = estimate_footprint(gen).total_bytes
    emb_total = estimate_footprint(emb).total_bytes
    combined_total = gen_total + emb_total
    reasons: list[str] = []

    def result(verdict: str, pair_reasons: list[str]) -> PairVerdict:
        return PairVerdict(
            generation_model_id=gen.model_id,
            embedding_model_id=emb.model_id,
            verdict=verdict,
            combined_total_bytes=combined_total,
            reasons=tuple(pair_reasons),
        )

    gpu = _gpu_budget(hw)
    if gpu is not None:
        combined_reasons: list[str] = []
        if _fits_gpu(combined_total, gpu, combined_reasons):
            return result("simultaneous", reasons)
        gen_fits = _fits_gpu(gen_total, gpu, [])
        emb_fits = _fits_gpu(emb_total, gpu, [])
        if gen_fits and emb_fits:
            return result("sequential_only", combined_reasons)
        reasons.extend(combined_reasons)  # GPU context for the CPU fallback
    budget = _ram_budget(hw)
    if budget is None:
        # Undecidable: the sentinel the report assembler omits.
        return result("infeasible", ["hardware_unknown"])
    if combined_total <= budget:
        return result("simultaneous", reasons)
    if gen_total <= budget and emb_total <= budget:
        return result("sequential_only", [*reasons, "insufficient_ram"])
    return result("infeasible", [*reasons, "insufficient_ram"])


# --- report assembly ----------------------------------------------------------------


def _pairable(manifest: CatalogManifest) -> tuple[list[CatalogEntry], list[CatalogEntry]]:
    gens = [e for e in manifest.entries if "generation" in e.roles]
    embs = [e for e in manifest.entries if "embedding" in e.roles]
    if len(gens) * len(embs) > MAX_PAIR_EVALUATIONS:
        # Documented guard: restrict to the curator-designated default
        # pairing partners.
        gens = [e for e in gens if e.pairing_default]
        embs = [e for e in embs if e.pairing_default]
    return gens, embs


def _load_mode_for(gen_id: str, pairs: tuple[PairVerdict, ...]) -> str | None:
    """The generation entry's best pair: simultaneous > sequential_only;
    `infeasible` pairs contribute no load mode. Ties resolve to pair
    order (manifest order)."""
    best: str | None = None
    for pair in pairs:
        if pair.generation_model_id != gen_id:
            continue
        if pair.verdict == "simultaneous":
            return "simultaneous"
        if pair.verdict == "sequential_only" and best is None:
            best = "sequential"
    return best


def _assemble(
    manifest: CatalogManifest, hw: HardwareInfo, hardware_source: str
) -> RecommendationReport:
    entries: list[EntryVerdict] = []
    for entry in manifest.entries:
        for role in entry.roles:
            base = evaluate_entry(entry, hw)
            entries.append(
                EntryVerdict(
                    model_id=base.model_id,
                    role=role,
                    verdict=base.verdict,
                    load_mode=base.load_mode,
                    estimated=base.estimated,
                    reasons=base.reasons,
                )
            )
    gens, embs = _pairable(manifest)
    pairs: list[PairVerdict] = []
    for gen in gens:
        for emb in embs:
            pair = evaluate_pair(gen, emb, hw)
            if pair.verdict == "infeasible" and "hardware_unknown" in pair.reasons:
                continue  # undecidable pair is omitted, never claimed
            pairs.append(pair)
    entries = [
        (
            EntryVerdict(
                model_id=v.model_id,
                role=v.role,
                verdict=v.verdict,
                load_mode=_load_mode_for(v.model_id, tuple(pairs)),
                estimated=v.estimated,
                reasons=v.reasons,
            )
            if v.role == "generation"
            else v
        )
        for v in entries
    ]
    return RecommendationReport(
        hardware_source=hardware_source,
        entries=tuple(entries),
        pairs=tuple(pairs),
    )


def recommend(
    manifest: CatalogManifest,
    hw: HardwareInfo,
    *,
    installed_model_ids: frozenset[str] = frozenset(),
) -> RecommendationReport:
    """The detected-hardware report. ``installed_model_ids`` is reserved
    for UI cross-referencing (S6-07) and deliberately does NOT affect
    the math: a model already on disk still needs its memory budget to
    run, and the disk gate is about storing files, not running them."""
    del installed_model_ids  # reserved; see docstring
    return _assemble(manifest, hw, "detected")


def recommend_for_profile(
    manifest: CatalogManifest, profile: CatalogProfile
) -> RecommendationReport:
    """The same math on a synthetic profile (`MODEL_RUNTIME.md` §3.3):
    per-GPU VRAM is the profile's aggregate divided evenly (the shipped
    profiles are even banks: 8×36 GiB = 288 GB). Profiles carry no disk
    figure, so the disk gate is skipped by the known-data-only rule."""
    if profile.gpu_count > 0 and profile.vram_bytes > 0:
        per_gpu = profile.vram_bytes // profile.gpu_count
        gpus: tuple[GpuInfo, ...] = tuple(
            GpuInfo(vendor="unknown", name=None, vram_bytes=per_gpu)
            for _ in range(profile.gpu_count)
        )
    else:
        gpus = ()
    hw = HardwareInfo(
        os_name="unknown",
        os_version=None,
        cpu_model=None,
        cpu_cores_logical=None,
        ram_total_bytes=profile.ram_bytes if profile.ram_bytes > 0 else None,
        ram_available_bytes=None,
        gpus=gpus,
        model_dir_free_bytes=None,
        detected_at=datetime.now(tz=UTC),
    )
    return _assemble(manifest, hw, f"profile:{profile.profile_id}")
