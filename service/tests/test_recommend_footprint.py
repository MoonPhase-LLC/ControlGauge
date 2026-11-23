"""S6-06: the §5 footprint formula, pinned to exact integers.

`estimate_footprint` is pure arithmetic — these tests pin every term so
a formula drift is caught at the exact byte, not by a downstream verdict
flip. Synthetic entries only (`recommend_fixtures`); no hardware, no
manifest load.
"""

from __future__ import annotations

from controlgauge_service.model_catalog.recommend import estimate_footprint

from .recommend_fixtures import GIB, make_entry


def test_weights_are_the_sum_of_all_files() -> None:
    entry = make_entry(weights=(1 * GIB, 2 * GIB, 3 * GIB))
    estimated = estimate_footprint(entry)
    assert estimated.weights_bytes == 6 * GIB
    assert estimated.kv_cache_bytes == 0
    assert estimated.runtime_overhead_bytes == 0
    assert estimated.total_bytes == 6 * GIB


def test_kv_cache_uses_the_curated_context_by_default() -> None:
    entry = make_entry(kv_per_token=28_672, curated_context=8_192, overhead=0)
    estimated = estimate_footprint(entry)
    assert estimated.kv_cache_bytes == 28_672 * 8_192 == 234_881_024
    assert estimated.total_bytes == estimated.weights_bytes + 234_881_024


def test_kv_cache_explicit_context_overrides_the_curated_default() -> None:
    entry = make_entry(kv_per_token=28_672, curated_context=8_192)
    estimated = estimate_footprint(entry, context_tokens=100)
    assert estimated.kv_cache_bytes == 2_867_200


def test_runtime_overhead_is_part_of_the_total() -> None:
    entry = make_entry(overhead=644_245_094)
    estimated = estimate_footprint(entry)
    assert estimated.runtime_overhead_bytes == 644_245_094
    assert estimated.total_bytes == estimated.weights_bytes + 644_245_094


def test_embedding_entries_keep_zero_kv_by_the_role_rule() -> None:
    entry = make_entry("emb", roles=("embedding",), kv_per_token=999)
    estimated = estimate_footprint(entry)
    assert estimated.kv_cache_bytes == 0


def test_total_is_exactly_the_three_terms() -> None:
    entry = make_entry(weights=(3 * GIB,), kv_per_token=1_000, overhead=500)
    estimated = estimate_footprint(entry, context_tokens=10)
    assert estimated.total_bytes == 3 * GIB + 10_000 + 500
