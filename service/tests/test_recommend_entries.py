"""S6-06: per-entry verdicts on synthetic hardware fixtures.

Every rule in the normative §5 entry math has a named test here, on
in-memory fixtures only (PD-026: deterministic, no hardware dependence
in CI). Exact byte boundaries pin the integer-arithmetic reading of the
fractions (0.7/0.9/1.5 as exact integer forms — see `recommend.py`).
"""

from __future__ import annotations

from typing import Any

import pytest

from controlgauge_service.hardware.models import GpuInfo
from controlgauge_service.model_catalog.recommend import REASON_CODES, evaluate_entry

from .recommend_fixtures import GIB, make_entry, make_gpu, make_hw

#: 10,000 bytes of RAM -> exactly 7,000 usable (×0.7 exact integer form).
_TINY_RAM = 10_000
_TINY_BUDGET = 7_000


# --- CPU path -----------------------------------------------------------------------


def test_cpu_fit_with_curator_recommendation_is_recommended() -> None:
    verdict = evaluate_entry(make_entry(), make_hw(ram=16 * GIB, free=100 * GIB))
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ()
    assert verdict.load_mode is None
    assert verdict.estimated.total_bytes == 1 * GIB


def test_exact_budget_boundary_is_feasible_but_not_recommended() -> None:
    """total == budget passes; total×1.5 does not -> plain `feasible`."""
    verdict = evaluate_entry(make_entry(weights=(_TINY_BUDGET,)), make_hw(ram=_TINY_RAM))
    assert verdict.verdict == "feasible"
    assert verdict.reasons == ()


def test_recommended_boundary_is_exact() -> None:
    """total×3 ≤ 2×budget is the exact ×1.5 form: 4,666 passes, 4,667
    does not."""
    fits = evaluate_entry(make_entry(weights=(4_666,)), make_hw(ram=_TINY_RAM))
    assert fits.verdict == "recommended"
    tight = evaluate_entry(make_entry(weights=(4_667,)), make_hw(ram=_TINY_RAM))
    assert tight.verdict == "feasible"


def test_one_byte_over_budget_is_infeasible() -> None:
    verdict = evaluate_entry(make_entry(weights=(_TINY_BUDGET + 1,)), make_hw(ram=_TINY_RAM))
    assert verdict.verdict == "infeasible"
    assert verdict.reasons == ("insufficient_ram",)


def test_unknown_ram_is_unknown_and_never_infeasible() -> None:
    """No GPUs and no RAM figure: the honest answer is `unknown`
    (`hardware_unknown`), never `infeasible`."""
    verdict = evaluate_entry(make_entry(weights=(100 * GIB,)), make_hw(ram=None))
    assert verdict.verdict == "unknown"
    assert verdict.reasons == ("hardware_unknown",)


def test_empty_gpu_list_uses_the_cpu_path() -> None:
    """The dev-box shape: `gpus: []` is a valid result — the CPU path
    decides, with no GPU reason codes."""
    verdict = evaluate_entry(make_entry(), make_hw(ram=16 * GIB, gpus=()))
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ()


# --- disk gate -----------------------------------------------------------------------


def test_disk_gate_refuses_regardless_of_memory() -> None:
    """`total + 1 GiB ≤ free` else infeasible — even with limitless
    RAM."""
    entry = make_entry(weights=(10 * GIB,))
    verdict = evaluate_entry(entry, make_hw(ram=1024 * GIB, free=10 * GIB + GIB - 1))
    assert verdict.verdict == "infeasible"
    assert verdict.reasons == ("insufficient_disk",)


def test_disk_gate_boundary_passes_exactly() -> None:
    entry = make_entry(weights=(10 * GIB,))
    verdict = evaluate_entry(entry, make_hw(ram=1024 * GIB, free=10 * GIB + GIB))
    assert verdict.verdict == "recommended"


def test_disk_gate_is_skipped_when_free_bytes_unknown() -> None:
    """An unknown disk figure can never fabricate a refusal (and is what
    keeps synthetic profiles computable)."""
    verdict = evaluate_entry(make_entry(weights=(10 * GIB,)), make_hw(ram=32 * GIB, free=None))
    assert verdict.verdict == "recommended"


# --- curator gate ----------------------------------------------------------------------


def test_feasible_but_curator_withheld_is_not_recommended() -> None:
    """The no-ceiling honesty rule: a fit on `recommended: false` is
    `feasible` with `curator_withheld`, never auto-promoted."""
    entry = make_entry(recommended=False)
    verdict = evaluate_entry(entry, make_hw(ram=16 * GIB))
    assert verdict.verdict == "feasible"
    assert verdict.reasons == ("curator_withheld",)


def test_curator_gate_applies_on_the_gpu_path_too() -> None:
    entry = make_entry(recommended=False)
    verdict = evaluate_entry(entry, make_hw(ram=None, gpus=(make_gpu(24 * GIB),)))
    assert verdict.verdict == "feasible"
    assert verdict.reasons == ("curator_withheld",)


# --- GPU path --------------------------------------------------------------------------


def test_gpu_single_fits_within_usable_vram() -> None:
    """24 GiB reported × 0.9 = 21.6 GiB usable; a 12 GiB total fits with
    the ×1.5 headroom (18 ≤ 21.6)."""
    verdict = evaluate_entry(
        make_entry(weights=(12 * GIB,)), make_hw(ram=None, gpus=(make_gpu(24 * GIB),))
    )
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ()


def test_gpu_fits_without_headroom_is_plain_feasible() -> None:
    """20 GiB on the same 21.6 GiB usable budget: feasible, but the ×1.5
    headroom (30 GiB) does not fit — never auto-promoted."""
    verdict = evaluate_entry(
        make_entry(weights=(20 * GIB,)), make_hw(ram=None, gpus=(make_gpu(24 * GIB),))
    )
    assert verdict.verdict == "feasible"
    assert verdict.reasons == ()


def test_gpu_exceeded_falls_back_to_the_cpu_verdict() -> None:
    """GPU too small but RAM fits comfortably: the verdict is the CPU
    one (with ×1.5 headroom: 15 GiB ≤ 44.8 GiB), with both GPU failure
    codes recorded (one GPU: aggregate and share bounds coincide)."""
    verdict = evaluate_entry(
        make_entry(weights=(10 * GIB,)),
        make_hw(ram=64 * GIB, gpus=(make_gpu(8 * GIB),)),
    )
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ("insufficient_vram", "exceeds_per_gpu_share")


def test_gpu_over_everywhere_is_infeasible() -> None:
    """Neither the GPU nor the RAM budget holds it. One GPU means the
    aggregate and per-GPU bounds coincide, so both GPU codes fire before
    the CPU one."""
    verdict = evaluate_entry(
        make_entry(weights=(100 * GIB,)),
        make_hw(ram=16 * GIB, gpus=(make_gpu(8 * GIB),)),
    )
    assert verdict.verdict == "infeasible"
    assert verdict.reasons == (
        "insufficient_vram",
        "exceeds_per_gpu_share",
        "insufficient_ram",
    )


def test_gpu_multi_even_bank_aggregate_and_share_both_pass() -> None:
    """8×36 GiB (the enterprise shape): a 150 GiB total is under the
    259.2 GiB aggregate AND the per-GPU share, with ×1.5 headroom
    (225 ≤ 259.2)."""
    gpus = tuple(make_gpu(36 * GIB) for _ in range(8))
    verdict = evaluate_entry(make_entry(weights=(150 * GIB,)), make_hw(ram=None, gpus=gpus))
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ()


def test_gpu_aggregate_pass_with_straggler_share_fails() -> None:
    """The "NOT one contiguous pool" case (PD-026): 7×36 GiB + one 8 GiB
    straggler. A 60 GiB total is under the 234 GiB aggregate but needs
    7.5 GiB per GPU — over the straggler's 7.2 GiB usable share — and
    the CPU path decides."""
    gpus = tuple(make_gpu(36 * GIB) for _ in range(7)) + (make_gpu(8 * GIB),)
    verdict = evaluate_entry(make_entry(weights=(60 * GIB,)), make_hw(ram=96 * GIB, gpus=gpus))
    assert verdict.verdict == "feasible"  # 60 GiB fits 67.2 GiB; ×1.5 does not
    assert verdict.reasons == ("exceeds_per_gpu_share",)


def test_gpu_share_boundary_is_exact() -> None:
    """Two 10 GiB GPUs: 18 GiB usable total. 12 GiB fits with ×1.5
    headroom (18 ≤ 18); 18 GiB sits exactly on the feasible boundary
    (headroom fails); one byte over breaks BOTH bounds (equal GPUs make
    the aggregate and share bounds coincide) and the CPU decides."""
    gpus = (make_gpu(10 * GIB), make_gpu(10 * GIB))
    with_headroom = evaluate_entry(make_entry(weights=(12 * GIB,)), make_hw(ram=None, gpus=gpus))
    assert with_headroom.verdict == "recommended"
    boundary = evaluate_entry(make_entry(weights=(18 * GIB,)), make_hw(ram=None, gpus=gpus))
    assert boundary.verdict == "feasible"
    over = evaluate_entry(make_entry(weights=(18 * GIB + 1,)), make_hw(ram=64 * GIB, gpus=gpus))
    assert over.verdict == "recommended"  # CPU fallback: 27 GiB ≤ 44.8 GiB
    assert over.reasons == ("insufficient_vram", "exceeds_per_gpu_share")


def test_gpu_fit_decides_without_any_ram_figure() -> None:
    """The GPU path needs no RAM: a fit there is a verdict even when
    `ram_total_bytes` is unknown (RAM becomes a needed input only when
    the CPU path must decide)."""
    verdict = evaluate_entry(
        make_entry(weights=(1 * GIB,)), make_hw(ram=None, gpus=(make_gpu(24 * GIB),))
    )
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ()


def test_gpu_with_unknown_vram_is_excluded_never_guessed() -> None:
    """A detected GPU whose VRAM the probe could not determine is not a
    budget input; the CPU path decides instead."""
    verdict = evaluate_entry(
        make_entry(),
        make_hw(ram=16 * GIB, gpus=(GpuInfo(vendor="nvidia", name=None, vram_bytes=None),)),
    )
    assert verdict.verdict == "recommended"
    assert verdict.reasons == ()


# --- shape ------------------------------------------------------------------------------


def test_verdict_and_reason_vocabularies_are_closed() -> None:
    from controlgauge_service.model_catalog.recommend import (
        ENTRY_VERDICTS,
        LOAD_MODES,
        REASON_CODES,
    )

    assert ENTRY_VERDICTS == ("recommended", "feasible", "infeasible", "unknown")
    assert LOAD_MODES == ("simultaneous", "sequential")
    assert REASON_CODES == (
        "insufficient_ram",
        "insufficient_vram",
        "exceeds_per_gpu_share",
        "insufficient_disk",
        "hardware_unknown",
        "curator_withheld",
    )


@pytest.mark.parametrize(
    ("kwargs", "hw_kwargs"),
    [
        ({}, {"ram": 16 * GIB}),
        ({"weights": (100 * GIB,)}, {"ram": None}),
    ],
    ids=["cpu-fit", "ram-unknown"],
)
def test_entry_verdict_fields_are_always_present(
    kwargs: dict[str, Any], hw_kwargs: dict[str, Any]
) -> None:
    verdict = evaluate_entry(make_entry(**kwargs), make_hw(**hw_kwargs))
    assert verdict.model_id == "entry-a"
    assert verdict.role == "generation"
    assert verdict.verdict in ("recommended", "feasible", "infeasible", "unknown")
    assert all(reason in REASON_CODES for reason in verdict.reasons)
